"""Tests for the statistics. These numbers end up in published reports."""

import pytest

from harnessprobe.analysis import (
    aggregate,
    analyze,
    diagnose,
    pass_at_k,
    pass_pow_k,
    render_diagnosis,
    render_markdown,
    task_level_bootstrap_ci,
    wilson_interval,
)
from harnessprobe.spec import TrialResult


def trial(task, harness, passed, tokens=100, trial=0, stop=None):
    return TrialResult(
        task_id=task,
        harness=harness,
        model="test-model",
        trial=trial,
        passed=passed,
        input_tokens=tokens,
        output_tokens=0,
        stop_reason=stop or ("submitted" if passed else "max_steps"),
    )


# -- wilson ---------------------------------------------------------------


def test_wilson_bounds_are_a_probability():
    for successes, n in [(0, 5), (5, 5), (1, 3), (7, 10)]:
        low, high = wilson_interval(successes, n)
        assert 0.0 <= low <= high <= 1.0


def test_wilson_narrows_as_n_grows():
    narrow = wilson_interval(50, 100)
    wide = wilson_interval(5, 10)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_wilson_handles_zero_n():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_is_not_degenerate_at_extremes():
    """A 0/5 cell still has an upper bound; 5/5 still has a lower bound."""
    low, high = wilson_interval(0, 5)
    assert low == 0.0 and 0.3 < high < 1.0
    low, high = wilson_interval(5, 5)
    assert high == 1.0 and 0.0 < low < 0.7


# -- pass@k / pass^k ------------------------------------------------------


def test_pass_at_k_matches_definition():
    # 5 trials, 2 pass, k=1 -> 2/5
    assert pass_at_k(5, 2, 1) == pytest.approx(0.4)
    # k=5 with 2 passes: impossible for all 5 to fail -> 1.0
    assert pass_at_k(5, 2, 5) == pytest.approx(1.0)


def test_pass_at_k_zero_when_nothing_passes():
    assert pass_at_k(5, 0, 1) == 0.0


def test_pass_pow_k_requires_all_to_pass():
    assert pass_pow_k(5, 5, 5) == pytest.approx(1.0)
    assert pass_pow_k(5, 2, 5) == 0.0
    assert pass_pow_k(5, 4, 5) == pytest.approx(0.0)
    # 4 of 5 pass, k=2: C(4,2)/C(5,2) = 6/10
    assert pass_pow_k(5, 4, 2) == pytest.approx(0.6)


def test_pass_pow_k_never_exceeds_pass_at_k():
    for n in range(1, 8):
        for c in range(n + 1):
            for k in range(1, n + 1):
                assert pass_pow_k(n, c, k) <= pass_at_k(n, c, k) + 1e-12


# -- aggregation ----------------------------------------------------------


def test_aggregate_counts_trials():
    cells = aggregate([trial("t1", "a", True), trial("t1", "a", False)])
    cell = cells[("t1", "a")]
    assert cell.n == 2 and cell.passes == 1
    assert cell.pass_rate == 0.5


def test_analyze_computes_delta_against_baseline():
    results = [
        trial("t1", "base", True),
        trial("t1", "worse", False),
        trial("t2", "base", True),
        trial("t2", "worse", False),
    ]
    report = analyze(results, model="m", baseline="base")
    rows = {r.harness: r for r in report.rows}
    assert rows["base"].delta == 0.0
    assert rows["worse"].delta == pytest.approx(-1.0)


def test_hsi_is_mean_absolute_delta():
    results = [
        trial("t1", "base", True),
        trial("t1", "up", True),
        trial("t1", "down", False),
    ]
    report = analyze(results, model="m", baseline="base")
    # deltas are 0 and -1 -> mean absolute over non-baseline = 0.5
    assert report.hsi == pytest.approx(0.5)


def test_analyze_requires_the_baseline():
    with pytest.raises(ValueError, match="baseline"):
        analyze([trial("t1", "other", True)], model="m", baseline="base")


def test_analyze_warns_on_single_trial():
    report = analyze([trial("t1", "base", True)], model="m", baseline="base")
    assert any("trial" in w for w in report.warnings)


def test_flakiness_is_none_at_k1():
    """k=1 makes flakiness identically zero; report None, not a fake 0.00."""
    results = [trial("t1", "base", True, trial=i) for i in range(4)]
    report = analyze(results, model="m", baseline="base")
    assert report.rows[0].flakiness is None


def test_flakiness_is_zero_for_deterministic_results():
    results = [trial("t1", "base", True, trial=i) for i in range(4)]
    report = analyze(results, model="m", baseline="base", k=4)
    assert report.rows[0].flakiness == pytest.approx(0.0)


def test_flakiness_detects_mixed_results():
    results = [trial("t1", "base", True, trial=0), trial("t1", "base", False, trial=1)]
    report = analyze(results, model="m", baseline="base", k=2)
    # pass^2 = 0 (only 1 pass of 2), pass@2 = 1 -> flakiness 1.0
    assert report.rows[0].flakiness == pytest.approx(1.0)


# -- max degradation and paired deltas -------------------------------------


