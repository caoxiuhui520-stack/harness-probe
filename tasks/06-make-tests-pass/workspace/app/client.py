"""HTTP client used by the sync job."""

from .config import load_settings


class Client:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else load_settings()
        self.attempts = 0

    @property
    def endpoint(self):
        return self.settings["endpoint"]

    def max_attempts(self):
        return int(self.settings["retries"])

    def fetch(self, path):
        """Pretend to fetch ``path`` and return a payload.

        Real transport is out of scope; the retry accounting is what the sync
        job depends on.
        """
        self.attempts += 1
        if self.attempts > self.max_attempts():
            raise RuntimeError(f"gave up after {self.max_attempts()} attempts")
        return {"path": path, "endpoint": self.endpoint, "attempt": self.attempts}
