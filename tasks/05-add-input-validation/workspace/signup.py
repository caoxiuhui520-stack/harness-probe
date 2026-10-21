"""Signup form handling."""


class ValidationError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def validate_signup(payload):
    """Validate a signup payload and return a normalised copy."""
    return {
        "username": payload["username"],
        "email": payload["email"],
        "age": payload["age"],
    }
