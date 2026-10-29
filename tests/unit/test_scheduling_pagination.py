from datetime import datetime, timedelta, timezone

import pytest

from app.interfaces.pagination import (
    InvalidSchedulingQuery,
    decode_scheduling_cursor,
    encode_scheduling_cursor,
    parse_scheduling_window,
)


def test_scheduling_window_requires_offset_and_maximum_31_days():
    start, end = parse_scheduling_window(
        "2026-10-01T10:00:00+02:00",
        "2026-11-01T08:00:00Z",
    )

    assert start == datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
    assert end - start == timedelta(days=31)
    with pytest.raises(InvalidSchedulingQuery):
        parse_scheduling_window(
            "2026-10-01T10:00:00",
            "2026-10-02T10:00:00Z",
        )
    with pytest.raises(InvalidSchedulingQuery):
        parse_scheduling_window(
            "2026-10-01T00:00:00Z",
            "2026-11-01T00:00:01Z",
        )


def test_scheduling_cursor_is_bound_to_all_query_context():
    start, end = parse_scheduling_window(
        "2026-10-01T00:00:00Z",
        "2026-10-02T00:00:00Z",
    )
    item_start = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    cursor = encode_scheduling_cursor(
        endpoint="company_shifts",
        user_id="account-a",
        company_id="company-a",
        from_at=start,
        to_at=end,
        limit=10,
        last_start=item_start,
        last_id="00000000-0000-4000-8000-000000000001",
    )
    context = {
        "endpoint": "company_shifts",
        "user_id": "account-a",
        "company_id": "company-a",
        "from_at": start,
        "to_at": end,
        "limit": 10,
    }

    assert decode_scheduling_cursor(cursor, **context) == (
        item_start,
        "00000000-0000-4000-8000-000000000001",
    )
    for key, value in (
        ("endpoint", "employee_shifts"),
        ("user_id", "account-b"),
        ("company_id", "company-b"),
        ("from_at", start - timedelta(days=1)),
        ("limit", 20),
    ):
        changed_context = {**context, key: value}
        with pytest.raises(InvalidSchedulingQuery):
            decode_scheduling_cursor(cursor, **changed_context)
    with pytest.raises(InvalidSchedulingQuery):
        decode_scheduling_cursor("not-a-cursor", **context)
