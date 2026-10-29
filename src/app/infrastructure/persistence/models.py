from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="ck_users_email_normalized"),
        UniqueConstraint("email", name="uq_users_email"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserSessionRecord(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="ck_user_sessions_expiry_after_creation",
        ),
        UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
        CheckConstraint(
            "(previous_csrf_token_hash IS NULL "
            "AND previous_csrf_expires_at IS NULL) OR "
            "(previous_csrf_token_hash IS NOT NULL "
            "AND previous_csrf_expires_at IS NOT NULL)",
            name="ck_user_sessions_previous_csrf_pair",
        ),
        Index("ix_user_sessions_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_csrf_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    previous_csrf_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AccountActionTokenRecord(Base):
    __tablename__ = "account_action_tokens"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')",
            name="ck_account_action_tokens_purpose",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_account_action_tokens_expiry_after_creation",
        ),
        UniqueConstraint(
            "token_digest",
            name="uq_account_action_tokens_digest",
        ),
        Index(
            "ix_account_action_tokens_user_purpose",
            "user_id",
            "purpose",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RateLimitBucketRecord(Base):
    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        CheckConstraint(
            "scope IN ("
            "'register:email', 'register:ip', "
            "'login:email', 'login:ip', "
            "'verify_email:email', 'verify_email:ip', "
            "'reset_password:email', 'reset_password:ip'"
            ")",
            name="ck_rate_limit_buckets_scope",
        ),
        CheckConstraint(
            "request_count > 0",
            name="ck_rate_limit_buckets_positive_count",
        ),
        Index(
            "ix_rate_limit_buckets_window_started_at",
            "window_started_at",
        ),
    )

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)


class CompanyRecord(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("joining_code", name="uq_companies_joining_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    business_email: Mapped[str] = mapped_column(Text, nullable=False)
    business_phone: Mapped[str] = mapped_column(String(16), nullable=False)
    business_address: Mapped[str] = mapped_column(Text, nullable=False)
    industry: Mapped[str] = mapped_column(Text, nullable=False)
    joining_code: Mapped[str] = mapped_column(String(8), nullable=False)


class CompanyMembershipRecord(Base):
    __tablename__ = "company_memberships"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "user_id",
            name="uq_company_memberships_company_user",
        ),
        UniqueConstraint(
            "company_id",
            "account_user_id",
            name="uq_company_memberships_company_account_user",
        ),
        UniqueConstraint("public_id", name="uq_company_memberships_public_id"),
        CheckConstraint(
            "account_user_id IS NULL OR display_name IS NOT NULL",
            name="ck_company_memberships_linked_display_name",
        ),
        CheckConstraint(
            "display_name IS NULL OR "
            "(length(trim(display_name)) BETWEEN 1 AND 80)",
            name="ck_company_memberships_display_name_length",
        ),
        Index(
            "ix_company_memberships_account_user_id",
            "account_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    account_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    public_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        default=lambda: str(uuid4()),
    )
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)


class AvailabilityRecord(Base):
    __tablename__ = "availabilities"
    __table_args__ = (
        CheckConstraint(
            "end_at > start_at",
            name="ck_availabilities_end_after_start",
        ),
        ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["company_memberships.company_id", "company_memberships.user_id"],
            name="fk_availabilities_company_employee_membership",
            ondelete="CASCADE",
        ),
        Index(
            "ix_availabilities_company_employee_start",
            "company_id",
            "employee_id",
            "start_at",
        ),
        Index(
            "ix_availabilities_company_start",
            "company_id",
            "start_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), nullable=False)
    employee_id: Mapped[str] = mapped_column(Text, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShiftRecord(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint(
            "end_at > start_at",
            name="ck_shifts_end_after_start",
        ),
        UniqueConstraint("id", "company_id", name="uq_shifts_id_company"),
        Index("ix_shifts_company_start", "company_id", "start_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShiftAssignmentRecord(Base):
    __tablename__ = "shift_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["shift_id", "company_id"],
            ["shifts.id", "shifts.company_id"],
            name="fk_shift_assignments_shift_company",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["company_memberships.company_id", "company_memberships.user_id"],
            name="fk_shift_assignments_company_employee_membership",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "shift_id",
            name="uq_shift_assignments_one_per_shift",
        ),
        Index(
            "ix_shift_assignments_company_employee",
            "company_id",
            "employee_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shift_id: Mapped[str] = mapped_column(String(36), nullable=False)
    company_id: Mapped[str] = mapped_column(String(36), nullable=False)
    employee_id: Mapped[str] = mapped_column(Text, nullable=False)
