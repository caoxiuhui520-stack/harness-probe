"""Command-line entry point."""

from app.report import build_report
from app.store import Store


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["report", "list"])
    args = parser.parse_args(argv)

    store = Store()
    if args.command == "list":
        for name in store.names():
            print(name)
        return 0

    report = build_report(store)
    print(report.summary())
    return 0
