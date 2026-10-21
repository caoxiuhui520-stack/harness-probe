"""Page a list of records for the web UI."""


def paginate(items, page, per_page):
    """Return the slice of ``items`` belonging to the 1-based ``page``."""
    start = page * per_page
    return items[start : start + per_page]


def page_count(items, per_page):
    """Number of pages needed to show every item."""
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return (len(items) + per_page - 1) // per_page
