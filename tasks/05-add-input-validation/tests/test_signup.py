import pytest

from signup import ValidationError, validate_signup

GOOD = {"username": "ada_l", "email": "ada@example.com", "age": 36}


def test_valid_payload_passes_through():
    assert validate_signup(GOOD) == GOOD


def test_username_is_lowercased_and_trimmed():
    out = validate_signup({**GOOD, "username": "  Ada_L  "})
    assert out["username"] == "ada_l"


def test_email_is_lowercased():
    out = validate_signup({**GOOD, "email": "Ada@Example.COM"})
    assert out["email"] == "ada@example.com"


def test_age_is_coerced_from_string():
    out = validate_signup({**GOOD, "age": "36"})
    assert out["age"] == 36


@pytest.mark.parametrize(
    "payload,field",
    [
        ({**GOOD, "username": "ab"}, "username"),
        ({**GOOD, "username": "has space"}, "username"),
        ({**GOOD, "username": "x" * 33}, "username"),
        ({**GOOD, "email": "not-an-email"}, "email"),
        ({**GOOD, "email": "a@b"}, "email"),
        ({**GOOD, "age": 12}, "age"),
        ({**GOOD, "age": "abc"}, "age"),
    ],
)
def test_invalid_field_raises_with_that_field(payload, field):
    with pytest.raises(ValidationError) as exc:
        validate_signup(payload)
    assert field in exc.value.errors


def test_missing_field_is_reported_not_keyerror():
    with pytest.raises(ValidationError) as exc:
        validate_signup({"username": "ada_l"})
    assert "email" in exc.value.errors
    assert "age" in exc.value.errors


def test_all_errors_are_collected_at_once():
    with pytest.raises(ValidationError) as exc:
        validate_signup({"username": "ab", "email": "bad", "age": 5})
    assert set(exc.value.errors) == {"username", "email", "age"}


def test_username_allows_letters_digits_underscore_hyphen():
    for name in ["ada_l", "ada-l", "ada123", "a" * 32]:
        assert validate_signup({**GOOD, "username": name})["username"] == name
