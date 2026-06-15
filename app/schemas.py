from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models import PurchaseStatus, ApprovalAction, ApprovalLevel


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


class PurchaseOrderUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    department: Optional[str] = Field(None, max_length=100)
    items: Optional[list[PurchaseItemCreate]] = None


class PurchaseOrderOut(BaseModel):
    id: int
    title: str
    description: Optional[str]
    total_amount: float
    applicant: str
    department: Optional[str]
    status: PurchaseStatus
    current_level: Optional[ApprovalLevel]
    items: list[PurchaseItemOut]
    approvals: list["ApprovalRecordOut"]
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
    current_level: Optional[ApprovalLevel]
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalRequest(BaseModel):
    approver: str = Field(..., max_length=100)
    action: ApprovalAction
    comment: Optional[str] = None


class ApprovalRecordOut(BaseModel):
    id: int
    order_id: int
    level: ApprovalLevel
    approver: str
    action: ApprovalAction
    comment: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalRuleCreate(BaseModel):
    level: ApprovalLevel
    min_amount: float = Field(0, ge=0)
    max_amount: Optional[float] = Field(None, gt=0)


class ApprovalRuleOut(ApprovalRuleCreate):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApprovalRouteOut(BaseModel):
    amount: float
    required_levels: list[ApprovalLevel]
    description: str


class MessageOut(BaseModel):
    message: str
    detail: Optional[str] = None
