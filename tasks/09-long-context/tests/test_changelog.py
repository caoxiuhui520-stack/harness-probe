"""The policy is docs/policy.md; these tests encode every rule in it."""

import pytest

from changelog import collect_entries, is_user_visible, parse_entries, render_release


def entry(kind, text=""):
    return {"kind": kind, "text": text}


# Rule 1
@pytest.mark.parametrize("kind", ["Added", "Fixed"])
def test_added_and_fixed_are_visible(kind):
    assert is_user_visible(entry(kind)) is True


# Rule 2 — the `internal:` prefix overrides rule 1
def test_changed_is_visible_by_default():
    assert is_user_visible(entry("Changed", "faster search")) is True


def test_internal_prefix_hides_a_changed_entry():
    assert is_user_visible(entry("Changed", "internal: bumped a dependency")) is False


def test_internal_prefix_overrides_added_and_fixed():
    assert is_user_visible(entry("Added", "internal: hidden")) is False
    assert is_user_visible(entry("Fixed", "internal: hidden")) is False


# Rule 3
def test_internal_entries_are_never_visible():
    assert is_user_visible(entry("Internal", "refactored the loader")) is False


# Rule 4 — `chore:` overrides everything
def test_chore_prefix_is_never_visible():
    assert is_user_visible(entry("Added", "chore: nothing to see")) is False
    assert is_user_visible(entry("Changed", "chore: nothing to see")) is False
    assert is_user_visible(entry("Internal", "chore: nothing to see")) is False


# Rule 5
def test_unknown_kinds_follow_the_prefix_rules():
    assert is_user_visible(entry("Deprecated", "old API")) is True
    assert is_user_visible(entry("Removed", "internal: hidden")) is False
    assert is_user_visible(entry("Removed", "chore: hidden")) is False


# Rendering
def test_render_groups_and_orders_sections():
    entries = [
        entry("Fixed", "a crash on save"),
        entry("Added", "dark mode"),
        entry("Changed", "faster startup"),
    ]
    out = render_release(entries, "1.2.0")
    assert out.index("### Added") < out.index("### Changed") < out.index("### Fixed")


def test_render_omits_empty_sections():
    out = render_release([entry("Added", "one thing")], "1.0.0")
    assert "### Changed" not in out
    assert "### Fixed" not in out


def test_render_drops_internal_entries():
    entries = [
        entry("Added", "visible"),
        entry("Internal", "hidden"),
        entry("Changed", "internal: also hidden"),
        entry("Fixed", "chore: hidden too"),
    ]
    out = render_release(entries, "2.0.0")
    assert "visible" in out
    assert "hidden" not in out


# Fragments
def test_parse_entries_preserves_order():
    text = "- Added: one\n- Fixed: two\n- Internal: three\n"
    assert [e["kind"] for e in parse_entries(text)] == ["Added", "Fixed", "Internal"]


def test_collect_entries_reads_fragments_in_order(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("- Added: first\n", encoding="utf-8")
    b.write_text("- Fixed: second\n", encoding="utf-8")
    entries = collect_entries([str(a), str(b)])
    assert [e["text"] for e in entries] == ["first", "second"]


def test_collect_entries_errors_on_a_missing_fragment(tmp_path):
    with pytest.raises(FileNotFoundError):
        collect_entries([str(tmp_path / "nope.md")])
