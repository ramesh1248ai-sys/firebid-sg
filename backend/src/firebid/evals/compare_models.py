"""`compare-models`: the evidence required before a route's model chain changes.

project-context says a route's models change "only when `firebid-eval compare-models` on the
golden set shows equal or better quality, with cost and latency recorded, and the product
owner approves". This is that command.

It reruns one suite once per candidate model, by overriding the route's chain in a *copy* of
the configuration. Two things it refuses to do:

* **Run a candidate not approved for the route's data class.** The gateway would refuse the
  call anyway; refusing here means the report says why instead of showing an empty column.
* **Rank on quality alone.** Cost and latency sit beside the metrics, because a model that is
  half a percent better and four times dearer is a decision, not an answer.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from firebid.ai_gateway.config import LlmConfig
from firebid.ai_gateway.metering import cost_of
from firebid.ai_gateway.types import Usage
from firebid.evals.prediction import Predictor
from firebid.evals.runner import SuiteResult, run_suite
from firebid.evals.schema import GoldenSet


class CandidateRefused(Exception):
    """A candidate model cannot serve this route, and the report should say so."""


@dataclass
class Candidate:
    """One model's showing on the suite."""

    model: str
    provider: str
    result: SuiteResult | None = None
    refused_because: str | None = None
    cost_sgd: Decimal = Decimal(0)
    latency_p50_ms: int = 0
    latency_p95_ms: int = 0

    @property
    def ran(self) -> bool:
        return self.result is not None


@dataclass
class Comparison:
    route: str
    suite: str
    config_version: str
    candidates: list[Candidate] = field(default_factory=list)
    # The metric the ranking is on; quality only, with cost and latency shown beside it.
    ranked_on: str = "sprinkler_count_accuracy"

    def ranked(self) -> list[Candidate]:
        """Best first. Candidates that could not run come last, in name order."""
        from firebid.evals.metrics import HIGHER_IS_BETTER

        higher_is_better = HIGHER_IS_BETTER.get(self.ranked_on, True)

        def key(candidate: Candidate) -> tuple[int, float, str]:
            if candidate.result is None:
                return (1, 0.0, candidate.model)
            value = candidate.result.overall.get(self.ranked_on)
            if value is None:
                return (1, 0.0, candidate.model)
            return (0, -value if higher_is_better else value, candidate.model)

        return sorted(self.candidates, key=key)

    def best(self) -> Candidate | None:
        ordered = [candidate for candidate in self.ranked() if candidate.ran]
        return ordered[0] if ordered else None


def check_candidate(config: LlmConfig, route_name: str, model_name: str) -> None:
    """Raise `CandidateRefused` if this model cannot serve this route."""
    route = config.route(route_name)
    model = config.models.get(model_name)
    if model is None:
        raise CandidateRefused(f"'{model_name}' is not a model in llm.yaml")

    provider = config.providers.get(model.provider)
    if provider is None:
        raise CandidateRefused(f"model '{model_name}' names an unknown provider")

    if not provider.approves(route.data_class):
        approved = [str(value) for value in provider.approved_data_classes] or "nothing"
        raise CandidateRefused(
            f"provider '{model.provider}' is approved for {approved}, and route "
            f"'{route_name}' sends '{route.data_class}' data"
        )

    missing = sorted(
        str(capability) for capability in set(route.requires) - set(model.capabilities)
    )
    if missing and not route.allow_emulation:
        raise CandidateRefused(
            f"model '{model_name}' lacks {', '.join(missing)}, which route '{route_name}' requires"
        )


def with_route_model(config: LlmConfig, route_name: str, model_name: str) -> LlmConfig:
    """A copy of the configuration with one route pinned to one model.

    A copy, not a mutation: a comparison must not leave the running configuration changed.
    """
    payload = config.model_dump()
    payload["routes"][route_name]["models"] = [model_name]
    return LlmConfig.model_validate(payload)


