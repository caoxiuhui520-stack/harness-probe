"""Input validation for the signup flow."""


def validate_email(value):
    if not isinstance(value, str):
        return False
    if "@" not in value:
        return False
    local, _, domain = value.partition("@")
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    return True


def validate_username(value):
    if not isinstance(value, str):
        return False
    if len(value) < 3 or len(value) > 20:
        return False
    for ch in value:
        if not (ch.isalnum() or ch == "_"):
            return False
    return True


def validate_password(value):
    if not isinstance(value, str):
        return False
    if len(value) < 8:
        return False
    return True


def validate_age(value):
    if not isinstance(value, int):
        return False
    return 13 <= value <= 130
