"""add boundary fields v3

Revision ID: 002_add_boundary_fields
Revises: 001_initial_v2
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_add_boundary_fields"
down_revision: Union[str, None] = "001_initial_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IRREVERSIBLE_WARNING = """
╔═══════════════════════════════════════════════════════════════╗
║  ⚠️  WARNING: THIS MIGRATION IS IRREVERSIBLE ⚠️               ║
║                                                               ║
║  This migration adds new columns and constraints that        ║
║  cannot be safely rolled back in production.                 ║
║                                                               ║
║  - Adds NOT NULL columns with defaults                       ║
║  - Adds unique constraint on idempotency_key                 ║
║                                                               ║
║  DO NOT run downgrade unless you are absolutely sure         ║
║  you understand the consequences and have a full backup.     ║
╚═══════════════════════════════════════════════════════════════╝
"""


def upgrade() -> None:
    op.add_column(
        "approval_node_approvers",
        sa.Column("is_absent", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "approval_node_approvers",
        sa.Column("backup_approver", sa.String(length=100), nullable=True),
    )

    op.add_column(
        "approval_records",
        sa.Column(
            "idempotency_key",
            sa.String(length=128),
            nullable=True,
        ),
    )
    with op.batch_alter_table("approval_records", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_approval_records_idempotency_key",
            ["idempotency_key"],
        )

    op.add_column(
        "approval_rule_versions",
        sa.Column("min_skip_amount", sa.Float(), nullable=False, server_default="50000"),
    )


def downgrade() -> None:
    raise RuntimeError(IRREVERSIBLE_WARNING)
