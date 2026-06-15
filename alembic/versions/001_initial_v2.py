"""initial schema v2

Revision ID: 001_initial_v2
Revises: None
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_initial_v2"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "approval_rule_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_number"),
    )

    op.create_table(
        "approval_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.Enum("manager", "director", "vp", "ceo", name="approvallevel"), nullable=False),
        sa.Column("min_amount", sa.Float(), nullable=False),
        sa.Column("max_amount", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["version_id"], ["approval_rule_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("total_amount", sa.Float(), nullable=False),
        sa.Column("applicant", sa.String(length=100), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("status", sa.Enum("draft", "pending", "approved", "rejected", "cancelled", name="purchasestatus"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("current_node_id", sa.Integer(), nullable=True),
        sa.Column("rule_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["rule_version_id"], ["approval_rule_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )

    op.create_index("ix_purchase_orders_id", "purchase_orders", ["id"])

    op.create_table(
        "purchase_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False),
        sa.Column("specification", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["purchase_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_purchase_items_id", "purchase_items", ["id"])

    op.create_table(
        "approval_nodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.Enum("manager", "director", "vp", "ceo", name="approvallevel"), nullable=False),
        sa.Column("mode", sa.Enum("sequential", "countersign", "parallel", name="approvalnodemode"), nullable=False),
        sa.Column("status", sa.Enum("pending", "approved", "rejected", "skipped", name="approvalnodestatus"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["purchase_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_approval_nodes_id", "approval_nodes", ["id"])

    op.create_table(
        "approval_node_approvers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("approver", sa.String(length=100), nullable=False),
        sa.Column("acted", sa.Boolean(), nullable=False),
        sa.Column("action_type", sa.Enum("approve", "reject", "countersign", "add_sign", "parallel_sign", "skip", "return", "withdraw", name="approvalactiontype"), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("acted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["approval_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_approval_node_approvers_id", "approval_node_approvers", ["id"])

    op.create_table(
        "approval_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=True),
        sa.Column("approver", sa.String(length=100), nullable=False),
        sa.Column("action_type", sa.Enum("approve", "reject", "countersign", "add_sign", "parallel_sign", "skip", "return", "withdraw", name="approvalactiontype"), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["approval_nodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["order_id"], ["purchase_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_approval_records_id", "approval_records", ["id"])

    op.create_index("ix_approval_rules_id", "approval_rules", ["id"])
    op.create_index("ix_approval_rule_versions_id", "approval_rule_versions", ["id"])


def downgrade() -> None:
    op.drop_table("approval_records")
    op.drop_table("approval_node_approvers")
    op.drop_table("approval_nodes")
    op.drop_table("purchase_items")
    op.drop_table("purchase_orders")
    op.drop_table("approval_rules")
    op.drop_table("approval_rule_versions")
