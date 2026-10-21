"""Tests for the agent loop and the history policies.

The history policies are the easiest thing to get subtly wrong, and a wrong
policy silently changes what every run measures.
"""

from harnessprobe.providers.base import Message, ToolCall
from harnessprobe.runner import apply_history_policy, build_system_prompt
from harnessprobe.spec import HarnessConfig


def assistant(text="", calls=()):
    return {"role": "assistant", "content": text, "tool_calls": list(calls)}


def tool_result(i, content="output"):
    return {
        "role": "tool",
        "tool_call_id": f"c{i}",
        "name": "read_file",
        "content": content,
    }


def build_history():
    """user, then 8 (assistant, tool) pairs."""
    msgs: list[Message] = [{"role": "user", "content": "do the task"}]
    for i in range(8):
        msgs.append(assistant("", [ToolCall(id=f"c{i}", name="read_file", input={})]))
        msgs.append(tool_result(i))
    return msgs


# -- system prompt --------------------------------------------------------


def test_default_system_prompt_has_no_custom_text():
    cfg = HarnessConfig()
    prompt = build_system_prompt(cfg)
    assert "software engineer" in prompt.lower()


def test_reasoning_hint_toggles():
    on = build_system_prompt(HarnessConfig(reasoning_hint=True))
    off = build_system_prompt(HarnessConfig(reasoning_hint=False))
    assert "state in one sentence" in on
    assert "state in one sentence" not in off
    assert "Do not narrate" in off


def test_custom_system_prompt_replaces_the_default():
    cfg = HarnessConfig(system_prompt="You are a robot.")
    prompt = build_system_prompt(cfg)
    assert prompt.startswith("You are a robot.")
    assert "software engineer" not in prompt


# -- history policies -----------------------------------------------------


def test_full_policy_is_identity():
    msgs = build_history()
    assert apply_history_policy(msgs, HarnessConfig(history_policy="full")) == msgs


def test_drop_policy_keeps_recent_results_intact():
    msgs = build_history()
    out = apply_history_policy(
        msgs, HarnessConfig(history_policy="drop_old_tool_results")
    )
    assert len(out) == len(msgs)
    elided = [m for m in out if m["role"] == "tool" and "elided" in m["content"]]
    assert len(elided) == 4  # 8 results, last 4 preserved
    assert out[-1]["content"] == "output"


def test_drop_policy_never_orphans_a_tool_call_id():
    msgs = build_history()
    out = apply_history_policy(
        msgs, HarnessConfig(history_policy="drop_old_tool_results")
    )
    call_ids = {c.id for m in out if m["role"] == "assistant" for c in m["tool_calls"]}
    result_ids = {m["tool_call_id"] for m in out if m["role"] == "tool"}
    assert result_ids <= call_ids


def test_keep_last_n_preserves_the_task_statement():
    msgs = build_history()
    out = apply_history_policy(
        msgs, HarnessConfig(history_policy="keep_last_n", keep_last_n=4)
    )
    assert out[0]["content"] == "do the task"
    assert len(out) < len(msgs)


def test_keep_last_n_never_starts_on_a_tool_result():
    msgs = build_history()
    out = apply_history_policy(
        msgs, HarnessConfig(history_policy="keep_last_n", keep_last_n=5)
    )
    # After the head user message, the tail must begin with an assistant turn.
    assert out[1]["role"] == "assistant"


def test_keep_last_n_is_a_noop_when_history_is_short():
    msgs = [{"role": "user", "content": "task"}, assistant("done")]
    out = apply_history_policy(
        msgs, HarnessConfig(history_policy="keep_last_n", keep_last_n=10)
    )
    assert out == msgs


def test_unknown_policy_falls_back_to_full():
    msgs = build_history()
    assert apply_history_policy(msgs, HarnessConfig(history_policy="bogus")) == msgs
