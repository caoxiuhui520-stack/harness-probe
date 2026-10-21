"""Release-note assembly.

A release is built from merged change entries. The rules for what counts as
"user-visible" are in docs/policy.md, and they are the part people get wrong.
"""

import re

_ENTRY = re.compile(r"^- (?P<kind>\w+): (?P<text>.+)$", re.MULTILINE)


def parse_entries(text):
    """Return the change entries in a changelog fragment, in file order."""
    return [
        {"kind": m.group("kind"), "text": m.group("text")}
        for m in _ENTRY.finditer(text)
    ]


def collect_entries(paths):
    """Read every fragment and concatenate its entries in path order."""
    entries = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            entries.extend(parse_entries(fh.read()))
    return entries


def render_release(entries, version):
    """Render a release note: only user-visible entries, grouped by kind."""
    lines = [f"## {version}", ""]
    for kind in ("Added", "Changed", "Fixed"):
        visible = [
            e for e in entries
            if e["kind"] == kind and is_user_visible(e)
        ]
        if visible:
            lines.append(f"### {kind}")
            lines.append("")
            lines.extend(f"- {e['text']}" for e in visible)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def is_user_visible(entry):
    """Decide whether an entry belongs in the release note.

    See docs/policy.md for the rules.
    """
    return entry["kind"] in ("Added", "Changed", "Fixed")
