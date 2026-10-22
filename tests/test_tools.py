"""Tests for the sandbox. A path-traversal hole here would make every score a lie."""

from pathlib import Path

import pytest

from harnessprobe.tools import Sandbox, ToolError, build_tool_specs


@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "hello.txt").write_text("line one\nline two\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.py").write_text(
        "def f():\n    return 42\n", encoding="utf-8"
    )
    return Sandbox(tmp_path)


# -- confinement ----------------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["../outside.txt", "../../etc/passwd", "sub/../../escape.txt"]
)
def test_parent_traversal_is_rejected(sandbox, bad):
    with pytest.raises(ToolError, match="escapes the workspace"):
        sandbox.resolve(bad)


@pytest.mark.parametrize("bad", ["/etc/passwd", "C:/Windows/System32", "D:\\data\\x"])
def test_absolute_paths_are_rejected(sandbox, bad):
    with pytest.raises(ToolError, match="absolute"):
        sandbox.resolve(bad)


def test_relative_paths_resolve_inside(sandbox):
    assert sandbox.resolve("sub/nested.py").exists()
    assert sandbox.resolve(".") == sandbox.root


# -- read / write ---------------------------------------------------------


def test_read_file_numbers_lines(sandbox):
    out = sandbox.read_file("hello.txt")
    assert "line one" in out.content
    assert out.content.startswith("    1\t")


def test_read_file_reports_missing_file(sandbox):
    out = sandbox.read_file("nope.txt")
    assert out.is_error and "no such file" in out.content


def test_read_file_respects_offset_and_limit(sandbox):
    out = sandbox.read_file("hello.txt", offset=2, limit=1)
    assert "line two" in out.content
    assert "line one" not in out.content


def test_write_file_creates_parents(sandbox, tmp_path):
    out = sandbox.write_file("deep/a/b/new.txt", "content")
    assert not out.is_error
    assert (tmp_path / "deep" / "a" / "b" / "new.txt").read_text(
        encoding="utf-8"
    ) == "content"


def test_readonly_files_cannot_be_written(tmp_path):
    (tmp_path / "locked.py").write_text("x = 1\n", encoding="utf-8")
    box = Sandbox(tmp_path, readonly=["locked.py"])
    out = box.write_file("locked.py", "x = 2\n")
    assert out.is_error and "read-only" in out.content


# -- listing / search -----------------------------------------------------


def test_list_dir_marks_directories(sandbox):
    out = sandbox.list_dir(".")
    assert "sub/" in out.content
    assert "hello.txt" in out.content


def test_list_dir_on_a_file_is_an_error(sandbox):
    out = sandbox.list_dir("hello.txt")
    assert out.is_error


def test_search_returns_path_line_matches(sandbox):
    out = sandbox.search(r"return 42")
    assert "sub/nested.py:2:" in out.content


def test_search_reports_no_matches(sandbox):
    out = sandbox.search("this_does_not_exist_anywhere")
    assert out.content == "(no matches)"


def test_search_rejects_a_bad_regex(sandbox):
    out = sandbox.search("[unclosed")
    assert out.is_error and "bad regex" in out.content


def test_search_skips_pycache(sandbox, tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "junk.py").write_text("needle\n", encoding="utf-8")
    out = sandbox.search("needle")
    assert out.content == "(no matches)"


# -- command execution ----------------------------------------------------


def test_run_command_reports_exit_code(sandbox):
    out = sandbox.run_command("echo hi")
    assert "hi" in out.content
    assert "[exit 0]" in out.content
    assert not out.is_error


def test_run_command_nonzero_is_an_error(sandbox):
    out = sandbox.run_command("exit 3")
    assert out.is_error
    assert "[exit 3]" in out.content


def test_run_command_captures_stderr(sandbox):
    out = sandbox.run_command("echo problem 1>&2")
    assert "[stderr]" in out.content
    assert "problem" in out.content


def test_run_command_runs_in_the_sandbox(sandbox, tmp_path):
    sandbox.write_file("marker.txt", "x")
    out = sandbox.run_command("cat marker.txt")
    assert "x" in out.content


# -- isolation -------------------------------------------------------------
#
# `shell=True` hands the model a full shell, so the environment is the only
# thing between it and the host's credentials. These tests are the actual
# security boundary; without them the isolation is a comment.


def test_isolated_env_excludes_host_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("SOME_SESSION_TOKEN", "should-not-leak")
    box = Sandbox(Path("."), isolated=True)
    env = box.command_env()
    leaked = box.blocked_env_present(env)
    assert leaked == [], f"credentials present in sandbox env: {leaked}"