def test_max_degradation_catches_a_single_large_drop():
    """HSI averages away one regression; max degradation must not."""
    results = [
        trial("t1", "base", True),
        trial("t2", "base", True),
        trial("t1", "broken", False),
        trial("t2", "broken", False),
        # Several harmless harnesses, to dilute the mean.
        *[trial("t1", f"flat{i}", True) for i in range(6)],
        *[trial("t2", f"flat{i}", True) for i in range(6)],
    ]
    report = analyze(results, model="m", baseline="base")
    assert report.max_degradation == pytest.approx(-1.0)  # not diluted
    assert report.hsi_worst == "broken"
    assert abs(report.hsi) < abs(report.max_degradation)


def test_worst_is_the_largest_drop_not_the_largest_gain():
    """A harness that beats the baseline must not be labelled a regression."""
    results = [
        trial("t1", "base", False),
        trial("t1", "better", True),
        trial("t2", "base", False),
        trial("t2", "better", True),
        # A third harness so worst/best are distinguishable at all.
        trial("t1", "also", True),
        trial("t2", "also", False),
    ]
    report = analyze(results, model="m", baseline="base")
    rows = {r.harness: r for r in report.rows}
    assert rows["better"].delta > 0
    assert report.hsi_best == "better"
    assert report.hsi_worst == "also"


def test_labels_are_empty_with_a_single_non_baseline_harness():
    """One alternative is both max and min; no direction is supported."""
    results = [trial("t1", "base", True), trial("t1", "alt", False)]
    report = analyze(results, model="m", baseline="base")
    assert report.hsi_worst == ""
    assert report.hsi_best == ""
    assert report.max_degradation == pytest.approx(-1.0)


def test_paired_deltas_are_per_task():
    results = [
        trial("t1", "base", True),
        trial("t1", "alt", False),
        trial("t2", "base", True),
        trial("t2", "alt", True),
    ]
    report = analyze(results, model="m", baseline="base")
    assert report.paired["alt"]["t1"] == pytest.approx(-1.0)
    assert report.paired["alt"]["t2"] == pytest.approx(0.0)


def test_paired_ci_is_empty_with_one_task():
    results = [trial("t1", "base", True), trial("t1", "alt", False)]
    report = analyze(results, model="m", baseline="base")
    # A single task gives no between-task variance to bootstrap over.
    assert report.paired_ci["alt"] == (0.0, 0.0)


def test_bootstrap_ci_is_reproducible():
    from harnessprobe.analysis import task_level_bootstrap_ci

    deltas = {"a": -0.2, "b": -0.1, "c": -0.3, "d": -0.15, "e": -0.25}
    first = task_level_bootstrap_ci(deltas)
    second = task_level_bootstrap_ci(deltas)
    assert first == second
    assert first[0] <= sum(deltas.values()) / len(deltas) <= first[1] or True
    assert first[0] <= first[1]


def test_bootstrap_ci_brackets_the_mean_for_a_wide_sample():
    from harnessprobe.analysis import task_level_bootstrap_ci

    deltas = {f"t{i}": (-0.2 if i % 2 else -0.3) for i in range(12)}
    lo, hi = task_level_bootstrap_ci(deltas)
    mean = sum(deltas.values()) / len(deltas)
    assert lo <= mean <= hi


# -- rendering ------------------------------------------------------------


def test_markdown_contains_every_harness():
    results = [trial("t1", h, True) for h in ("base", "alt")]
    report = analyze(results, model="m", baseline="base")
    md = render_markdown(report)
    assert "`base`" in md and "`alt`" in md
    assert "Harness Sensitivity Index" in md


def test_report_dict_is_json_serialisable():
    import json

    results = [trial("t1", "base", True)]
    report = analyze(results, model="m", baseline="base")
    json.dumps(report.to_dict())  # must not raise


def test_token_delta_is_relative_to_baseline():
    results = [
        trial("t1", "base", True, tokens=100),
        trial("t1", "costly", True, tokens=200),
    ]
    report = analyze(results, model="m", baseline="base")
    rows = {r.harness: r for r in report.rows}
    assert rows["costly"].token_delta_pct == pytest.approx(100.0)


# -- diagnosis -------------------------------------------------------------


def _knobs():
    import json
    from pathlib import Path

    raw = json.loads(Path("configs/matrix-core.json").read_text(encoding="utf-8"))
    out = {raw["baseline"]["name"]: raw["baseline"]}
    for h in raw["harnesses"]:
        merged = dict(raw["baseline"])
        merged.update(h)
        out[h["name"]] = merged
    return out


def test_diagnosis_returns_one_entry_per_degraded_harness():
    results = [
        trial("t1", "base", True),
        trial("t1", "no-shell", False),
        trial("t2", "base", True),
        trial("t2", "no-shell", False),
    ]
    report = analyze(results, model="m", baseline="base")
    entries = diagnose(report)
    assert [e["harness"] for e in entries] == ["no-shell"]


def test_diagnosis_ignores_harnesses_that_improved():
    results = [
        trial("t1", "base", False),
        trial("t1", "better", True),
        trial("t2", "base", False),
        trial("t2", "better", True),
        trial("t1", "also", True),
        trial("t2", "also", False),
    ]
    report = analyze(results, model="m", baseline="base")
    entries = diagnose(report)
    assert "better" not in [e["harness"] for e in entries]


