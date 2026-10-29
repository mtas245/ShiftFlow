"""Add bounded previous-CSRF-token grace state.

Revision ID: b7e5d1c9a403
Revises: a6c21f3907bd
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e5d1c9a403"
down_revision: Union[str, None] = "a6c21f3907bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("previous_csrf_token_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "user_sessions",
        sa.Column(
            "previous_csrf_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_user_sessions_previous_csrf_pair",
        "user_sessions",
        "(previous_csrf_token_hash IS NULL "
        "AND previous_csrf_expires_at IS NULL) OR "
        "(previous_csrf_token_hash IS NOT NULL "
        "AND previous_csrf_expires_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_user_sessions_previous_csrf_pair",
        "user_sessions",
        type_="check",
    )
    op.drop_column("user_sessions", "previous_csrf_expires_at")
    op.drop_column("user_sessions", "previous_csrf_token_hash")
