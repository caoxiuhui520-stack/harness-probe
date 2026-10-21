from paginate import page_count, paginate

ITEMS = list(range(1, 26))  # 25 records


def test_first_page_starts_at_zero():
    assert paginate(ITEMS, 1, 10) == list(range(1, 11))


def test_second_page_is_the_next_slice():
    assert paginate(ITEMS, 2, 10) == list(range(11, 21))


def test_last_page_is_partial():
    assert paginate(ITEMS, 3, 10) == list(range(21, 26))


def test_page_past_the_end_is_empty():
    assert paginate(ITEMS, 9, 10) == []


def test_page_count_rounds_up():
    assert page_count(ITEMS, 10) == 3
    assert page_count(ITEMS, 5) == 5
    assert page_count([], 10) == 0


def test_pages_cover_every_item_exactly_once():
    seen = []
    for page in range(1, page_count(ITEMS, 10) + 1):
        seen.extend(paginate(ITEMS, page, 10))
    assert seen == ITEMS
