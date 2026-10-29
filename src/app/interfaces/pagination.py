import base64
import binascii
import hashlib
import json
from datetime import datetime, timedelta, timezone

from app.application.scheduling_queries import InvalidSchedulingQuery
from app.interfaces.timestamps import parse_utc_timestamp


_CURSOR_VERSION = 1
_MAX_CURSOR_LENGTH = 2048
_MAX_WINDOW = timedelta(days=31)


def parse_scheduling_window(
    from_value: str,
    to_value: str,
) -> tuple[datetime, datetime]:
    try:
        start = parse_utc_timestamp(from_value, InvalidSchedulingQuery)
        end = parse_utc_timestamp(to_value, InvalidSchedulingQuery)
    except InvalidSchedulingQuery:
        raise
    if end <= start or end - start > _MAX_WINDOW:
        raise InvalidSchedulingQuery
    return start, end


def encode_scheduling_cursor(
    *,
    endpoint: str,
    user_id: str,
    company_id: str,
    from_at: datetime,
    to_at: datetime,
    limit: int,
    last_start: datetime,
    last_id: str,
) -> str:
    payload = {
        "v": _CURSOR_VERSION,
        "e": endpoint,
        "p": hashlib.sha256(user_id.encode("utf-8")).hexdigest(),
        "c": company_id,
        "f": _format_timestamp(from_at),
        "t": _format_timestamp(to_at),
        "l": limit,
        "s": _format_timestamp(last_start),
        "i": last_id,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    if len(encoded) > _MAX_CURSOR_LENGTH:
        raise InvalidSchedulingQuery
    return encoded


def decode_scheduling_cursor(
    cursor: str,
    *,
    endpoint: str,
    user_id: str,
    company_id: str,
    from_at: datetime,
    to_at: datetime,
    limit: int,
) -> tuple[datetime, str]:
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
        raise InvalidSchedulingQuery
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != cursor:
            raise ValueError
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {
            "v", "e", "p", "c", "f", "t", "l", "s", "i"
        }:
            raise ValueError
        if json.dumps(
            payload, separators=(",", ":"), sort_keys=True
        ).encode("utf-8") != raw:
            raise ValueError
        if (
            type(payload["v"]) is not int
            or payload["v"] != _CURSOR_VERSION
            or payload["e"] != endpoint
            or payload["p"] != hashlib.sha256(user_id.encode("utf-8")).hexdigest()
            or payload["c"] != company_id
            or payload["f"] != _format_timestamp(from_at)
            or payload["t"] != _format_timestamp(to_at)
            or type(payload["l"]) is not int
            or payload["l"] != limit
            or not isinstance(payload["s"], str)
            or not isinstance(payload["i"], str)
            or not payload["i"].strip()
            or len(payload["i"]) > 36
        ):
            raise ValueError
        last_start = parse_utc_timestamp(payload["s"], InvalidSchedulingQuery)
        if _format_timestamp(last_start) != payload["s"]:
            raise ValueError
        if last_start >= to_at:
            raise ValueError
        return last_start, payload["i"]
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        InvalidSchedulingQuery,
        ValueError,
        TypeError,
    ):
        raise InvalidSchedulingQuery from None


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidSchedulingQuery
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
