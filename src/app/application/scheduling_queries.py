from datetime import datetime, timedelta, timezone


class InvalidSchedulingQuery(ValueError):
    pass


def validate_scheduling_window(
    from_at: datetime,
    to_at: datetime,
) -> tuple[datetime, datetime]:
    if (
        from_at.tzinfo is None
        or from_at.utcoffset() is None
        or to_at.tzinfo is None
        or to_at.utcoffset() is None
    ):
        raise InvalidSchedulingQuery
    start = from_at.astimezone(timezone.utc)
    end = to_at.astimezone(timezone.utc)
    if end <= start or end - start > timedelta(days=31):
        raise InvalidSchedulingQuery
    return start, end
