from datetime import datetime, timedelta, timezone

import pytest

from app.domain.shift import InvalidShift, InvalidShiftAssignment, Shift, ShiftAssignment


def instant(day, hour, *, tz=timezone.utc):
    return datetime(2026, 10, day, hour, tzinfo=tz)


def make_shift(**overrides):
    values = {
        "company_id": "company-1",
        "start_at": instant(1, 9),
        "end_at": instant(1, 12),
    }
    values.update(overrides)
    return Shift.create(**values)


def test_shift_normalizes_offset_timestamps_to_utc():
    plus_two = timezone(timedelta(hours=2))

    shift = make_shift(
        start_at=instant(1, 11, tz=plus_two),
        end_at=instant(1, 14, tz=plus_two),
    )

    assert shift.start_at == instant(1, 9)
    assert shift.end_at == instant(1, 12)
    assert shift.start_at.tzinfo is timezone.utc
    assert shift.end_at.tzinfo is timezone.utc


@pytest.mark.parametrize(
    ("start_at", "end_at"),
    [
        (datetime(2026, 10, 1, 9), instant(1, 12)),
        (instant(1, 9), datetime(2026, 10, 1, 12)),
        (instant(1, 12), instant(1, 12)),
        (instant(1, 13), instant(1, 12)),
    ],
)
def test_shift_rejects_naive_or_nonpositive_intervals(start_at, end_at):
    with pytest.raises(InvalidShift):
        make_shift(start_at=start_at, end_at=end_at)


def test_shift_accepts_overnight_interval():
    shift = make_shift(start_at=instant(1, 23), end_at=instant(2, 1))

    assert shift.end_at > shift.start_at


def test_shift_rejects_missing_company_identity():
    with pytest.raises(InvalidShift):
        make_shift(company_id=" ")


def test_shift_intervals_allow_adjacency_and_detect_overlap():
    first = make_shift(start_at=instant(1, 9), end_at=instant(1, 12))
    overlapping = make_shift(start_at=instant(1, 11), end_at=instant(1, 13))
    adjacent = make_shift(start_at=instant(1, 12), end_at=instant(1, 14))

    assert first.overlaps(overlapping)
    assert not first.overlaps(adjacent)


def test_assignment_requires_nonempty_shift_company_and_employee_ids():
    with pytest.raises(InvalidShiftAssignment):
        ShiftAssignment.create(shift_id="", company_id="company-1", employee_id="user-1")
    with pytest.raises(InvalidShiftAssignment):
        ShiftAssignment.create(shift_id="shift-1", company_id=" ", employee_id="user-1")
    with pytest.raises(InvalidShiftAssignment):
        ShiftAssignment.create(shift_id="shift-1", company_id="company-1", employee_id="")


def test_shift_accepts_a_matching_assignment_once():
    shift = make_shift()
    assignment = ShiftAssignment.create(
        shift_id=shift.id,
        company_id=shift.company_id,
        employee_id="employee-1",
    )

    assigned = shift.with_assignment(assignment)

    assert assigned.assignment == assignment
    with pytest.raises(InvalidShiftAssignment):
        assigned.with_assignment(assignment)


@pytest.mark.parametrize(
    ("shift_id", "company_id"),
    [("other-shift", "company-1"), ("shift-1", "other-company")],
)
def test_shift_rejects_assignment_for_different_shift_or_company(
    shift_id, company_id
):
    shift = make_shift(id="shift-1")
    assignment = ShiftAssignment.create(
        shift_id=shift_id,
        company_id=company_id,
        employee_id="employee-1",
    )

    with pytest.raises(InvalidShiftAssignment):
        shift.with_assignment(assignment)
