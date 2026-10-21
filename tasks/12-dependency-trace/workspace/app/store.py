"""The store. Everything else depends on this module."""

from app.util import checksum


class Store:
    def __init__(self):
        self._items = {}

    def add(self, name, data):
        self._items[name] = checksum(data)
        return self._items[name]

    def names(self):
        return sorted(self._items)

    def total_size(self):
        return len(self._items)

    def get(self, name):
        return self._items.get(name)
