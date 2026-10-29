"""Add account links and public references to memberships.

Revision ID: c8a4f0e2b761
Revises: b7e5d1c9a403
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8a4f0e2b761"
down_revision: Union[str, None] = "b7e5d1c9a403"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company_memberships",
        sa.Column("account_user_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "company_memberships",
        sa.Column("public_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "company_memberships",
        sa.Column("display_name", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_company_memberships_account_user",
        "company_memberships",
        "users",
        ["account_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        "UPDATE company_memberships "
        "SET public_id = gen_random_uuid()::text "
        "WHERE public_id IS NULL"
    )
    op.alter_column(
        "company_memberships",
        "public_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_company_memberships_public_id",
        "company_memberships",
        ["public_id"],
    )
    op.create_unique_constraint(
        "uq_company_memberships_company_account_user",
        "company_memberships",
        ["company_id", "account_user_id"],
    )
    op.create_check_constraint(
        "ck_company_memberships_linked_display_name",
        "company_memberships",
        "account_user_id IS NULL OR display_name IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_company_memberships_display_name_length",
        "company_memberships",
        "display_name IS NULL OR "
        "(length(btrim(display_name)) BETWEEN 1 AND 80)",
    )
    op.create_index(
        "ix_company_memberships_account_user_id",
        "company_memberships",
        ["account_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_memberships_account_user_id",
        table_name="company_memberships",
    )
    op.drop_constraint(
        "ck_company_memberships_display_name_length",
        "company_memberships",
        type_="check",
    )
    op.drop_constraint(
        "ck_company_memberships_linked_display_name",
        "company_memberships",
        type_="check",
    )
    op.drop_constraint(
        "uq_company_memberships_company_account_user",
        "company_memberships",
        type_="unique",
    )
    op.drop_constraint(
        "uq_company_memberships_public_id",
        "company_memberships",
        type_="unique",
    )
    op.drop_constraint(
        "fk_company_memberships_account_user",
        "company_memberships",
        type_="foreignkey",
    )
    op.drop_column("company_memberships", "display_name")
    op.drop_column("company_memberships", "public_id")
    op.drop_column("company_memberships", "account_user_id")
