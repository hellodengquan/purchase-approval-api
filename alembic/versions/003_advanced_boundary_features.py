"""advanced boundary features: payload hash, skip levels

Revision ID: 003_advanced_boundary_features
Revises: 002_add_boundary_fields
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "003_advanced_boundary_features"
down_revision: Union[str, None] = "002_add_boundary_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "approval_records",
        sa.Column("idempotency_payload_hash", sa.String(length=64), nullable=True),
    )

    op.add_column(
        "approval_rule_versions",
        sa.Column("min_skip_levels", sa.Integer(), nullable=False, server_default="2"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "IRREVERSIBLE MIGRATION: This migration cannot be downgraded. "
        "Downgrading would remove payload hash protection and skip level thresholds. "
        "Manual data migration required."
    )
