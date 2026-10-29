from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from app.domain.interval import normalize_utc_interval


class InvalidShift(ValueError):
    code = "INVALID_SHIFT"


class InvalidShiftAssignment(ValueError):
    pass


def _required_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidShift(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class ShiftAssignment:
    shift_id: str
    company_id: str
    employee_id: str
    id: str = field(default_factory=lambda: str(uuid4()))
    membership_id: str | None = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "shift_id", "company_id", "employee_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidShiftAssignment(
                    f"{field_name} must not be empty"
                )

    @classmethod
    def create(
        cls,
        *,
        shift_id: str,
        company_id: str,
        employee_id: str,
        id: str | None = None,
        membership_id: str | None = None,
        display_name: str | None = None,
    ) -> "ShiftAssignment":
        if id is not None:
            return cls(
                shift_id=shift_id,
                company_id=company_id,
                employee_id=employee_id,
                id=id,
                membership_id=membership_id,
                display_name=display_name,
            )
        return cls(
            shift_id=shift_id,
            company_id=company_id,
            employee_id=employee_id,
            membership_id=membership_id,
            display_name=display_name,
        )


@dataclass(frozen=True, slots=True)
class Shift:
    company_id: str
    start_at: datetime
    end_at: datetime
    id: str = field(default_factory=lambda: str(uuid4()))
    assignment: ShiftAssignment | None = None

    def __post_init__(self) -> None:
        _required_identifier(self.company_id, "company_id")
        _required_identifier(self.id, "id")
        start_at, end_at = normalize_utc_interval(
            self.start_at, self.end_at, InvalidShift
        )
        object.__setattr__(self, "start_at", start_at)
        object.__setattr__(self, "end_at", end_at)
        if self.assignment is not None:
            self._validate_assignment(self.assignment)

    @classmethod
    def create(
        cls,
        *,
        company_id: str,
        start_at: datetime,
        end_at: datetime,
        id: str | None = None,
    ) -> "Shift":
        if id is None:
            return cls(company_id=company_id, start_at=start_at, end_at=end_at)
        return cls(
            company_id=company_id,
            start_at=start_at,
            end_at=end_at,
            id=id,
        )

    def overlaps(self, other: "Shift") -> bool:
        return self.start_at < other.end_at and other.start_at < self.end_at

    def with_assignment(self, assignment: ShiftAssignment) -> "Shift":
        if self.assignment is not None:
            raise InvalidShiftAssignment("shift already has an assignment")
        self._validate_assignment(assignment)
        return Shift(
            company_id=self.company_id,
            start_at=self.start_at,
            end_at=self.end_at,
            id=self.id,
            assignment=assignment,
        )

    def _validate_assignment(self, assignment: ShiftAssignment) -> None:
        if (
            assignment.shift_id != self.id
            or assignment.company_id != self.company_id
        ):
            raise InvalidShiftAssignment(
                "assignment must reference this shift and company"
            )
