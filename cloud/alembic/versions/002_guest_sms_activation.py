"""guest sms activation fields

Revision ID: 002_guest_sms_activation
Revises: 001_audit_events
Create Date: 2026-04-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "002_guest_sms_activation"
down_revision = "001_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guests", sa.Column("sms_code", sa.String(length=8), nullable=True))
    op.add_column("guests", sa.Column("sms_code_expires_at", sa.DateTime(), nullable=True))
    op.add_column("guests", sa.Column("phone_verified_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("guests", "phone_verified_at")
    op.drop_column("guests", "sms_code_expires_at")
    op.drop_column("guests", "sms_code")
