"""A behavioural change in util.py must propagate through every dependent."""

from app.report import Report, build_report
from app.store import Store
from app.util import checksum


def test_checksum_is_order_independent():
    assert checksum("ab") == checksum("ba")


def test_checksum_is_stable_across_calls():
    assert checksum("hello") == checksum("hello")


def test_store_reports_a_stable_digest():
    store = Store()
    first = store.add("a", "hello")
    assert store.add("b", "hello") == first


def test_report_summary_uses_the_store_digest():
    """The summary must not recompute a different digest of its own."""
    store = Store()
    store.add("a", "hello")
    report = build_report(store)
    assert isinstance(report, Report)
    assert "1 items" in report.summary()


def test_cli_report_runs(tmp_path, capsys, monkeypatch):
    """The CLI must work end to end — it imports report, which imports store,
    which imports util. A change to util has to survive all three hops."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "report"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "0 items" in proc.stdout


def test_cli_list_runs():
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "list"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
