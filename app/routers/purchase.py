from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    PurchaseOrder, PurchaseItem, PurchaseStatus, ApprovalRecord, ApprovalRule,
)
from app.schemas import (
    PurchaseOrderCreate, PurchaseOrderUpdate, PurchaseOrderOut, PurchaseOrderSummary,
    ApprovalRequest, ApprovalRouteOut, ApprovalRuleCreate, ApprovalRuleOut, MessageOut,
)
from app.services.approval import (
    determine_initial_level, process_approval, get_approval_route,
    create_or_update_rule, init_default_rules,
)

router = APIRouter(prefix="/api/purchase-orders", tags=["采购单"])


@router.post("/", response_model=PurchaseOrderOut, status_code=201)
def create_purchase_order(data: PurchaseOrderCreate, db: Session = Depends(get_db)):
    total_amount = sum(item.quantity * item.unit_price for item in data.items)

    order = PurchaseOrder(
        title=data.title,
        description=data.description,
        total_amount=total_amount,
        applicant=data.applicant,
        department=data.department,
        status=PurchaseStatus.DRAFT,
    )
    db.add(order)
    db.flush()

    for item_data in data.items:
        item = PurchaseItem(
            order_id=order.id,
            name=item_data.name,
            quantity=item_data.quantity,
            unit_price=item_data.unit_price,
            specification=item_data.specification,
        )
        db.add(item)

    db.commit()
    db.refresh(order)
    return _load_order(db, order.id)


@router.post("/{order_id}/submit", response_model=PurchaseOrderOut)
def submit_purchase_order(order_id: int, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status != PurchaseStatus.DRAFT:
        raise HTTPException(status_code=400, detail="仅草稿状态的采购单可提交")

    init_default_rules(db)
    initial_level = determine_initial_level(order.total_amount, db)

    order.status = PurchaseStatus.PENDING
    order.current_level = initial_level
    db.commit()
    db.refresh(order)
    return _load_order(db, order.id)


@router.post("/{order_id}/approve", response_model=PurchaseOrderOut)
def approve_purchase_order(order_id: int, data: ApprovalRequest, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status != PurchaseStatus.PENDING:
        raise HTTPException(status_code=400, detail="仅待审批状态的采购单可审批")

    try:
        order = process_approval(db, order, data.approver, data.action, data.comment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _load_order(db, order.id)


@router.post("/{order_id}/cancel", response_model=PurchaseOrderOut)
def cancel_purchase_order(order_id: int, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status not in (PurchaseStatus.DRAFT, PurchaseStatus.PENDING):
        raise HTTPException(status_code=400, detail="仅草稿或待审批状态可取消")

    order.status = PurchaseStatus.CANCELLED
    order.current_level = None
    db.commit()
    db.refresh(order)
    return _load_order(db, order.id)


@router.get("/", response_model=list[PurchaseOrderSummary])
def list_purchase_orders(
    status: PurchaseStatus | None = Query(None, description="按状态筛选"),
    applicant: str | None = Query(None, description="按申请人筛选"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(PurchaseOrder)
    if status:
        query = query.filter(PurchaseOrder.status == status)
    if applicant:
        query = query.filter(PurchaseOrder.applicant == applicant)
    orders = query.order_by(PurchaseOrder.created_at.desc()).offset(skip).limit(limit).all()
    return orders


@router.get("/{order_id}", response_model=PurchaseOrderOut)
def get_purchase_order(order_id: int, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    return order


@router.put("/{order_id}", response_model=PurchaseOrderOut)
def update_purchase_order(order_id: int, data: PurchaseOrderUpdate, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status != PurchaseStatus.DRAFT:
        raise HTTPException(status_code=400, detail="仅草稿状态可修改")

    if data.title is not None:
        order.title = data.title
    if data.description is not None:
        order.description = data.description
    if data.department is not None:
        order.department = data.department

    if data.items is not None:
        db.query(PurchaseItem).filter(PurchaseItem.order_id == order.id).delete()
        for item_data in data.items:
            item = PurchaseItem(
                order_id=order.id,
                name=item_data.name,
                quantity=item_data.quantity,
                unit_price=item_data.unit_price,
                specification=item_data.specification,
            )
            db.add(item)
        order.total_amount = sum(i.quantity * i.unit_price for i in data.items)

    db.commit()
    return _load_order(db, order.id)


@router.get("/{order_id}/approval-route", response_model=ApprovalRouteOut)
def get_order_approval_route(order_id: int, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")

    init_default_rules(db)
    return get_approval_route(order.total_amount, db)


@router.get("/approval-route/preview", response_model=ApprovalRouteOut)
def preview_approval_route(amount: float = Query(..., gt=0, description="采购金额"), db: Session = Depends(get_db)):
    init_default_rules(db)
    return get_approval_route(amount, db)


def _load_order(db: Session, order_id: int) -> PurchaseOrder | None:
    return (
        db.query(PurchaseOrder)
        .options(selectinload(PurchaseOrder.items), selectinload(PurchaseOrder.approvals))
        .filter(PurchaseOrder.id == order_id)
        .first()
    )


rule_router = APIRouter(prefix="/api/approval-rules", tags=["审批规则"])


@rule_router.get("/", response_model=list[ApprovalRuleOut])
def list_approval_rules(db: Session = Depends(get_db)):
    init_default_rules(db)
    return db.query(ApprovalRule).order_by(ApprovalRule.min_amount).all()


@rule_router.post("/", response_model=ApprovalRuleOut)
def create_or_update_approval_rule(data: ApprovalRuleCreate, db: Session = Depends(get_db)):
    init_default_rules(db)
    return create_or_update_rule(db, data)
