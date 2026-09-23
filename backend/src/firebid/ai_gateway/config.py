"""`backend/config/llm.yaml`: the whole of which provider serves which task.

Changing a route's provider, its model chain, its reasoning level or which data classes a
provider may receive is an edit to this file. No code changes, and no deployment of new code.

The schema is validated at startup and the file is versioned by content hash, so every call
can record which configuration produced it (NFR-11).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from firebid.ai_gateway.errors import ConfigError
from firebid.ai_gateway.types import Capability, DataClass, ReasoningLevel

# A credential is never written in the file: it is named, and resolved from the environment.
SECRET_REFERENCE = re.compile(r"^(env|secret)://(?P<name>[A-Za-z0-9_./-]+)$")

ProviderKind = Literal["anthropic", "openai", "google", "openai_compatible", "fake"]
Platform = Literal["api", "bedrock", "vertex", "foundry", "azure"]


class Price(BaseModel):
    """Effective-dated, so a price change does not rewrite history."""

    model_config = ConfigDict(extra="forbid")

    effective_from: date
    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    cached_input_per_mtok: float | None = Field(default=None, ge=0)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ProviderKind
    platform: Platform = "api"
    enabled: bool = True
    credentials: str | None = None
    base_url: str | None = None
    region: str | None = None
    project_id: str | None = None
    # The decision that matters: what this provider is allowed to be sent (decision D2).
    approved_data_classes: list[DataClass] = Field(default_factory=list)
    requests_per_minute: int | None = Field(default=None, gt=0)
    # Provider-level knobs (Azure's api_version, for instance), so a provider's own
    # requirements stay in configuration rather than in an adapter.
    options: dict[str, Any] = Field(default_factory=dict)
    # Notes carried for the audit trail; the gateway does not interpret them.
    retention: str | None = None
    no_training: bool | None = None

    @model_validator(mode="after")
    def _check_credentials(self) -> ProviderConfig:
        if self.credentials is not None and not SECRET_REFERENCE.match(self.credentials):
            raise ValueError(
                f"credentials must look like env://NAME or secret://name, not {self.credentials!r}"
            )
        return self

    def approves(self, data_class: DataClass) -> bool:
        return data_class in self.approved_data_classes

    def resolve_credential(self) -> str | None:
        """Read the named secret from the environment. Missing is not fatal here: an adapter
        that needs it fails when it is constructed, naming the variable."""
        if self.credentials is None:
            return None
        match = SECRET_REFERENCE.match(self.credentials)
        if match is None:  # unreachable: the validator rejects anything else
            return None
        name = match.group("name")
        # secret://llm/anthropic reads FIREBID_SECRET_LLM_ANTHROPIC in this deployment.
        env_name = (
            name
            if match.group(1) == "env"
            else (
                "FIREBID_SECRET_"
                + name.replace("/", "_").replace("-", "_").replace(".", "_").upper()
            )
        )
        return os.environ.get(env_name)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    # The provider's own identifier. Kept separate from our key so a model can be renamed
    # upstream without touching every route.
    model_id: str
    capabilities: list[Capability] = Field(default_factory=list)
    context_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    prices: list[Price] = Field(default_factory=list)
    # The provider parameter that carries reasoning depth, where the model has one
    # (`reasoning_effort` on OpenAI, `thinking_level` on Gemini). Left unset, the route's
    # reasoning level is simply not sent, which is right for models that reject it.
    reasoning_parameter: str | None = None
    # Anything else this model needs passed through. An escape hatch so a provider's new knob
    # is a configuration edit rather than an adapter change.
    options: dict[str, Any] = Field(default_factory=dict)

    def price_on(self, when: date) -> Price | None:
        applicable = [price for price in self.prices if price.effective_from <= when]
        return max(applicable, key=lambda price: price.effective_from) if applicable else None


class RouteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requires: list[Capability] = Field(default_factory=list)
    data_class: DataClass
    # Primary first, then fallbacks in order.
    models: Annotated[list[str], Field(min_length=1)]
    reasoning: ReasoningLevel = ReasoningLevel.MEDIUM
    max_output_tokens: int = Field(default=16_000, gt=0)
    prompt: str | None = None
    # Allow the adapter to stand in for a capability the model lacks (P0-04 sub-part E).
    allow_emulation: bool = False


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelConfig]
    routes: dict[str, RouteConfig]
    # Set from the file's bytes, not from the parsed content, so formatting changes are visible.
    config_hash: str = ""

    @model_validator(mode="after")
    def _check_references(self) -> LlmConfig:
        problems: list[str] = []

        for name, defined_model in self.models.items():
            if defined_model.provider not in self.providers:
                problems.append(
                    f"model '{name}' names provider '{defined_model.provider}', "
                    "which is not defined"
                )

        for route_name, route in self.routes.items():
            for model_name in route.models:
                model = self.models.get(model_name)
                if model is None:
                    problems.append(
                        f"route '{route_name}' names model '{model_name}', which is not defined"
                    )
                    continue
                provider = self.providers.get(model.provider)
                if provider is None:
                    continue  # already reported above

                missing = sorted(set(route.requires) - set(model.capabilities))
                if missing and not route.allow_emulation:
                    problems.append(
                        f"route '{route_name}' requires {missing} but model '{model_name}' "
                        f"does not have {'them' if len(missing) > 1 else 'it'}"
                    )
                if not provider.approves(route.data_class):
                    problems.append(
                        f"route '{route_name}' sends '{route.data_class}' data, but provider "
                        f"'{model.provider}' (via model '{model_name}') is approved only for "
                        f"{[str(c) for c in provider.approved_data_classes] or 'nothing'}"
                    )

        if problems:
            raise ValueError("\n  - " + "\n  - ".join(problems))
        return self

    def enabled_chain(self, route_name: str) -> list[tuple[str, ModelConfig, ProviderConfig]]:
        """The route's models in order, skipping providers that are switched off."""
        route = self.route(route_name)
        chain: list[tuple[str, ModelConfig, ProviderConfig]] = []
        for model_name in route.models:
            model = self.models[model_name]
            provider = self.providers[model.provider]
            if provider.enabled:
                chain.append((model_name, model, provider))
        return chain

    def route(self, name: str) -> RouteConfig:
        try:
            return self.routes[name]
        except KeyError:
            known = ", ".join(sorted(self.routes)) or "none"
            raise ConfigError(f"unknown route '{name}'; configured routes: {known}") from None


def _default_path() -> Path:
    override = os.environ.get("FIREBID_LLM_CONFIG")
    if override:
        return Path(override)
    # backend/src/firebid/ai_gateway/config.py -> backend/config/llm.yaml
    return Path(__file__).resolve().parents[3] / "config" / "llm.yaml"


def load_config(path: Path | None = None, overlay: Mapping[str, Any] | None = None) -> LlmConfig:
    """Read and validate the configuration, failing loudly and specifically.

    A per-environment overlay is merged over the base file, so staging and production differ by
    a small file rather than by a fork of the whole configuration.
    """
    path = path or _default_path()
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"cannot read LLM configuration at {path}: {error}") from error

    try:
        data = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error

    if overlay:
        data = _merge(data, overlay)

    data["config_hash"] = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]

    try:
        return LlmConfig.model_validate(data)
    except Exception as error:  # pydantic ValidationError, or our ValueError from the validator
        raise ConfigError(f"{path} is not a usable LLM configuration:{error}") from error


def _merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            merged[key] = _merge(existing, value)
        else:
            merged[key] = value
    return merged


@lru_cache
def get_config() -> LlmConfig:
    return load_config()
