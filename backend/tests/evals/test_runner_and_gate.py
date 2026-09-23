"""The runner, the regression gate and model comparison (FR-LRN-01, NFR-11).

The load-bearing tests are the two the prompt names: an injected regression must make
`compare` fail and restoring the predictor must make it pass, and `compare-models` must rank
two models of known different accuracy correctly while refusing one that is not approved for
the route's data class.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from firebid.ai_gateway.config import LlmConfig, load_config
from firebid.evals.cli import main
from firebid.evals.compare_models import (
    CandidateRefused,
    check_candidate,
    compare_models,
    comparison_report,
    with_route_model,
)
from firebid.evals.dummy import DummyPredictor
from firebid.evals.prediction import Predictor
from firebid.evals.runner import (
    Baseline,
    compare,
    markdown_report,
    regression_report,
    run_suite,
)
from firebid.evals.schema import GoldenSet
from firebid.evals.synthetic import generate_tender

pytestmark = pytest.mark.req("FR-LRN-01")


def _value(overall: dict[str, float | None], metric: str) -> float:
    """A metric that must be measurable for the test to mean anything."""
    value = overall.get(metric)
    assert value is not None, f"{metric} was not measured"
    return value


@pytest.fixture
def golden(tmp_path: Path) -> GoldenSet:
    """A small synthetic set, three tenders across two consultants."""
    tenders = [
        generate_tender(
            tmp_path / f"T{index}",
            tender_id=f"SYNTH-{index:03d}",
            consultant=f"Consultant {index % 2 + 1}",
            seed=index + 1,
            with_raster=index == 0,
        ).truth
        for index in range(3)
    ]
    return GoldenSet(name="synthetic", tenders=tuple(tenders))


class TestRunningASuite:
    def test_a_perfect_predictor_scores_at_the_top(self, golden: GoldenSet) -> None:
        result = run_suite(golden, DummyPredictor())
        assert result.overall["sprinkler_count_accuracy"] == pytest.approx(1.0)
        assert result.overall["missed_item_rate"] == pytest.approx(0.0)
        assert len(result.tenders) == 3

    def test_a_degraded_predictor_scores_lower(self, golden: GoldenSet) -> None:
        good = run_suite(golden, DummyPredictor()).overall
        poor = run_suite(golden, DummyPredictor(count_error=0.2)).overall
        assert _value(poor, "sprinkler_count_accuracy") < _value(good, "sprinkler_count_accuracy")
        assert _value(poor, "missed_item_rate") > _value(good, "missed_item_rate")

    def test_results_are_sliced_by_input_class(self, golden: GoldenSet) -> None:
        """An average hiding a bad score on scans is the number that gets someone hurt."""
        result = run_suite(golden, DummyPredictor())
        assert len(result.by_input_class) >= 1
        assert all(
            "sprinkler_count_accuracy" in values for values in result.by_input_class.values()
        )

    def test_results_are_sliced_by_consultant(self, golden: GoldenSet) -> None:
        result = run_suite(golden, DummyPredictor())
        assert set(result.by_consultant) == {"Consultant 1", "Consultant 2"}

    def test_the_same_predictor_twice_gives_the_same_scores(self, golden: GoldenSet) -> None:
        """A gate on a noisy measurement is a gate people switch off."""
        first = run_suite(golden, DummyPredictor(count_error=0.1, miss_rate=0.3))
        second = run_suite(golden, DummyPredictor(count_error=0.1, miss_rate=0.3))
        assert first.overall == second.overall

    def test_the_report_names_every_metric_and_its_target(self, golden: GoldenSet) -> None:
        report = markdown_report(run_suite(golden, DummyPredictor()))
        assert "sprinkler_count_accuracy" in report
        assert "By input class" in report
        assert "By consultant" in report
        # §14 targets are shown so a reader knows whether a number is good enough.
        assert "≥0.98" in report

    def test_the_report_says_synthetic_proves_the_pipeline_not_accuracy(
        self, golden: GoldenSet
    ) -> None:
        """Nobody should quote a synthetic number as an accuracy figure."""
        report = markdown_report(run_suite(golden, DummyPredictor()))
        assert "not real-world accuracy" in report
        assert "decision D3" in report

    def test_the_report_lists_what_could_not_be_measured(self, golden: GoldenSet) -> None:
        report = markdown_report(run_suite(golden, DummyPredictor()))
        assert "Not measured" in report, "sheets with no sprinklers should be declared"


class TestTheRegressionGate:
    def _baseline(self, golden: GoldenSet, path: Path) -> Baseline:
        """Accept a perfect run as the baseline, then read it back the way the CLI does."""
        result = run_suite(golden, DummyPredictor())
        Baseline.from_result(result, approver="Test Approver").write(path)
        return Baseline.read(path)

    def test_an_unchanged_predictor_passes(self, golden: GoldenSet, tmp_path: Path) -> None:
        baseline = self._baseline(golden, tmp_path / "baseline.json")
        assert compare(baseline, run_suite(golden, DummyPredictor())) == []

    def test_an_injected_regression_fails(self, golden: GoldenSet, tmp_path: Path) -> None:
        """The test the whole gate exists for."""
        baseline = self._baseline(golden, tmp_path / "baseline.json")
        regressions = compare(baseline, run_suite(golden, DummyPredictor(count_error=0.2)))

        assert regressions, "a 20% counting error must not pass the gate"
        names = {regression.metric for regression in regressions}
        assert "sprinkler_count_accuracy" in names
        assert "missed_item_rate" in names

    def test_restoring_the_predictor_passes_again(self, golden: GoldenSet, tmp_path: Path) -> None:
        baseline = self._baseline(golden, tmp_path / "baseline.json")
        assert compare(baseline, run_suite(golden, DummyPredictor(count_error=0.2)))
        assert compare(baseline, run_suite(golden, DummyPredictor())) == []

    def test_a_small_wobble_inside_tolerance_passes(
        self, golden: GoldenSet, tmp_path: Path
    ) -> None:
        """A gate that fires on noise is a gate someone disables."""
        baseline = self._baseline(golden, tmp_path / "baseline.json")
        tiny = run_suite(golden, DummyPredictor(count_error=0.001))
        assert compare(baseline, tiny) == []

    def test_an_improvement_is_not_a_regression(self, golden: GoldenSet, tmp_path: Path) -> None:
        poor = run_suite(golden, DummyPredictor(count_error=0.2))
        path = tmp_path / "b.json"
        Baseline.from_result(poor, approver="Test").write(path)
        assert compare(Baseline.read(path), run_suite(golden, DummyPredictor())) == []

    def test_a_metric_that_stops_being_measured_counts_as_a_regression(
        self, golden: GoldenSet, tmp_path: Path
    ) -> None:
        """Measuring nothing is not an improvement, and silence is how a suite rots."""
        baseline = self._baseline(golden, tmp_path / "baseline.json")
        result = run_suite(golden, DummyPredictor())
        result.overall["duplicate_detection_rate"] = None

        regressions = compare(baseline, result)
        assert any(r.metric == "duplicate_detection_rate" for r in regressions)
        assert "not measured" in regression_report(regressions)

    def test_the_baseline_records_who_accepted_it(self, golden: GoldenSet, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        Baseline.from_result(run_suite(golden, DummyPredictor()), "Ramesh R").write(path)
        assert Baseline.read(path).approver == "Ramesh R"
        assert Baseline.read(path).accepted_at


COMPARE_CONFIG = """
version: 1
providers:
  approved:
    kind: fake
    approved_data_classes: [internal, confidential]
  internal_only:
    kind: fake
    approved_data_classes: [internal]
