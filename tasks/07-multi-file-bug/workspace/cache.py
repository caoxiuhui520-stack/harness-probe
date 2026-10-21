"""In-memory cache with a TTL.

The eviction policy is documented in docs/design.md — read it, it is the
specification for how expiry is supposed to work.
"""

import time


class Cache:
    def __init__(self, default_ttl=60.0):
        self.default_ttl = default_ttl
        self._store = {}

    def set(self, key, value, ttl=None):
        self._store[key] = (value, time.time() + (ttl or self.default_ttl))

    def get(self, key):
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def size(self):
        return len(self._store)

    def keys(self):
        return list(self._store)
