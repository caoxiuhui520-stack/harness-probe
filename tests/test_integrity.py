"""Regression tests for two integrity defects found after v0.3.0.

1. `--resume` did not cover the provider, model or token ceiling in its
   experiment fingerprint, so resuming with a different model reused the first
   model's trials and reported them under the second model's name.

2. `_stage_tests` marks the staged test copies read-only, and
   `shutil.rmtree` cannot delete a read-only file on Windows. Cleanup silently
   failed, sandbox directories accumulated across runs, and the staged tests
   stayed readable on disk after the trial was over.
"""

from pathlib import Path

from harnessprobe.cli import experiment_fingerprint
from harnessprobe.runner import _remove_tree

REPO = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO / "tasks"
MATRIX = REPO / "configs" / "matrix-core.json"


def _tasks():
    from harnessprobe.cli import load_tasks

    return load_tasks([str(TASKS_DIR / "01-fix-off-by-one")])


def _harnesses():
    from harnessprobe.cli import load_harnesses

    return load_harnesses(str(MATRIX))


# -- fingerprint coverage ---------------------------------------------------


def test_fingerprint_changes_with_the_model():
    tasks = _tasks()
    harnesses, baseline = _harnesses()
    a = experiment_fingerprint(
        tasks, harnesses, baseline, 1, provider="anthropic", model="model-A"
    )
    b = experiment_fingerprint(
        tasks, harnesses, baseline, 1, provider="anthropic", model="model-B"
    )
    assert a != b, "a model change must invalidate a resume"


def test_fingerprint_changes_with_the_provider():
    tasks = _tasks()
    harnesses, baseline = _harnesses()
    a = experiment_fingerprint(
        tasks, harnesses, baseline, 1, provider="anthropic", model="m"
    )
    b = experiment_fingerprint(
        tasks, harnesses, baseline, 1, provider="deepseek", model="m"
    )
    assert a != b


def test_fingerprint_changes_with_max_tokens():
    tasks = _tasks()
    harnesses, baseline = _harnesses()
    a = experiment_fingerprint(
        tasks, harnesses, baseline, 1, provider="p", model="m", max_tokens=4096
    )
    b = experiment_fingerprint(
        tasks, harnesses, baseline, 1, provider="p", model="m", max_tokens=8192
    )
    assert a != b


def test_fingerprint_is_stable_for_identical_input():
    tasks = _tasks()
    harnesses, baseline = _harnesses()
    kwargs = {"provider": "anthropic", "model": "m", "max_tokens": 4096}
    assert experiment_fingerprint(tasks, harnesses, baseline, 1, **kwargs) == (
        experiment_fingerprint(tasks, harnesses, baseline, 1, **kwargs)
    )


def test_fingerprint_changes_with_a_harness_knob():
    tasks = _tasks()
    harnesses, baseline = _harnesses()
    before = experiment_fingerprint(tasks, harnesses, baseline, 1, model="m")
    harnesses[1].max_steps = 999
    after = experiment_fingerprint(tasks, harnesses, baseline, 1, model="m")
    assert before != after


# -- read-only sandbox cleanup ---------------------------------------------


def test_remove_tree_deletes_read_only_files(tmp_path):
    """The staged tests are chmod 0o444; rmtree alone leaves them on Windows."""
    nested = tmp_path / "sandbox" / "tests"
    nested.mkdir(parents=True)
    victim = nested / "test_x.py"
    victim.write_text("def test_x():\n    pass\n", encoding="utf-8")
    victim.chmod(0o444)

    _remove_tree(tmp_path / "sandbox")

    assert not (tmp_path / "sandbox").exists(), (
        "read-only files survived cleanup; the sandbox is leaking onto disk"
    )


def test_remove_tree_handles_a_missing_directory(tmp_path):
    _remove_tree(tmp_path / "never-existed")  # must not raise


def test_remove_tree_handles_nested_read_only_dirs(tmp_path):
    deep = tmp_path / "s" / "a" / "b" / "c"
    deep.mkdir(parents=True)
    for i in range(3):
        f = deep / f"f{i}.txt"
        f.write_text("x", encoding="utf-8")
        f.chmod(0o444)
    _remove_tree(tmp_path / "s")
    assert not (tmp_path / "s").exists()


def test_staged_tests_are_read_only_but_the_sandbox_is_removable(tmp_path):
    """End-to-end: stage, confirm read-only, then confirm cleanup succeeds."""
    from harnessprobe.runner import _stage_tests

    task = _tasks()[0]
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    _stage_tests(task, sandbox)

    staged = sandbox / "tests" / "test_paginate.py"
    assert staged.exists()
    assert not (staged.stat().st_mode & 0o200), "staged tests should be read-only"

    _remove_tree(sandbox)
    assert not sandbox.exists()
