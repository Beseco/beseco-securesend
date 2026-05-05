"""audit_events Tabelle (Admin-/Debug-Audit).

Diese Revision ergänzt manuelle Alembic-Nutzung. Bei Start lädt die App
zusätzlich `Base.metadata.create_all` und legt die Tabelle bei Bedarf an.

Revision ID: 001_audit_events
Revises:
Create Date: 2026-04-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "001_audit_events"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("actor_role", sa.String(40), nullable=True),
        sa.Column("org_id", sa.String(36), nullable=True),
        sa.Column("reseller_id", sa.String(36), nullable=True),
        sa.Column("target_type", sa.String(60), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="success"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message_redacted", sa.String(500), nullable=True),
        sa.Column("meta_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_org_id", "audit_events", ["org_id"])
    op.create_index("ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_org_id", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_table("audit_events")
