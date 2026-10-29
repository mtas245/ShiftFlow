"""Create the initial ShiftFlow schema.

Revision ID: 4cd72c57db85
Revises:
Create Date: 2026-10-28 16:06:06.373894
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4cd72c57db85"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("business_email", sa.Text(), nullable=False),
        sa.Column("business_phone", sa.String(length=16), nullable=False),
        sa.Column("business_address", sa.Text(), nullable=False),
        sa.Column("industry", sa.Text(), nullable=False),
        sa.Column("joining_code", sa.String(length=8), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("joining_code", name="uq_companies_joining_code"),
    )
    op.create_table(
        "company_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "user_id",
            name="uq_company_memberships_company_user",
        ),
    )
    op.create_table(
        "shifts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "end_at > start_at",
            name="ck_shifts_end_after_start",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "company_id", name="uq_shifts_id_company"),
    )
    op.create_index(
        "ix_shifts_company_start",
        "shifts",
        ["company_id", "start_at"],
        unique=False,
    )
    op.create_table(
        "availabilities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("employee_id", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "end_at > start_at",
            name="ck_availabilities_end_after_start",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["company_memberships.company_id", "company_memberships.user_id"],
            name="fk_availabilities_company_employee_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_availabilities_company_employee_start",
        "availabilities",
        ["company_id", "employee_id", "start_at"],
        unique=False,
    )
    op.create_index(
        "ix_availabilities_company_start",
        "availabilities",
        ["company_id", "start_at"],
        unique=False,
    )
    op.create_table(
        "shift_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("shift_id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("employee_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["company_memberships.company_id", "company_memberships.user_id"],
            name="fk_shift_assignments_company_employee_membership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["shift_id", "company_id"],
            ["shifts.id", "shifts.company_id"],
            name="fk_shift_assignments_shift_company",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shift_id",
            name="uq_shift_assignments_one_per_shift",
        ),
    )
    op.create_index(
        "ix_shift_assignments_company_employee",
        "shift_assignments",
        ["company_id", "employee_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shift_assignments_company_employee",
        table_name="shift_assignments",
    )
    op.drop_table("shift_assignments")
    op.drop_index(
        "ix_availabilities_company_start",
        table_name="availabilities",
    )
    op.drop_index(
        "ix_availabilities_company_employee_start",
        table_name="availabilities",
    )
    op.drop_table("availabilities")
    op.drop_index("ix_shifts_company_start", table_name="shifts")
    op.drop_table("shifts")
    op.drop_table("company_memberships")
    op.drop_table("companies")
