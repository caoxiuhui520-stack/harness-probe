"""Tests for task and harness-config parsing."""

import json
from pathlib import Path

import pytest

from harnessprobe.spec import HarnessConfig, SpecError, Task, TrialResult


# -- HarnessConfig --------------------------------------------------------


def test_merge_inherits_unset_knobs():
    base = HarnessConfig(name="base", max_steps=20, temperature=0.0)
    child = HarnessConfig(name="child", max_steps=8)
    merged = child.merged(base)
    assert merged.max_steps == 8
    assert merged.temperature == 0.0
    assert merged.name == "child"


def test_merge_does_not_mutate_inputs():
    base = HarnessConfig(name="base", max_steps=20)
    child = HarnessConfig(name="child")
    child.merged(base)
    assert child.max_steps is None


@pytest.mark.parametrize(
    "raw,message",
    [
        ({"history_policy": "nonsense"}, "history_policy"),
        ({"tool_description_style": "nonsense"}, "tool_description_style"),
        ({"max_steps": 0}, "max_steps"),
        ({"temperature": 9.0}, "temperature"),
        ({"not_a_knob": 1}, "unknown harness knobs"),
    ],
)
def test_invalid_configs_are_rejected(raw, message):
    with pytest.raises(SpecError, match=message):
        HarnessConfig.from_dict(raw).validate()


def test_round_trip_through_dict():
    cfg = HarnessConfig(name="x", max_steps=5, temperature=0.2, tool_set=["read_file"])
    again = HarnessConfig.from_dict(cfg.to_dict())
    assert again == cfg


# -- Task -----------------------------------------------------------------


def make_task(tmp_path: Path, **overrides):
    raw = {"id": "t1", "prompt": "do the thing", **overrides}
    return Task.from_dict(raw, root=tmp_path)


def test_task_defaults(tmp_path):
    task = make_task(tmp_path)
    assert task.max_steps == 15
    assert task.workspace == "workspace"
    assert task.grader.type == "exit_code"


def test_task_requires_id_and_prompt(tmp_path):
    with pytest.raises(SpecError, match="id"):
        Task.from_dict({"prompt": "x"})
    with pytest.raises(SpecError, match="prompt"):
        Task.from_dict({"id": "x"})


def test_task_rejects_unknown_grader(tmp_path):
    with pytest.raises(SpecError, match="unknown grader"):
        Task.from_dict({"id": "t", "prompt": "p", "grader": {"type": "vibes"}})


def test_task_grader_may_be_a_bare_string(tmp_path):
    task = Task.from_dict({"id": "t", "prompt": "p", "grader": "pytest"})
    assert task.grader.type == "pytest"


def test_fingerprint_is_stable_and_sensitive(tmp_path):
    a = make_task(tmp_path)
    b = make_task(tmp_path)
    assert a.fingerprint == b.fingerprint
    c = make_task(tmp_path, prompt="different")
    assert a.fingerprint != c.fingerprint


def test_fingerprint_ignores_cosmetic_fields(tmp_path):
    a = make_task(tmp_path, title="A", tags=["x"], difficulty="easy")
    b = make_task(tmp_path, title="B", tags=["y"], difficulty="hard")
    assert a.fingerprint == b.fingerprint


def test_task_loads_from_directory(tmp_path):
    task_dir = tmp_path / "mytask"
    (task_dir / "workspace").mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps({"id": "mytask", "prompt": "p"}), encoding="utf-8"
    )
    task = Task.load(task_dir)
    assert task.id == "mytask"
    assert task.workspace_path == task_dir / "workspace"


def test_task_load_reports_a_missing_spec(tmp_path):
    with pytest.raises(SpecError, match="missing task.json"):
        Task.load(tmp_path)


# -- TrialResult ----------------------------------------------------------


def test_trial_result_round_trips():
    from harnessprobe.spec import Step

    r = TrialResult(
        task_id="t",
        harness="h",
        model="m",
        trial=0,
        passed=True,
        steps=[Step(index=0, kind="assistant", text="hi")],
        input_tokens=10,
        output_tokens=5,
    )
    again = TrialResult.from_dict(r.to_dict())
    assert again.passed and again.task_id == "t"
    assert again.steps[0].text == "hi"
    assert again.total_tokens == 15
