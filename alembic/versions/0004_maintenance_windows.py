"""Add planned maintenance windows for physical Home Assistant devices.

Revision ID: 0004_maintenance_windows
Revises: 0003_notifications
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_maintenance_windows"
down_revision = "0003_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", name="uq_maintenance_windows_device_id"),
    )
    op.create_index("ix_maintenance_windows_active", "maintenance_windows", ["active"])
    op.create_index("ix_maintenance_windows_ends_at", "maintenance_windows", ["ends_at"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_windows_ends_at", table_name="maintenance_windows")
    op.drop_index("ix_maintenance_windows_active", table_name="maintenance_windows")
    op.drop_table("maintenance_windows")
