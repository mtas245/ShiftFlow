import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable

from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.application.account_lifecycle import RateLimiterUnavailable
from app.infrastructure.persistence.models import RateLimitBucketRecord


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    limit: int
    window: timedelta


RATE_LIMIT_POLICIES = {
    "register:email": RateLimitPolicy(3, timedelta(days=1)),
    "register:ip": RateLimitPolicy(10, timedelta(hours=1)),
    "login:email": RateLimitPolicy(5, timedelta(minutes=15)),
    "login:ip": RateLimitPolicy(20, timedelta(minutes=15)),
    "verify_email:email": RateLimitPolicy(3, timedelta(hours=1)),
    "verify_email:ip": RateLimitPolicy(10, timedelta(hours=1)),
    "reset_password:email": RateLimitPolicy(3, timedelta(hours=1)),
    "reset_password:ip": RateLimitPolicy(10, timedelta(hours=1)),
}

_CLEANUP_INTERVAL_SECONDS = 3600


class PostgresRateLimiter:
    def __init__(
        self,
        engine: Engine,
        *,
        hmac_secret: str,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL rate limiting requires PostgreSQL")
        secret_bytes = hmac_secret.encode("utf-8")
        if len(secret_bytes) < 32:
            raise ValueError("Rate-limit HMAC secret must contain at least 32 bytes")
        self._engine = engine
        self._hmac_secret = secret_bytes
        self._clock = clock
        self._max_window = max(policy.window for policy in RATE_LIMIT_POLICIES.values())
        self._cleanup_lock = Lock()
        self._last_cleanup: float | None = None

    def allow(self, scope: str, key: str) -> bool:
        policy = RATE_LIMIT_POLICIES.get(scope)
        if policy is None:
            raise ValueError("Unknown rate-limit scope")
        if not isinstance(key, str) or not key or len(key) > 512:
            raise ValueError("Rate-limit keys must be non-empty strings")

        now = self._now_utc()
        self._cleanup_if_due()

        key_digest = hmac.new(
            self._hmac_secret,
            f"{scope}\0{key}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        bucket = RateLimitBucketRecord.__table__
        expired = bucket.c.window_started_at <= now - policy.window
        next_count = case(
            (expired, 1),
            (bucket.c.request_count < policy.limit + 1, bucket.c.request_count + 1),
            else_=policy.limit + 1,
        )
        statement = (
            insert(RateLimitBucketRecord)
            .values(
                scope=scope,
                key_digest=key_digest,
                window_started_at=now,
                request_count=1,
            )
            .on_conflict_do_update(
                index_elements=[
                    RateLimitBucketRecord.scope,
                    RateLimitBucketRecord.key_digest,
                ],
                set_={
                    "window_started_at": case(
                        (expired, now),
                        else_=bucket.c.window_started_at,
                    ),
                    "request_count": next_count,
                },
            )
            .returning(RateLimitBucketRecord.request_count)
        )

        try:
            with self._engine.begin() as connection:
                count = connection.scalar(statement)
        except SQLAlchemyError:
            raise RateLimiterUnavailable from None
        return count is not None and count <= policy.limit

    def cleanup_expired(self) -> int:
        cutoff = self._now_utc() - self._max_window
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    delete(RateLimitBucketRecord).where(
                        RateLimitBucketRecord.window_started_at < cutoff
                    )
                )
                return result.rowcount or 0
        except SQLAlchemyError:
            raise RateLimiterUnavailable from None

    def _now_utc(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Rate-limit clock must return an offset-aware datetime")
        return now.astimezone(timezone.utc)

    def _cleanup_if_due(self) -> None:
        current = time.monotonic()
        with self._cleanup_lock:
            if (
                self._last_cleanup is None
                or current - self._last_cleanup >= _CLEANUP_INTERVAL_SECONDS
            ):
                self.cleanup_expired()
                self._last_cleanup = current
