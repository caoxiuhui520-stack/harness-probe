"""A session that caches fetched records."""

import time

from cache import Cache


class Session:
    def __init__(self, fetcher, ttl=60.0):
        self.cache = Cache(default_ttl=ttl)
        self._fetcher = fetcher

    def fetch(self, key):
        """Return the cached value, or fetch and cache it."""
        value = self.cache.get(key)
        if value is not None:
            return value
        value = self._fetcher(key)
        self.cache.set(key, value)
        return value

    def reload(self):
        """Re-read every key currently in the cache."""
        refreshed = {}
        for key in self.cache.keys():
            value = self.cache.get(key)
            if value is not None:
                refreshed[key] = self._fetcher(key)
                self.cache.set(key, refreshed[key])
        return refreshed

    def cached_count(self):
        return self.cache.size()