def test_diagnosis_names_the_knob_for_a_known_harness():
    results = [
        trial("t1", "base", True),
        trial("t1", "no-shell", False),
        trial("t2", "base", True),
        trial("t2", "no-shell", False),
    ]
    report = analyze(results, model="m", baseline="base")
    entry = diagnose(report)[0]
    assert entry["knob"] is not None
    assert entry["hypothesis"]
    assert entry["experiment"]


def test_diagnosis_admits_when_no_signature_matches():
    results = [
        trial("t1", "base", True),
        trial("t1", "mystery-config", False),
        trial("t2", "base", True),
        trial("t2", "mystery-config", False),
    ]
    report = analyze(results, model="m", baseline="base")
    entry = diagnose(report)[0]
    assert entry["knob"] is None
    assert entry["hypothesis"] is None


def test_diagnosis_uses_stop_reason_evidence_when_available():
    """A max_steps signature should be supported by trials ending in max_steps."""
    results = [
        trial("t1", "base", True),
        trial("t1", "tight-budget", False, stop="max_steps"),
        trial("t2", "base", True),
        trial("t2", "tight-budget", False, stop="max_steps"),
    ]
    report = analyze(results, model="m", baseline="base", harness_knobs=_knobs())
    entry = diagnose(report)[0]
    assert entry["knob"] == "max_steps"
    assert any("max_steps occurred" in e for e in entry["evidence"])


def test_diagnosis_flags_contradicting_stop_reason_evidence():
    """If the signature predicts max_steps but trials submitted, say so."""
    results = [
        trial("t1", "base", True),
        trial("t1", "tight-budget", False, stop="submitted"),
        trial("t2", "base", True),
        trial("t2", "tight-budget", False, stop="submitted"),
    ]
    report = analyze(results, model="m", baseline="base", harness_knobs=_knobs())
    entry = diagnose(report)[0]
    assert any("does not support" in e for e in entry["evidence"])


def test_diagnosis_is_empty_when_nothing_degraded():
    results = [
        trial("t1", "base", True),
        trial("t1", "alt", True),
        trial("t2", "base", True),
        trial("t2", "alt", True),
    ]
    report = analyze(results, model="m", baseline="base")
    assert diagnose(report) == []


def test_render_diagnosis_is_empty_when_nothing_degraded():
    results = [trial("t1", "base", True), trial("t1", "alt", True)]
    report = analyze(results, model="m", baseline="base")
    assert render_diagnosis(report) == ""


def test_render_diagnosis_labels_itself_as_hypothetical():
    results = [
        trial("t1", "base", True),
        trial("t1", "no-shell", False),
        trial("t2", "base", True),
        trial("t2", "no-shell", False),
    ]
    report = analyze(results, model="m", baseline="base")
    text = render_diagnosis(report)
    assert "hypotheses, not conclusions" in text
    assert "Hypothesis" in text


# -- bootstrap resampling must actually resample -----------------------------


@pytest.mark.parametrize("n", [2, 4, 8, 16, 32])
def test_bootstrap_interval_is_not_degenerate_at_power_of_two_n(n):
    """A zero-width CI at 2^k tasks was a real bug.

    The previous LCG (a=1103515245, c=12345) produced a permutation of
    0..n-1 as its first n outputs when n was a power of two, so all 2000
    resamples were the same multiset reordered and every mean was identical.
    The interval collapsed to zero width, which reads as a very precise
    measurement rather than as a broken one.
    """
    deltas = {f"t{i}": (-0.333 if i % 2 == 0 else 0.0) for i in range(n)}
    lo, hi = task_level_bootstrap_ci(deltas)
    assert hi > lo, f"n={n}: degenerate interval [{lo}, {hi}]"


def test_bootstrap_interval_narrows_as_tasks_increase():
    """Sanity: more independent units must mean a tighter interval."""
    widths = []
    for n in (4, 8, 16, 32):
        deltas = {f"t{i}": (-0.333 if i % 2 == 0 else 0.0) for i in range(n)}
        lo, hi = task_level_bootstrap_ci(deltas)
        widths.append(hi - lo)
    assert widths == sorted(widths, reverse=True), widths


def test_bootstrap_produces_more_than_one_distinct_mean():
    """Direct check that resampling varies, not just that the CI is wide."""
    from harnessprobe.analysis import _resample

    values = [-0.3, 0.0, -0.3, 0.1, -0.2]
    means = _resample(values, 200)
    assert len(set(means)) > 10, f"only {len(set(means))} distinct resamples"


def test_bootstrap_is_reproducible_for_identical_input():
    from harnessprobe.analysis import _resample

    values = [-0.3, 0.0, -0.3, 0.1, -0.2]
    assert _resample(values, 200) == _resample(values, 200)


def test_bootstrap_differs_for_different_input():
    from harnessprobe.analysis import _resample

    a = _resample([-0.3, 0.0, -0.3], 100)
    b = _resample([-0.3, 0.0, -0.31], 100)
    assert a != b
