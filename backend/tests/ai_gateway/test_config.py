"""Configuration is the whole point of the gateway, so it is checked hard at startup.

A configuration that would send tender documents to an unapproved provider, or that names a
model which cannot do what the route needs, must fail loudly before any call is made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from firebid.ai_gateway.config import load_config
from firebid.ai_gateway.errors import ConfigError
from firebid.ai_gateway.types import Capability, DataClass

pytestmark = pytest.mark.req("NFR-05")

BASE = """
version: 1
providers:
  anthropic:
    kind: anthropic
    credentials: env://ANTHROPIC_API_KEY
    approved_data_classes: [internal, confidential, commercial]
  narrow:
    kind: openai
    credentials: env://OPENAI_API_KEY
    approved_data_classes: [internal]
models:
  claude-opus-5:
    provider: anthropic
    model_id: claude-opus-5
    capabilities: [vision, structured_output, tools]
  narrow-model:
    provider: narrow
    model_id: narrow-1
    capabilities: [structured_output]
routes:
  reader:
    requires: [vision, structured_output]
    data_class: confidential
    models: [claude-opus-5]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "llm.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TestAValidConfiguration:
    def test_loads_and_exposes_the_chain(self, tmp_path: Path) -> None:
        config = load_config(write(tmp_path, BASE))
        chain = config.enabled_chain("reader")
        assert [name for name, _, _ in chain] == ["claude-opus-5"]
        assert config.route("reader").data_class is DataClass.CONFIDENTIAL

    def test_is_versioned_by_content_hash(self, tmp_path: Path) -> None:
        """Every call records this, so an answer can be tied to the configuration that made it."""
        first = load_config(write(tmp_path, BASE)).config_hash
        again = load_config(write(tmp_path, BASE)).config_hash
        changed = load_config(write(tmp_path, BASE + "\n# a comment\n")).config_hash
        assert first == again
        assert first != changed

    def test_an_unknown_route_names_the_ones_that_exist(self, tmp_path: Path) -> None:
        config = load_config(write(tmp_path, BASE))
        with pytest.raises(ConfigError, match="reader"):
            config.route("no_such_route")


class TestStartupRefusesBadConfiguration:
    def test_a_route_whose_model_lacks_a_required_capability(self, tmp_path: Path) -> None:
        broken = BASE.replace("    models: [claude-opus-5]", "    models: [narrow-model]").replace(
            "    data_class: confidential", "    data_class: internal"
        )
        with pytest.raises(ConfigError, match="vision"):
            load_config(write(tmp_path, broken))

    def test_a_chain_containing_an_unapproved_provider(self, tmp_path: Path) -> None:
        """The headline rule: confidential data cannot be routed to an internal-only provider."""
        broken = BASE.replace(
            "    models: [claude-opus-5]", "    models: [claude-opus-5, narrow-model]"
        ).replace(
            "    capabilities: [structured_output]",
            "    capabilities: [vision, structured_output]",
        )
        with pytest.raises(ConfigError, match="approved only for"):
            load_config(write(tmp_path, broken))

    def test_a_model_naming_a_provider_that_does_not_exist(self, tmp_path: Path) -> None:
        broken = BASE.replace(
            "    provider: narrow\n    model_id: narrow-1",
            "    provider: ghost\n    model_id: narrow-1",
        )
        with pytest.raises(ConfigError, match="ghost"):
            load_config(write(tmp_path, broken))

    def test_a_route_naming_a_model_that_does_not_exist(self, tmp_path: Path) -> None:
        broken = BASE.replace("    models: [claude-opus-5]", "    models: [imaginary]")
        with pytest.raises(ConfigError, match="imaginary"):
            load_config(write(tmp_path, broken))

    def test_a_credential_written_inline_rather_than_referenced(self, tmp_path: Path) -> None:
        """A key in the file would be a key in git history."""
        broken = BASE.replace(
            "credentials: env://ANTHROPIC_API_KEY", "credentials: sk-secret-value"
        )
        with pytest.raises(ConfigError, match="env://"):
            load_config(write(tmp_path, broken))

    def test_an_unknown_field_is_a_typo_not_an_extension(self, tmp_path: Path) -> None:
        broken = BASE.replace("    reasoning: medium", "").replace(
            "  reader:\n", "  reader:\n    resoning: low\n"
        )
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, broken))

    def test_a_missing_file_says_where_it_looked(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="cannot read"):
            load_config(tmp_path / "absent.yaml")


class TestOverlays:
    def test_approving_a_provider_for_more_data_is_a_configuration_edit(
        self, tmp_path: Path
    ) -> None:
        """Signing decision D2 widens a provider here, and nowhere else. No code changes."""
        config = load_config(
            write(tmp_path, BASE),
            overlay={
                "providers": {"narrow": {"approved_data_classes": ["internal", "confidential"]}}
            },
        )
        narrow = config.providers["narrow"]
        assert narrow.approves(DataClass.CONFIDENTIAL)
        # The rest of the provider survives the merge.
        assert narrow.credentials == "env://OPENAI_API_KEY"

    def test_an_overlay_that_breaks_a_rule_still_fails(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="approved only for"):
            load_config(
                write(tmp_path, BASE),
                overlay={
                    "providers": {"anthropic": {"approved_data_classes": ["internal"]}},
                    "routes": {
                        "reader": {
                            "data_class": "confidential",
                            "requires": ["vision", "structured_output"],
                            "models": ["claude-opus-5"],
                        }
                    },
                },
            )


@pytest.mark.req("FR-ADM-05")
def test_prices_are_effective_dated(tmp_path: Path) -> None:
    """Cost attribution has to use the price that applied when the call was made."""
    from datetime import date

    priced = BASE.replace(
        "    capabilities: [vision, structured_output, tools]",
        "    capabilities: [vision, structured_output, tools]\n"
        "    prices:\n"
        "      - {effective_from: 2026-01-01, input_per_mtok: 5.0, output_per_mtok: 25.0}\n"
        "      - {effective_from: 2026-06-01, input_per_mtok: 4.0, output_per_mtok: 20.0}",
    )
    model = load_config(write(tmp_path, priced)).models["claude-opus-5"]
    assert model.price_on(date(2026, 3, 1)).input_per_mtok == 5.0  # type: ignore[union-attr]
    assert model.price_on(date(2026, 9, 1)).input_per_mtok == 4.0  # type: ignore[union-attr]
    assert model.price_on(date(2025, 1, 1)) is None


def test_the_shipped_configuration_is_valid() -> None:
    """The file the application actually loads must itself pass every rule."""
    config = load_config()
    assert "title_block_read" in config.routes
    assert Capability.VISION in config.routes["title_block_read"].requires
    # Every provider named by a model exists, every route resolves: enforced by the validator,
    # asserted here so a bad edit fails in CI rather than at startup in production.
    for route_name in config.routes:
        assert config.enabled_chain(route_name) is not None
