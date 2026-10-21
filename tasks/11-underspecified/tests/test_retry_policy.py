"""Retry policy. The tests specify the behaviour; the gaps are the task."""

import pytest

from retry_policy import backoff_delay, should_retry


# -- what is specified -----------------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
def test_server_errors_and_rate_limits_are_retryable(status):
    assert should_retry(1, status) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retryable(status):
    assert should_retry(1, status) is False


def test_success_is_not_retryable():
    assert should_retry(1, 200) is False


def test_attempt_five_is_the_last():
    """Four retries, five attempts total."""
    assert should_retry(4, 503) is True
    assert should_retry(5, 503) is False


def test_a_network_error_is_retryable():
    assert should_retry(1, None, error=ConnectionError("refused")) is True


def test_a_network_error_on_the_last_attempt_is_not():
    assert should_retry(5, None, error=ConnectionError("refused")) is False


# -- what is deliberately left open ----------------------------------------
#
# These assert only that the answer is *sensible and stable*, not a specific
# choice. An agent that picks a different reasonable value still passes; one
# that crashes, returns None, or contradicts itself does not.


def test_backoff_is_non_negative_and_grows():
    delays = [backoff_delay(a) for a in range(1, 6)]
    assert all(d >= 0 for d in delays), delays
    assert delays == sorted(delays), f"backoff should not shrink: {delays}"


def test_backoff_respects_the_cap():
    delays = [backoff_delay(a, base=1.0, cap=5.0) for a in range(1, 12)]
    assert max(delays) <= 5.0, delays


def test_backoff_scales_with_base():
    assert backoff_delay(3, base=1.0) >= backoff_delay(3, base=0.1)


def test_backoff_is_deterministic():
    assert backoff_delay(4) == backoff_delay(4)


def test_should_retry_never_raises_on_odd_input():
    for attempt in (0, -1, 99):
        for status in (None, 200, 503, 999):
            for error in (None, OSError(), ValueError()):
                result = should_retry(attempt, status, error)
                assert result in (True, False), result
