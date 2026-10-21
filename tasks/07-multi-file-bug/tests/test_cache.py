"""Tests for cache expiry across modules.

The specification is docs/design.md. These tests encode its three rules:
lazy removal on get, sweeping in size/keys, and reload skipping expired keys.
"""

import time

import pytest

from cache import Cache
from session import Session


def make_fetcher():
    calls = []

    def fetcher(key):
        calls.append(key)
        return f"value-of-{key}"

    return fetcher, calls


def test_expired_entry_is_not_returned():
    cache = Cache()
    cache.set("k", "v", ttl=-1.0)  # already expired
    assert cache.get("k") is None


def test_size_does_not_count_expired_entries():
    cache = Cache()
    cache.set("fresh", "v", ttl=60.0)
    cache.set("stale", "v", ttl=-1.0)
    assert cache.size() == 1


def test_keys_does_not_report_expired_entries():
    cache = Cache()
    cache.set("fresh", "v", ttl=60.0)
    cache.set("stale", "v", ttl=-1.0)
    assert cache.keys() == ["fresh"]


def test_get_removes_the_expired_entry():
    cache = Cache()
    cache.set("k", "v", ttl=-1.0)
    cache.get("k")
    assert cache.keys() == []


def test_reload_skips_expired_keys():
    fetcher, calls = make_fetcher()
    session = Session(fetcher, ttl=60.0)
    session.fetch("a")
    session.fetch("b")
    # Expire only "b".
    session.cache.set("b", "value-of-b", ttl=-1.0)
    refreshed = session.reload()
    assert set(refreshed) == {"a"}
    assert "b" not in calls[2:], f"reload re-fetched an expired key: {calls}"


def test_reload_leaves_expired_entries_out_of_the_cache():
    fetcher, _ = make_fetcher()
    session = Session(fetcher, ttl=60.0)
    session.fetch("a")
    session.cache.set("a", "value-of-a", ttl=-1.0)
    session.reload()
    assert session.cached_count() == 0


def test_reload_refreshes_live_entries():
    fetcher, calls = make_fetcher()
    session = Session(fetcher, ttl=60.0)
    session.fetch("a")
    calls.clear()
    session.reload()
    assert calls == ["a"]


def test_cached_count_after_expiry():
    fetcher, _ = make_fetcher()
    session = Session(fetcher, ttl=60.0)
    session.fetch("a")
    session.fetch("b")
    session.cache.set("a", "value-of-a", ttl=-1.0)
    assert session.cached_count() == 1
