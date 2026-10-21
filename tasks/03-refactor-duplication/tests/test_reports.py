import inspect

import reports

ROWS = [{"name": "alpha", "value": 1}, {"name": "beta", "value": 22}]

# The exact rendering the three formatters must keep producing.
EXPECTED_USER = (
    "=" * 40 + "\n"
    "USER REPORT\n"
    + "=" * 40 + "\n"
    "alpha                         1\n"
    "beta                         22\n"
    + "-" * 40 + "\n"
    "total: 2"
)


def test_output_is_unchanged():
    assert reports.format_user_report(ROWS) == EXPECTED_USER


def test_titles_differ_only_in_the_title_line():
    user = reports.format_user_report(ROWS)
    order = reports.format_order_report(ROWS)
    assert order == user.replace("USER REPORT", "ORDER REPORT")
    assert reports.format_audit_report(ROWS) == user.replace("USER REPORT", "AUDIT REPORT")


def test_every_report_shares_one_implementation():
    """The three formatters must delegate to a common helper, not copy-paste."""
    sources = [
        inspect.getsource(reports.format_user_report),
        inspect.getsource(reports.format_order_report),
        inspect.getsource(reports.format_audit_report),
    ]
    for src in sources:
        assert "for row in rows" not in src, "the table loop is still duplicated"
        assert '"-" * 40' not in src, "the footer is still duplicated"


def test_helper_exists_and_is_parameterised():
    helper = getattr(reports, "format_report", None)
    assert helper is not None, "expected a shared format_report(rows, title) helper"
    out = helper(ROWS, "CUSTOM")
    assert "CUSTOM" in out
    assert out == EXPECTED_USER.replace("USER REPORT", "CUSTOM")


def test_empty_rows():
    assert reports.format_order_report([]).endswith("total: 0")
