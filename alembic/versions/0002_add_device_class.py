"""Store Home Assistant device classes for health profiles.

Revision ID: 0002_device_class
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_device_class"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("device_class", sa.String(length=64)))


def downgrade() -> None:
    op.drop_column("devices", "device_class")
