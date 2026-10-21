import json

import pytest

from app.client import Client
from app.config import load_settings
from app.sync import sync


def test_env_override_is_coerced_to_the_default_type(monkeypatch):
    """APP_RETRIES=5 must produce the int 5, not the string "5"."""
    monkeypatch.setenv("APP_RETRIES", "5")
    settings = load_settings()
    assert settings["retries"] == 5
    assert isinstance(settings["retries"], int)


def test_env_override_handles_booleans(monkeypatch):
    monkeypatch.setenv("APP_DEBUG", "true")
    assert load_settings()["debug"] is True


def test_env_override_keeps_strings(monkeypatch):
    monkeypatch.setenv("APP_ENDPOINT", "http://localhost:9000")
    assert load_settings()["endpoint"] == "http://localhost:9000"


def test_unknown_env_override_is_ignored(monkeypatch):
    monkeypatch.setenv("APP_NOT_A_SETTING", "x")
    assert "not_a_setting" not in load_settings()


def test_file_values_win_over_defaults(tmp_path):
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"retries": 7}), encoding="utf-8")
    assert load_settings(str(cfg))["retries"] == 7


def test_sync_retries_each_path_independently():
    """One failing path must not consume the retry budget of the next."""
    settings = {"debug": False, "retries": 2, "endpoint": "http://x"}
    out = sync(["a", "b", "c"], settings=settings)
    assert len(out["results"]) == 3
    assert [r["attempt"] for r in out["results"]] == [1, 1, 1]
    assert out["failures"] == []


def test_sync_reports_failures_without_aborting():
    settings = {"debug": False, "retries": 0, "endpoint": "http://x"}
    out = sync(["a"], settings=settings)
    assert out["results"] == []
    assert len(out["failures"]) == 1
    assert out["failures"][0]["path"] == "a"


def test_client_uses_injected_settings():
    client = Client({"debug": False, "retries": 1, "endpoint": "http://y"})
    assert client.endpoint == "http://y"
    assert client.max_attempts() == 1
