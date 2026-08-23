"""Add correlation_id to notifications for outbox tracing.

Revision ID: 0005_notification_correlation_id
Revises: 0004_maintenance_windows
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_notification_correlation_id"
down_revision = "0004_maintenance_windows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications", sa.Column("correlation_id", sa.String(length=32), nullable=True)
    )
    op.create_index(
        "ix_notifications_correlation_id", "notifications", ["correlation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_correlation_id", table_name="notifications")
    op.drop_column("notifications", "correlation_id")
