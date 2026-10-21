"""Behaviour must not change; the shared type-check boilerplate must go."""

import inspect

import pytest

import validators

EMAILS = [
    ("a@b.co", True),
    ("plain", False),
    ("@b.co", False),
    ("a@", False),
    ("a@b", False),
    (None, False),
    (42, False),
]

USERNAMES = [
    ("abc", True),
    ("ab", False),
    ("a" * 21, False),
    ("a b", False),
    ("a-b", False),
    (None, False),
]


def test_email_behaviour_is_preserved():
    for value, expected in EMAILS:
        assert validators.validate_email(value) is expected, value


def test_username_behaviour_is_preserved():
    for value, expected in USERNAMES:
        assert validators.validate_username(value) is expected, value


def test_password_behaviour_is_preserved():
    assert validators.validate_password("12345678") is True
    assert validators.validate_password("short") is False
    assert validators.validate_password(None) is False


def test_age_behaviour_is_preserved():
    assert validators.validate_age(20) is True
    assert validators.validate_age(12) is False
    assert validators.validate_age("20") is False


def test_type_guard_is_not_duplicated():
    """The string validators must delegate their isinstance check to one helper.

    validate_age is exempt: it guards on int, not str, so forcing it through
    _is_str would be the wrong abstraction.
    """
    for name in ("validate_email", "validate_username", "validate_password"):
        src = inspect.getsource(getattr(validators, name))
        assert "isinstance(" not in src, (
            f"{name} still does its own isinstance check; the type guard "
            "should be shared"
        )


def test_shared_helper_exists():
    helper = getattr(validators, "_is_str", None)
    assert callable(helper), "expected a shared _is_str(value) type guard"
    assert helper("x") is True
    assert helper(1) is False
    assert helper(None) is False
