"""Minimal .env file parser.

Supports:
  KEY=value
  KEY="quoted value"
  KEY='single quoted'
  # full-line comments
  inline comments after an unquoted value
"""


def parse_line(line):
    """Parse one line. Returns (key, value) or None for blanks and comments."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    # Strip inline comments.
    if "#" in stripped:
        stripped = stripped.split("#", 1)[0].rstrip()

    if "=" not in stripped:
        raise ValueError(f"not a KEY=value line: {line!r}")

    key, _, value = stripped.partition("=")
    key = key.strip()
    value = value.strip()

    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]

    if not key:
        raise ValueError(f"empty key in line: {line!r}")
    return key, value


def parse(text):
    """Parse a whole .env document into a dict."""
    out = {}
    for raw in text.splitlines():
        parsed = parse_line(raw)
        if parsed is None:
            continue
        key, value = parsed
        out[key] = value
    return out
