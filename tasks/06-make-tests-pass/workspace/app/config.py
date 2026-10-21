"""Application settings."""

import json
import os

_DEFAULTS = {
    "debug": False,
    "retries": 3,
    "endpoint": "https://api.example.com",
}


def load_settings(path=None):
    """Load settings from a JSON file, falling back to defaults.

    Environment variables prefixed with APP_ override the file, e.g.
    APP_RETRIES=5 becomes settings["retries"] = 5.
    """
    settings = dict(_DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            settings.update(json.load(fh))
    for key in list(settings):
        env_key = "APP_" + key.upper()
        if env_key in os.environ:
            settings[key] = os.environ[env_key]
    return settings
