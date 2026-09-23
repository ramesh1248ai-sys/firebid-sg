"""Where credentials come from, and that the documentation matches the code.

Two failures this prevents. A key placed in `backend/.env` that the gateway never reads,
because the two disagreed about the file. And an example file naming variables nothing
resolves, so filling it in does nothing and the mistake is invisible.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from firebid import env
from firebid.ai_gateway.config import ProviderConfig, load_config

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the env file at a temporary one, so a developer's real .env cannot affect this."""
    env._file_values.cache_clear()
    monkeypatch.setenv("FIREBID_ENV_FILE", str(tmp_path / ".env"))


def write_env(tmp_path: Path, text: str) -> None:
    (tmp_path / ".env").write_text(text, encoding="utf-8")
    env._file_values.cache_clear()


@pytest.mark.req("NFR-06")
class TestWhereACredentialComesFrom:
    def test_an_environment_variable_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOME_PROVIDER_KEY", "from-environment")
        provider = ProviderConfig(kind="openai", credentials="env://SOME_PROVIDER_KEY")
        assert provider.resolve_credential() == "from-environment"

    def test_the_env_file_is_read_when_the_variable_is_not_set(self, tmp_path: Path) -> None:
        """The point of the change: a key in backend/.env actually reaches the gateway."""
        write_env(tmp_path, "SOME_PROVIDER_KEY=from-the-file\n")
        provider = ProviderConfig(kind="openai", credentials="env://SOME_PROVIDER_KEY")
        assert provider.resolve_credential() == "from-the-file"

    def test_the_environment_wins_over_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stale .env in a working copy must never override what a deployment set."""
        write_env(tmp_path, "SOME_PROVIDER_KEY=from-the-file\n")
        monkeypatch.setenv("SOME_PROVIDER_KEY", "from-environment")
        provider = ProviderConfig(kind="openai", credentials="env://SOME_PROVIDER_KEY")
        assert provider.resolve_credential() == "from-environment"

    def test_an_empty_value_counts_as_absent(self, tmp_path: Path) -> None:
        """An example file copied without filling it in should read as "no key", not "".."""
        write_env(tmp_path, "SOME_PROVIDER_KEY=\n")
        provider = ProviderConfig(kind="openai", credentials="env://SOME_PROVIDER_KEY")
        assert provider.resolve_credential() is None

    def test_a_secret_reference_maps_to_its_variable(self, tmp_path: Path) -> None:
        write_env(tmp_path, "FIREBID_SECRET_LLM_OPENAI=from-the-vault-shim\n")
        provider = ProviderConfig(kind="openai", credentials="secret://llm/openai")
        assert provider.resolve_credential() == "from-the-vault-shim"

    def test_no_credentials_configured_is_not_an_error(self) -> None:
        assert ProviderConfig(kind="fake").resolve_credential() is None

    def test_an_edit_to_the_file_is_picked_up(self, tmp_path: Path) -> None:
        """The cache is keyed on the file's timestamp, not held for the process's life."""
        write_env(tmp_path, "SOME_PROVIDER_KEY=first\n")
        provider = ProviderConfig(kind="openai", credentials="env://SOME_PROVIDER_KEY")
        assert provider.resolve_credential() == "first"

        write_env(tmp_path, "SOME_PROVIDER_KEY=second\n")
        assert provider.resolve_credential() == "second"


def example_names(path: Path) -> set[str]:
    """The variable names an example file offers to set."""
    return {
        match.group(1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip()))
    }


def configured_names() -> set[str]:
    """The variable names the shipped llm.yaml actually resolves."""
    names: set[str] = set()
    for provider in load_config().providers.values():
        if provider.credentials is None:
            continue
        match = re.match(r"^(env|secret)://(.+)$", provider.credentials)
        if match is None:
            continue
        raw = match.group(2)
        names.add(
            raw
            if match.group(1) == "env"
            else "FIREBID_SECRET_" + raw.replace("/", "_").replace("-", "_").upper()
        )
    return names


@pytest.mark.req("NFR-06")
class TestTheDocumentationMatchesTheCode:
    def test_the_backend_example_offers_every_variable_llm_yaml_reads(self) -> None:
        """Otherwise someone fills in the example and nothing happens."""
        missing = configured_names() - example_names(BACKEND / ".env.example")
        assert not missing, (
            f"backend/.env.example does not mention {sorted(missing)}, which "
            "backend/config/llm.yaml reads"
        )

    def test_the_backend_example_offers_no_variable_nothing_reads(self) -> None:
        """A name nobody reads is worse than no name: it looks like it works."""
        offered = example_names(BACKEND / ".env.example")
        settings_names = {name for name in offered if name.startswith("FIREBID_")}
        unread = offered - settings_names - configured_names()
        assert not unread, f"backend/.env.example offers {sorted(unread)}, which nothing reads"

    def test_every_firebid_variable_in_the_example_is_a_real_setting(self) -> None:
        from firebid.settings import Settings

        fields = {f"FIREBID_{name.upper()}" for name in Settings.model_fields}
        offered = {
            name
            for name in example_names(BACKEND / ".env.example")
            if name.startswith("FIREBID_") and not name.startswith("FIREBID_SECRET_")
        }
        assert offered <= fields, f"not settings: {sorted(offered - fields)}"

    def test_the_stack_example_offers_the_keys_compose_interpolates(self) -> None:
        compose = (BACKEND.parent / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        interpolated = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)(?::-[^}]*)?\}", compose))
        provider_keys = {name for name in interpolated if name.endswith("_API_KEY")}
        offered = example_names(BACKEND.parent / "infra" / ".env.example")
        assert provider_keys <= offered, (
            f"infra/.env.example does not mention {sorted(provider_keys - offered)}"
        )


@pytest.mark.req("NFR-06")
def test_the_env_file_is_found_from_the_package_not_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A .env that works from backend/ but not from the repository root wastes an afternoon."""
    monkeypatch.delenv("FIREBID_ENV_FILE", raising=False)
    monkeypatch.chdir(os.sep)
    assert env.env_file() == BACKEND / ".env"


@pytest.mark.req("NFR-06")
def test_no_real_env_file_is_committed() -> None:
    """Only the examples belong in git."""
    assert not (BACKEND / ".env").exists() or ".env" in (
        (BACKEND.parent / ".gitignore").read_text(encoding="utf-8")
    )
