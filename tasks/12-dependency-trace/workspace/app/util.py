"""Shared helpers."""


def checksum(data):
    """A stable, order-independent digest of the data."""
    return sum(bytearray(data.encode("utf-8"))) % 100000
