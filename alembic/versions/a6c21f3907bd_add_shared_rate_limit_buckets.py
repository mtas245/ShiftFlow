"""Add shared PostgreSQL rate-limit buckets.

Revision ID: a6c21f3907bd
Revises: 83f5c1a6d902
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6c21f3907bd"
down_revision: Union[str, None] = "83f5c1a6d902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "scope IN ("
            "'register:email', 'register:ip', "
            "'login:email', 'login:ip', "
            "'verify_email:email', 'verify_email:ip', "
            "'reset_password:email', 'reset_password:ip'"
            ")",
            name="ck_rate_limit_buckets_scope",
        ),
        sa.CheckConstraint(
            "request_count > 0",
            name="ck_rate_limit_buckets_positive_count",
        ),
        sa.PrimaryKeyConstraint("scope", "key_digest"),
    )
    op.create_index(
        "ix_rate_limit_buckets_window_started_at",
        "rate_limit_buckets",
        ["window_started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rate_limit_buckets_window_started_at",
        table_name="rate_limit_buckets",
    )
    op.drop_table("rate_limit_buckets")
