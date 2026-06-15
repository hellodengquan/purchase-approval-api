import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, Text, ForeignKey, Boolean,
)
from sqlalchemy.orm import relationship

from app.database import Base


class PurchaseStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ApprovalActionType(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"
    COUNTERSIGN = "countersign"
    ADD_SIGN = "add_sign"
    PARALLEL_SIGN = "parallel_sign"
    SKIP = "skip"
    RETURN = "return"
    WITHDRAW = "withdraw"


class ApprovalLevel(str, enum.Enum):
    MANAGER = "manager"
    DIRECTOR = "director"
    VP = "vp"
    CEO = "ceo"


class ApprovalNodeMode(str, enum.Enum):
    SEQUENTIAL = "sequential"
    COUNTERSIGN = "countersign"
    PARALLEL = "parallel"


class ApprovalNodeStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    total_amount = Column(Float, nullable=False)
    applicant = Column(String(100), nullable=False)
    department = Column(String(100), nullable=True)
    status = Column(Enum(PurchaseStatus), default=PurchaseStatus.DRAFT, nullable=False)
    idempotency_key = Column(String(128), nullable=True, unique=True)
    current_node_id = Column(Integer, ForeignKey("approval_nodes.id"), nullable=True)
    rule_version_id = Column(Integer, ForeignKey("approval_rule_versions.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship("PurchaseItem", back_populates="order", cascade="all, delete-orphan")
    nodes = relationship("ApprovalNode", back_populates="order", cascade="all, delete-orphan",
                         foreign_keys="ApprovalNode.order_id")
    approvals = relationship("ApprovalRecord", back_populates="order", cascade="all, delete-orphan")
    current_node = relationship("ApprovalNode", foreign_keys=[current_node_id])


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    name = Column(String(200), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    specification = Column(String(200), nullable=True)

    order = relationship("PurchaseOrder", back_populates="items")


class ApprovalNode(Base):
    __tablename__ = "approval_nodes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    level = Column(Enum(ApprovalLevel), nullable=False)
    mode = Column(Enum(ApprovalNodeMode), default=ApprovalNodeMode.SEQUENTIAL, nullable=False)
    status = Column(Enum(ApprovalNodeStatus), default=ApprovalNodeStatus.PENDING, nullable=False)
    sort_order = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    order = relationship("PurchaseOrder", back_populates="nodes", foreign_keys=[order_id])
    approvers = relationship("ApprovalNodeApprover", back_populates="node", cascade="all, delete-orphan")


class ApprovalNodeApprover(Base):
    __tablename__ = "approval_node_approvers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    node_id = Column(Integer, ForeignKey("approval_nodes.id"), nullable=False)
    approver = Column(String(100), nullable=False)
    acted = Column(Boolean, default=False, nullable=False)
    action_type = Column(Enum(ApprovalActionType), nullable=True)
    comment = Column(Text, nullable=True)
    acted_at = Column(DateTime, nullable=True)

    node = relationship("ApprovalNode", back_populates="approvers")


class ApprovalRecord(Base):
    __tablename__ = "approval_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    node_id = Column(Integer, ForeignKey("approval_nodes.id", ondelete="SET NULL"), nullable=True)
    approver = Column(String(100), nullable=False)
    action_type = Column(Enum(ApprovalActionType), nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("PurchaseOrder", back_populates="approvals")
    node = relationship("ApprovalNode")


class ApprovalRuleVersion(Base):
    __tablename__ = "approval_rule_versions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    version_number = Column(Integer, nullable=False, unique=True)
    is_active = Column(Boolean, default=True, nullable=False)
    description = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    rules = relationship("ApprovalRule", back_populates="version", cascade="all, delete-orphan")


class ApprovalRule(Base):
    __tablename__ = "approval_rules"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    version_id = Column(Integer, ForeignKey("approval_rule_versions.id"), nullable=False)
    level = Column(Enum(ApprovalLevel), nullable=False)
    min_amount = Column(Float, nullable=False, default=0)
    max_amount = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    version = relationship("ApprovalRuleVersion", back_populates="rules")
