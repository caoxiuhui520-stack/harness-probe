"""The defect is in the retry loop's exception handling."""

import time

import pytest

from fetcher import (
    FetchError,
    Fetcher,
    PermanentError,
    TransientError,
)


def always_transient(url, attempt):
    raise TransientError(f"attempt {attempt} failed")


def succeed_on_third(url, attempt):
    if attempt < 2:
        raise TransientError("not yet")
    return f"ok after {attempt}"


def always_permanent(url, attempt):
    raise PermanentError("404")


def test_retries_transient_until_success():
    f = Fetcher(succeed_on_third, max_attempts=5)
    assert f.fetch("/x") == "ok after 2"
    assert f.attempts == 3


def test_gives_up_after_max_attempts():
    f = Fetcher(always_transient, max_attempts=3)
    with pytest.raises(FetchError):
        f.fetch("/x")
    assert f.attempts == 3


def test_permanent_error_is_not_retried():
    """A permanent failure must surface on the first attempt."""
    f = Fetcher(always_permanent, max_attempts=5)
    with pytest.raises(PermanentError):
        f.fetch("/x")
    assert f.attempts == 1


def test_permanent_error_does_not_sleep():
    """Retrying a permanent error also wastes wall-clock time."""
    import time

    f = Fetcher(always_permanent, max_attempts=3, base_delay=0.5)
    started = time.time()
    with pytest.raises(PermanentError):
        f.fetch("/x")
    assert time.time() - started < 0.4


def test_backoff_grows_between_attempts():
    delays = []
    f = Fetcher(always_transient, max_attempts=3, base_delay=0.01)
    real_sleep = time.sleep
    import fetcher as mod

    mod.time.sleep = lambda s: delays.append(s)
    try:
        with pytest.raises(FetchError):
            f.fetch("/x")
    finally:
        mod.time.sleep = real_sleep
    assert delays == sorted(delays) and len(delays) == 2


def test_success_needs_no_retry():
    f = Fetcher(lambda url, attempt: "ok", max_attempts=3)
    assert f.fetch("/x") == "ok"
    assert f.attempts == 1
