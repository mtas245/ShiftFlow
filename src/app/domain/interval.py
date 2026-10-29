from datetime import datetime, timezone


def normalize_utc_interval(
    start_at: datetime,
    end_at: datetime,
    error_type: type[ValueError],
) -> tuple[datetime, datetime]:
    normalized = []
    for value, field_name in ((start_at, "start_at"), (end_at, "end_at")):
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise error_type(f"{field_name} must include a UTC offset")
        try:
            normalized.append(value.astimezone(timezone.utc))
        except (OverflowError, ValueError) as error:
            raise error_type(
                f"{field_name} is outside the supported range"
            ) from error

    normalized_start, normalized_end = normalized
    if normalized_end <= normalized_start:
        raise error_type("end_at must be later than start_at")
    return normalized_start, normalized_end