models:
  good-model:
    provider: approved
    model_id: good-1
    capabilities: [vision, structured_output]
    prices:
      - {effective_from: 2026-01-01, input_per_mtok: 5.0, output_per_mtok: 25.0}
  weak-model:
    provider: approved
    model_id: weak-1
    capabilities: [vision, structured_output]
    prices:
      - {effective_from: 2026-01-01, input_per_mtok: 1.0, output_per_mtok: 5.0}
  unapproved-model:
    provider: internal_only
    model_id: internal-1
    capabilities: [vision, structured_output]
  incapable-model:
    provider: approved
    model_id: incapable-1
    capabilities: [structured_output]
routes:
  title_block_read:
    requires: [vision, structured_output]
    data_class: confidential
    models: [good-model]
"""


@pytest.fixture
def compare_config(tmp_path: Path) -> LlmConfig:
    path = tmp_path / "llm.yaml"
    path.write_text(COMPARE_CONFIG, encoding="utf-8")
    return load_config(path)


@pytest.mark.req("NFR-11")
class TestCompareModels:
    def _predictor_for(self, accuracy: dict[str, float]) -> Callable[[LlmConfig, str], Predictor]:
        def build(_config: LlmConfig, model_name: str) -> Predictor:
            return DummyPredictor(
                name=f"dummy[{model_name}]",
                version="1",
                count_error=accuracy.get(model_name, 0.0),
            )

        return build

    def test_it_ranks_two_models_of_known_accuracy_correctly(
        self, compare_config: LlmConfig, golden: GoldenSet
    ) -> None:
        comparison = compare_models(
            compare_config,
            golden,
            "title_block_read",
            ["weak-model", "good-model"],
            self._predictor_for({"weak-model": 0.3, "good-model": 0.0}),
        )
        ranked = [candidate.model for candidate in comparison.ranked()]
        assert ranked[0] == "good-model", "the more accurate model should rank first"
        assert ranked[1] == "weak-model"
        assert comparison.best() is not None
        best = comparison.best()
        assert best is not None and best.model == "good-model"

    def test_a_candidate_not_approved_for_the_data_class_is_refused(
        self, compare_config: LlmConfig, golden: GoldenSet
    ) -> None:
        """The gateway would refuse the call anyway; the report should say why."""
        comparison = compare_models(
            compare_config,
            golden,
            "title_block_read",
            ["good-model", "unapproved-model"],
            self._predictor_for({}),
        )
        refused = next(c for c in comparison.candidates if c.model == "unapproved-model")
        assert refused.result is None, "an unapproved model must not be run"
        assert "confidential" in (refused.refused_because or "")
        assert "internal" in (refused.refused_because or "")

    def test_a_candidate_lacking_a_required_capability_is_refused(
        self, compare_config: LlmConfig, golden: GoldenSet
    ) -> None:
        comparison = compare_models(
            compare_config,
            golden,
            "title_block_read",
            ["incapable-model"],
            self._predictor_for({}),
        )
        assert "vision" in (comparison.candidates[0].refused_because or "")

    def test_the_report_shows_cost_and_latency_beside_quality(
        self, compare_config: LlmConfig, golden: GoldenSet
    ) -> None:
        comparison = compare_models(
            compare_config,
            golden,
            "title_block_read",
            ["good-model", "weak-model", "unapproved-model"],
            self._predictor_for({"weak-model": 0.3}),
        )
        report = comparison_report(comparison)
        assert "Cost (SGD)" in report
        assert "p50 ms" in report and "p95 ms" in report
        assert "Not run" in report, "a refused candidate should be explained, not hidden"
        assert "decision for the product owner" in report

    def test_the_report_records_the_configuration_version(
        self, compare_config: LlmConfig, golden: GoldenSet
    ) -> None:
        """Which configuration produced this evidence has to be recoverable later (NFR-11)."""
        comparison = compare_models(
            compare_config, golden, "title_block_read", ["good-model"], self._predictor_for({})
        )
        assert compare_config.config_hash in comparison_report(comparison)

    def test_pinning_a_route_does_not_mutate_the_running_configuration(
        self, compare_config: LlmConfig
    ) -> None:
        original = list(compare_config.routes["title_block_read"].models)
        pinned = with_route_model(compare_config, "title_block_read", "weak-model")
        assert pinned.routes["title_block_read"].models == ["weak-model"]
        assert compare_config.routes["title_block_read"].models == original

    def test_checking_a_candidate_directly(self, compare_config: LlmConfig) -> None:
        check_candidate(compare_config, "title_block_read", "good-model")  # does not raise
        with pytest.raises(CandidateRefused, match="not a model"):
            check_candidate(compare_config, "title_block_read", "imaginary")


class TestTheCommandLine:
    def test_run_writes_a_report(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        report = tmp_path / "report.md"
        code = main(["--root", str(tmp_path), "run", "--tenders", "2", "--report", str(report)])
        assert code == 0
        assert report.exists()
        assert (tmp_path / "results" / "synthetic.json").exists()

    def test_accept_then_compare_passes(self, tmp_path: Path) -> None:
        root = ["--root", str(tmp_path)]
        assert main([*root, "accept", "--tenders", "2", "--approver", "Tester"]) == 0
        assert main([*root, "compare", "--tenders", "2"]) == 0

    def test_compare_fails_on_an_injected_regression(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """This is what runs in CI."""
        root = ["--root", str(tmp_path)]
        assert main([*root, "accept", "--tenders", "2", "--approver", "Tester"]) == 0
        assert main([*root, "compare", "--tenders", "2", "--count-error", "0.25"]) == 1
        assert "regression" in capsys.readouterr().err

    def test_compare_without_a_baseline_says_how_to_make_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--root", str(tmp_path), "compare", "--tenders", "1"]) == 1
        assert "firebid-eval accept" in capsys.readouterr().err

    def test_generate_writes_fixtures(self, tmp_path: Path) -> None:
        assert main(["--root", str(tmp_path), "generate", "--tenders", "1"]) == 0
        written = list((tmp_path / "synthetic" / "synthetic").rglob("*.dxf"))
        assert written