def test_isolated_command_cannot_read_credentials(sandbox, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    out = sandbox.run_command("env")
    assert "sk-ant-should-not-leak" not in out.content
    assert "ANTHROPIC_API_KEY" not in out.content


def test_isolated_env_keeps_the_path_so_tools_work(sandbox):
    env = sandbox.command_env()
    assert "PATH" in env
    out = sandbox.run_command("python --version")
    assert not out.is_error or "python" in out.content.lower()


def test_isolated_env_blocks_proxy_variables(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://10.0.0.1:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:8080")
    box = Sandbox(Path("."), isolated=True)
    env = box.command_env()
    assert not box.blocked_env_present(env)
    assert env.get("no_proxy") == "*"


def test_non_isolated_mode_inherits_host_env(monkeypatch):
    monkeypatch.setenv("HP_TEST_MARKER", "present")
    box = Sandbox(Path("."), isolated=False)
    assert box.command_env().get("HP_TEST_MARKER") == "present"


def test_extra_env_is_applied_last(sandbox):
    box = Sandbox(sandbox.root, extra_env={"HP_EXTRA": "yes"})
    assert box.command_env().get("HP_EXTRA") == "yes"


# -- tool specs -----------------------------------------------------------


def test_full_descriptions_are_longer_than_minimal():
    full = build_tool_specs(["read_file"], "full")[0]["description"]
    terse = build_tool_specs(["read_file"], "terse")[0]["description"]
    minimal = build_tool_specs(["read_file"], "minimal")[0]["description"]
    assert len(full) > len(terse) > len(minimal)


def test_unknown_style_falls_back_to_full():
    a = build_tool_specs(["read_file"], "nonsense")[0]["description"]
    b = build_tool_specs(["read_file"], "full")[0]["description"]
    assert a == b


def test_unknown_tool_raises():
    with pytest.raises(ToolError, match="unknown tool"):
        build_tool_specs(["not_a_tool"], "full")


def test_schemas_are_valid_json_schema_objects():
    for spec in build_tool_specs(["read_file", "write_file", "run_command"], "full"):
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema


# -- search glob must not defeat confinement ---------------------------------
#
# The glob goes to Path.rglob, which treats it as a path pattern. Without
# validation, "../*" walks out of the sandbox and returns the host's files as
# search hits — a one-parameter bypass of the confinement the file tools enforce.


def test_search_rejects_a_parent_traversal_glob(sandbox, tmp_path):
    (tmp_path / "outside.txt").write_text("needle-outside\n", encoding="utf-8")
    out = sandbox.search("needle-outside", "../*")
    assert out.is_error
    assert "outside.txt" not in out.content


@pytest.mark.parametrize("bad", ["/etc/*", "C:/Windows/*", "..", "../**/*"])
def test_search_rejects_escaping_globs(sandbox, bad):
    out = sandbox.search("anything", bad)
    assert out.is_error, f"glob {bad!r} was accepted"


def test_search_still_accepts_a_filename_pattern(sandbox):
    out = sandbox.search("return 42", "*.py")
    assert not out.is_error
    assert "nested.py" in out.content


def test_search_without_a_glob_still_works(sandbox):
    out = sandbox.search("line one")
    assert not out.is_error
    assert "hello.txt" in out.content


# -- graders must use the same environment policy ---------------------------


def test_grader_subprocess_does_not_inherit_credentials(tmp_path, monkeypatch):
    """A grader runs the task's own code, so it must not see the API key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-not-leak")
    from harnessprobe.graders import grade
    from harnessprobe.spec import Step, Task, TrialResult

    task = Task.from_dict(
        {
            "id": "env-probe",
            "prompt": "p",
            "grader": {"type": "exit_code", "options": {"command": "env"}},
        },
        root=tmp_path,
    )
    result = TrialResult(
        task_id=task.id,
        harness="h",
        model="m",
        trial=0,
        passed=False,
        steps=[Step(index=0, kind="assistant")],
    )
    graded = grade(task, result, tmp_path)

    assert "sk-ant-must-not-leak" not in graded.grader_output
    assert "ANTHROPIC_API_KEY" not in graded.grader_output


def test_grader_pytest_still_runs_with_the_filtered_env(tmp_path):
    """The filter must not break grading: pytest needs PATH and a python."""

    from harnessprobe.graders import grade
    from harnessprobe.spec import Step, Task, TrialResult

    task_dir = tmp_path / "task"
    (task_dir / "workspace").mkdir(parents=True)
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (task_dir / "task.json").write_text(
        '{"id":"t","prompt":"p","grader":{"type":"pytest","target":"tests"}}',
        encoding="utf-8",
    )
    task = Task.load(task_dir)

    result = TrialResult(
        task_id=task.id,
        harness="h",
        model="m",
        trial=0,
        passed=False,
        steps=[Step(index=0, kind="assistant")],
    )
    graded = grade(task, result, task_dir / "workspace")
    assert graded.passed, graded.grader_output[:400]
