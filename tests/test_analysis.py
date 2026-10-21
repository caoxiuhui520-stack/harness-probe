"""Tests for the statistics. These numbers end up in published reports."""

import pytest

from harnessprobe.analysis import (
    aggregate,
    analyze,
    pass_at_k,
    pass_pow_k,
    render_markdown,
    wilson_interval,
)
from harnessprobe.spec import TrialResult


def trial(task, harness, passed, tokens=100, trial=0):
    return TrialResult(
        task_id=task,
        harness=harness,
        model="test-model",
        trial=trial,
        passed=passed,
        input_tokens=tokens,
        output_tokens=0,
        stop_reason="submitted" if passed else "max_steps",
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


def test_flakiness_is_zero_for_deterministic_results():
    results = [trial("t1", "base", True, trial=i) for i in range(4)]
    report = analyze(results, model="m", baseline="base")
    assert report.rows[0].flakiness == pytest.approx(0.0)


def test_flakiness_detects_mixed_results():
    results = [trial("t1", "base", True, trial=0), trial("t1", "base", False, trial=1)]
    report = analyze(results, model="m", baseline="base")
    # pass@1 = 0.5, pass^1 = 0.5 -> flakiness 0 at k=1 by definition
    assert report.rows[0].flakiness == pytest.approx(0.0)
    report2 = analyze(results, model="m", baseline="base", k=2)
    # pass^2 = 0 (only 1 pass of 2), pass@2 = 1 -> flakiness 1.0
    assert report2.rows[0].flakiness == pytest.approx(1.0)


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