def compare_models(
    config: LlmConfig,
    golden_set: GoldenSet,
    route_name: str,
    model_names: list[str],
    predictor_for: Callable[[LlmConfig, str], Predictor],
    *,
    ranked_on: str = "sprinkler_count_accuracy",
) -> Comparison:
    """Run the suite once per candidate.

    `predictor_for` is called with the pinned configuration and the model name, and returns
    the `Predictor` to score. Later steps pass one that actually calls the gateway; the tests
    pass one whose accuracy is scripted per model.
    """
    comparison = Comparison(
        route=route_name,
        suite=golden_set.name,
        config_version=config.config_hash,
        ranked_on=ranked_on,
    )

    for model_name in model_names:
        model = config.models.get(model_name)
        candidate = Candidate(model=model_name, provider=model.provider if model else "(unknown)")

        try:
            check_candidate(config, route_name, model_name)
        except CandidateRefused as refusal:
            candidate.refused_because = str(refusal)
            comparison.candidates.append(candidate)
            continue

        pinned = with_route_model(config, route_name, model_name)
        predictor = predictor_for(pinned, model_name)

        latencies: list[int] = []
        started = time.monotonic()
        result = run_suite(golden_set, predictor, suite=golden_set.name)
        latencies.append(int((time.monotonic() - started) * 1000))

        result.route = route_name
        result.model = model_name
        result.config_version = pinned.config_hash
        candidate.result = result

        # Cost is what this suite would have cost on this model, from the configured prices.
        usage = getattr(predictor, "usage", None)
        if model is not None and isinstance(usage, Usage):
            candidate.cost_sgd = cost_of(model, usage)
            result.cost_sgd = str(candidate.cost_sgd)

        measured = getattr(predictor, "latencies_ms", None) or latencies
        candidate.latency_p50_ms = _percentile(measured, 50)
        candidate.latency_p95_ms = _percentile(measured, 95)
        result.latency_p50_ms = candidate.latency_p50_ms
        result.latency_p95_ms = candidate.latency_p95_ms
        result.prompt_versions = list(getattr(predictor, "prompt_versions", []) or [])

        comparison.candidates.append(candidate)

    return comparison


def _cell(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percentile / 100) * len(ordered)) - 1))
    return ordered[index]


def comparison_report(comparison: Comparison) -> str:
    """Side by side, best first, with cost and latency beside the quality."""
    from firebid.evals.metrics import HIGHER_IS_BETTER

    metrics = list(HIGHER_IS_BETTER)
    lines = [
        f"# Model comparison — route `{comparison.route}`",
        "",
        f"- Suite: {comparison.suite}",
        f"- Configuration version: `{comparison.config_version}`",
        f"- Ranked on: {comparison.ranked_on}",
        "",
        "| Model | Provider | " + " | ".join(metrics) + " | Cost (SGD) | p50 ms | p95 ms |",
        "|---" * (len(metrics) + 5) + "|",
    ]

    for candidate in comparison.ranked():
        if candidate.result is None:
            lines.append(
                f"| {candidate.model} | {candidate.provider} | "
                + " | ".join(["—"] * len(metrics))
                + " | — | — | — |"
            )
            continue
        values = " | ".join(_cell(candidate.result.overall.get(metric)) for metric in metrics)
        lines.append(
            f"| {candidate.model} | {candidate.provider} | {values} | "
            f"{candidate.cost_sgd} | {candidate.latency_p50_ms} | {candidate.latency_p95_ms} |"
        )

    refused = [c for c in comparison.candidates if c.refused_because]
    if refused:
        lines += ["", "## Not run", ""]
        lines += [f"- **{c.model}**: {c.refused_because}" for c in refused]

    best = comparison.best()
    if best is not None:
        lines += [
            "",
            f"Best on {comparison.ranked_on}: **{best.model}** ({best.provider}).",
            "",
            "> Quality alone does not decide this. A model half a percent better and four "
            "times dearer is a decision for the product owner, not a conclusion.",
        ]
    return "\n".join(lines) + "\n"
