import pytest

from envparse import parse, parse_line


def test_plain_pair():
    assert parse_line("KEY=value") == ("KEY", "value")


def test_blank_and_comment_lines():
    assert parse_line("") is None
    assert parse_line("   ") is None
    assert parse_line("# a comment") is None


def test_inline_comment_after_unquoted_value():
    assert parse_line("KEY=value  # trailing note") == ("KEY", "value")


def test_double_quoted_value_is_unwrapped():
    assert parse_line('KEY="hello world"') == ("KEY", "hello world")


def test_single_quoted_value_is_unwrapped():
    assert parse_line("KEY='hello world'") == ("KEY", "hello world")


def test_hash_inside_quotes_is_preserved():
    """A '#' inside quotes is data, not a comment."""
    assert parse_line('PASSWORD="ab#cd"') == ("PASSWORD", "ab#cd")
    assert parse_line("COLOR='#ff0000'") == ("COLOR", "#ff0000")


def test_spaces_inside_quotes_are_preserved():
    assert parse_line('GREETING="  padded  "') == ("GREETING", "  padded  ")


def test_empty_value():
    assert parse_line("EMPTY=") == ("EMPTY", "")


def test_missing_equals_raises():
    with pytest.raises(ValueError):
        parse_line("JUSTAWORD")


def test_parse_document():
    text = "\n".join(
        [
            "# app config",
            "HOST=localhost",
            "URL='https://example.com/#anchor'",
            "",
            "TOKEN=\"s3cr#t\"  # rotate quarterly",
        ]
    )
    assert parse(text) == {
        "HOST": "localhost",
        "URL": "https://example.com/#anchor",
        "TOKEN": "s3cr#t",
    }
