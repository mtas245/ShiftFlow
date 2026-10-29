from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from app.domain.interval import normalize_utc_interval


class InvalidAvailability(ValueError):
    code = "INVALID_AVAILABILITY"


def _required_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidAvailability(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class Availability:
    company_id: str
    employee_id: str
    start_at: datetime
    end_at: datetime
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        _required_identifier(self.company_id, "company_id")
        _required_identifier(self.employee_id, "employee_id")
        _required_identifier(self.id, "id")

        start_at, end_at = normalize_utc_interval(
            self.start_at, self.end_at, InvalidAvailability
        )
        object.__setattr__(self, "start_at", start_at)
        object.__setattr__(self, "end_at", end_at)

    @classmethod
    def create(
        cls,
        *,
        company_id: str,
        employee_id: str,
        start_at: datetime,
        end_at: datetime,
        id: str | None = None,
    ) -> "Availability":
        if id is None:
            return cls(
                company_id=company_id,
                employee_id=employee_id,
                start_at=start_at,
                end_at=end_at,
            )
        return cls(
            company_id=company_id,
            employee_id=employee_id,
            start_at=start_at,
            end_at=end_at,
            id=id,
        )

    def overlaps(self, other: "Availability") -> bool:
        return self.start_at < other.end_at and other.start_at < self.end_at

    def with_interval(
        self, start_at: datetime, end_at: datetime
    ) -> "Availability":
        return Availability.create(
            company_id=self.company_id,
            employee_id=self.employee_id,
            start_at=start_at,
            end_at=end_at,
            id=self.id,
        )
