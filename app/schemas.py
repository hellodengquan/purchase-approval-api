from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models import (
    PurchaseStatus, ApprovalActionType, ApprovalLevel,
    ApprovalNodeMode, ApprovalNodeStatus,
)


class PurchaseItemCreate(BaseModel):
    name: str = Field(..., max_length=200)
    quantity: int = Field(..., gt=0)
    unit_price: float = Field(..., gt=0)
    specification: Optional[str] = Field(None, max_length=200)


class PurchaseItemOut(PurchaseItemCreate):
    id: int
    order_id: int

    model_config = {"from_attributes": True}


class PurchaseOrderCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: Optional[str] = None
    applicant: str = Field(..., max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    items: list[PurchaseItemCreate] = Field(..., min_length=1)
    idempotency_key: Optional[str] = Field(None, max_length=128)


class PurchaseOrderUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    department: Optional[str] = Field(None, max_length=100)
    items: Optional[list[PurchaseItemCreate]] = None


class ApprovalNodeApproverOut(BaseModel):
    id: int
    node_id: int
    approver: str
    acted: bool
    is_absent: bool
    backup_approver: Optional[str]
    action_type: Optional[ApprovalActionType]
    comment: Optional[str]
    acted_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ApprovalNodeOut(BaseModel):
    id: int
    order_id: int
    level: ApprovalLevel
    mode: ApprovalNodeMode
    status: ApprovalNodeStatus
    sort_order: int
    approvers: list[ApprovalNodeApproverOut]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApprovalRecordOut(BaseModel):
    id: int
    order_id: int
    node_id: Optional[int]
    approver: str
    action_type: ApprovalActionType
    idempotency_key: Optional[str]
    idempotency_payload_hash: Optional[str]
    comment: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class PurchaseOrderOut(BaseModel):
    id: int
    title: str
    description: Optional[str]
    total_amount: float
    applicant: str
    department: Optional[str]
    status: PurchaseStatus
    idempotency_key: Optional[str]
    current_node_id: Optional[int]
    rule_version_id: Optional[int]
    items: list[PurchaseItemOut]
    nodes: list[ApprovalNodeOut]
    approvals: list[ApprovalRecordOut]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PurchaseOrderSummary(BaseModel):
    id: int
    title: str
    total_amount: float
    applicant: str
    department: Optional[str]
    status: PurchaseStatus
    current_node_id: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}


class SubmitRequest(BaseModel):
    idempotency_key: Optional[str] = Field(None, max_length=128)


class ApprovalRequest(BaseModel):
    approver: str = Field(..., max_length=100)
    action: ApprovalActionType = ApprovalActionType.APPROVE
    comment: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, max_length=128)


class CountersignRequest(BaseModel):
    initiator: str = Field(..., max_length=100)
    approvers: list[str] = Field(..., min_length=2)
    comment: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, max_length=128)


class AddSignRequest(BaseModel):
    approver: str = Field(..., max_length=100)
    added_approver: str = Field(..., max_length=100)
    backup_approver: Optional[str] = Field(None, max_length=100)
    comment: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, max_length=128)


class ParallelSignRequest(BaseModel):
    initiator: str = Field(..., max_length=100)
    approvers: list[str] = Field(..., min_length=2)
    comment: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, max_length=128)


class SkipRequest(BaseModel):
    approver: str = Field(..., max_length=100)
    comment: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, max_length=128)


class ReturnRequest(BaseModel):
    approver: str = Field(..., max_length=100)
    comment: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, max_length=128)


class WithdrawRequest(BaseModel):
    applicant: str = Field(..., max_length=100)
    comment: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, max_length=128)


class ApprovalRouteOut(BaseModel):
    amount: float
    required_levels: list[ApprovalLevel]
    description: str


class ApprovalRuleCreate(BaseModel):
    level: ApprovalLevel
    min_amount: float = Field(0, ge=0)
    max_amount: Optional[float] = Field(None, gt=0)


class ApprovalRuleOut(ApprovalRuleCreate):
    id: int
    version_id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class RuleVersionCreate(BaseModel):
    description: Optional[str] = Field(None, max_length=500)
    min_skip_amount: float = Field(50000, ge=0)
    min_skip_levels: int = Field(2, ge=1)
    rules: list[ApprovalRuleCreate] = Field(..., min_length=1)


class RuleVersionOut(BaseModel):
    id: int
    version_number: int
    is_active: bool
    min_skip_amount: float
    min_skip_levels: int
    description: Optional[str]
    rules: list[ApprovalRuleOut]
    created_at: datetime

    model_config = {"from_attributes": True}


class RuleVersionSummary(BaseModel):
    id: int
    version_number: int
    is_active: bool
    min_skip_amount: float
    min_skip_levels: int
    description: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    message: str
    detail: Optional[str] = None


class RollbackResult(BaseModel):
    version: RuleVersionOut
    affected_pending_orders: list[int]
    message: str
