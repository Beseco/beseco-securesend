"""guest twofa_pending for two-step registration

Revision ID: 003_guest_twofa_pending
Revises: 002_guest_sms_activation
Create Date: 2026-04-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "003_guest_twofa_pending"
down_revision = "002_guest_sms_activation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guests",
        sa.Column(
            "twofa_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("guests", "twofa_pending", server_default=None)


def downgrade() -> None:
    op.drop_column("guests", "twofa_pending")
