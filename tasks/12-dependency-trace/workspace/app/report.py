"""Reporting over the store."""

from app.store import Store


def build_report(store: Store) -> "Report":
    return Report(store)


class Report:
    def __init__(self, store: Store):
        self.store = store

    def summary(self):
        total = len(self.store.names())
        return f"{total} items, {self.store.total_size()} bytes"
