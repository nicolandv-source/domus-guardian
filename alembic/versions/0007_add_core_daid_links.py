"""Add core_daid_links: links a physical HA device to a DOMUS Core DAID.

Revision ID: 0007_add_core_daid_links
Revises: 0006_notification_dismissed_at
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_add_core_daid_links"
down_revision = "0006_notification_dismissed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "core_daid_links",
        sa.Column("device_id_ha", sa.String(length=255), nullable=False),
        sa.Column("daid", sa.String(length=64), nullable=True),
        sa.Column(
            "daid_status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("device_id_ha", name="pk_core_daid_links"),
    )
    op.create_index(
        "ix_core_daid_links_status", "core_daid_links", ["daid_status"]
    )


def downgrade() -> None:
    op.drop_index("ix_core_daid_links_status", table_name="core_daid_links")
    op.drop_table("core_daid_links")
