import re
from datetime import datetime, timezone
from typing import TypeVar


_RFC3339_OFFSET_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:\d{2})\Z"
)
_TimestampError = TypeVar("_TimestampError", bound=ValueError)


def parse_utc_timestamp(
    value: str, error_type: type[_TimestampError]
) -> datetime:
    if not isinstance(value, str) or not _RFC3339_OFFSET_TIMESTAMP.fullmatch(value):
        raise error_type("Timestamp must be RFC 3339 with an explicit UTC offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("Timestamp must include a UTC offset")
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError) as error:
        raise error_type("Timestamp is invalid or outside the supported range") from error
