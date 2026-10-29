from datetime import datetime, timedelta, timezone

import pytest

from app.domain.availability import Availability, InvalidAvailability


def instant(hour, *, tz=timezone.utc, day=1):
    return datetime(2026, 10, day, hour, tzinfo=tz)


def make_availability(**overrides):
    values = {
        "company_id": "company-1",
        "employee_id": "employee-1",
        "start_at": instant(9),
        "end_at": instant(12),
    }
    values.update(overrides)
    return Availability.create(**values)


def test_availability_normalizes_aware_timestamps_to_utc():
    plus_two = timezone(timedelta(hours=2))

    availability = make_availability(
        start_at=datetime(2026, 10, 1, 11, tzinfo=plus_two),
        end_at=datetime(2026, 10, 1, 14, tzinfo=plus_two),
    )

    assert availability.start_at == instant(9)
    assert availability.end_at == instant(12)
    assert availability.start_at.tzinfo is timezone.utc
    assert availability.end_at.tzinfo is timezone.utc


@pytest.mark.parametrize(
    ("start_at", "end_at"),
    [
        (datetime(2026, 10, 1, 9), instant(12)),
        (instant(9), datetime(2026, 10, 1, 12)),
        (instant(12), instant(12)),
        (instant(13), instant(12)),
    ],
)
def test_availability_rejects_naive_or_nonpositive_intervals(start_at, end_at):
    with pytest.raises(InvalidAvailability):
        make_availability(start_at=start_at, end_at=end_at)


def test_availability_rejects_timestamp_values_without_datetime_offsets():
    with pytest.raises(InvalidAvailability) as error:
        make_availability(start_at="2026-10-01T09:00:00Z")

    assert error.value.code == "INVALID_AVAILABILITY"


def test_availability_allows_an_interval_that_crosses_midnight():
    availability = make_availability(
        start_at=datetime(2026, 10, 1, 23, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 2, 1, tzinfo=timezone.utc),
    )

    assert availability.end_at > availability.start_at


@pytest.mark.parametrize(
    ("company_id", "employee_id"),
    [("", "employee-1"), ("company-1", " \t")],
)
def test_availability_requires_company_and_employee_identity(
    company_id, employee_id
):
    with pytest.raises(InvalidAvailability):
        make_availability(company_id=company_id, employee_id=employee_id)


def test_availability_overlap_uses_half_open_intervals():
    first = make_availability(start_at=instant(9), end_at=instant(12))
    overlapping = make_availability(start_at=instant(11), end_at=instant(13))
    adjacent = make_availability(start_at=instant(12), end_at=instant(14))

    assert first.overlaps(overlapping)
    assert not first.overlaps(adjacent)


def test_interval_update_preserves_availability_identity():
    availability = make_availability()

    updated = availability.with_interval(instant(10), instant(13))

    assert updated.id == availability.id
    assert updated.company_id == availability.company_id
    assert updated.employee_id == availability.employee_id
    assert updated.start_at == instant(10)
    assert updated.end_at == instant(13)
