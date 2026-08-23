"""Add dismissed_at to notifications for automatic HA panel cleanup.

Revision ID: 0006_notification_dismissed_at
Revises: 0005_notification_correlation_id
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_notification_dismissed_at"
down_revision = "0005_notification_correlation_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notifications", "dismissed_at")
