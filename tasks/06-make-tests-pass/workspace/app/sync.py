"""Nightly sync job."""

from .client import Client
from .config import load_settings


def sync(paths, settings=None):
    """Fetch every path, retrying each one independently."""
    settings = settings if settings is not None else load_settings()
    client = Client(settings)
    results = []
    failures = []
    for path in paths:
        try:
            results.append(client.fetch(path))
        except RuntimeError as exc:
            failures.append({"path": path, "error": str(exc)})
    return {"results": results, "failures": failures, "endpoint": client.endpoint}
