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


class ApprovalAction(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalLevel(str, enum.Enum):
    MANAGER = "manager"
    DIRECTOR = "director"
    VP = "vp"
    CEO = "ceo"


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    total_amount = Column(Float, nullable=False)
    applicant = Column(String(100), nullable=False)
    department = Column(String(100), nullable=True)
    status = Column(Enum(PurchaseStatus), default=PurchaseStatus.DRAFT, nullable=False)
    current_level = Column(Enum(ApprovalLevel), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship("PurchaseItem", back_populates="order", cascade="all, delete-orphan")
    approvals = relationship("ApprovalRecord", back_populates="order", cascade="all, delete-orphan")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    name = Column(String(200), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    specification = Column(String(200), nullable=True)

    order = relationship("PurchaseOrder", back_populates="items")


class ApprovalRecord(Base):
    __tablename__ = "approval_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    level = Column(Enum(ApprovalLevel), nullable=False)
    approver = Column(String(100), nullable=False)
    action = Column(Enum(ApprovalAction), nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("PurchaseOrder", back_populates="approvals")


class ApprovalRule(Base):
    __tablename__ = "approval_rules"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    level = Column(Enum(ApprovalLevel), nullable=False, unique=True)
    min_amount = Column(Float, nullable=False, default=0)
    max_amount = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
