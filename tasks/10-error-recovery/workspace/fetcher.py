"""A small HTTP-ish client with retry, for the sync job."""

import time


class FetchError(Exception):
    """Raised when a fetch ultimately fails."""


class TransientError(FetchError):
    """Worth retrying."""


class PermanentError(FetchError):
    """Retrying will not help."""


class Fetcher:
    def __init__(self, transport, max_attempts=3, base_delay=0.01):
        self.transport = transport
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.attempts = 0

    def fetch(self, url):
        """Fetch a URL, retrying transient failures with backoff.

        Raises PermanentError immediately — retrying a permanent failure wastes
        time and can make the outage worse.
        """
        last = None
        for attempt in range(self.max_attempts):
            self.attempts += 1
            try:
                return self.transport(url, attempt)
            except TransientError as exc:
                last = exc
                time.sleep(self.base_delay * (2**attempt))
            except PermanentError:
                raise
        raise TransientError(f"gave up on {url} after {self.max_attempts} attempts: {last}")
