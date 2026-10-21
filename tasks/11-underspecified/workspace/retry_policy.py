"""Retry policy for outbound requests.

Deliberately under-specified. The requirements live in the tests, and where
the tests are silent, choose the behaviour a reasonable engineer would expect
and say so in a docstring.
"""


def should_retry(attempt, status_code, error=None):
    """Decide whether to retry a request.

    ``attempt`` is 1-based: 1 is the first try.
    """
    raise NotImplementedError


def backoff_delay(attempt, base=0.5, cap=30.0):
    """Seconds to wait before the next attempt."""
    raise NotImplementedError
