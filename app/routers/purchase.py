from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    PurchaseOrder, PurchaseItem, PurchaseStatus, ApprovalRecord,
    ApprovalRuleVersion, ApprovalRule, ApprovalActionType, ApprovalNode,
)
from app.schemas import (
    PurchaseOrderCreate, PurchaseOrderUpdate, PurchaseOrderOut, PurchaseOrderSummary,
    ApprovalRequest, ApprovalRouteOut,
    CountersignRequest, AddSignRequest, ParallelSignRequest,
    SkipRequest, ReturnRequest, WithdrawRequest,
    RuleVersionCreate, RuleVersionOut, RuleVersionSummary, ApprovalRuleOut,
    MessageOut,
)
from app.services.approval import (
    init_default_rule_version, get_approval_route,
    create_approval_nodes, process_approve, process_reject,
    process_countersign, process_add_sign, process_parallel_sign,
    process_skip, process_return, process_withdraw,
    create_rule_version, activate_rule_version, deactivate_rule_version,
    rollback_rule_version, get_active_rule_version,
)

router = APIRouter(prefix="/api/purchase-orders", tags=["采购单"])


@router.post("/", response_model=PurchaseOrderOut, status_code=201)
def create_purchase_order(data: PurchaseOrderCreate, db: Session = Depends(get_db)):
    if data.idempotency_key:
        existing = db.query(PurchaseOrder).filter(
            PurchaseOrder.idempotency_key == data.idempotency_key
        ).first()
        if existing:
            return _load_order(db, existing.id)

    total_amount = sum(item.quantity * item.unit_price for item in data.items)

    order = PurchaseOrder(
        title=data.title,
        description=data.description,
        total_amount=total_amount,
        applicant=data.applicant,
        department=data.department,
        status=PurchaseStatus.DRAFT,
        idempotency_key=data.idempotency_key,
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

    init_default_rule_version(db)

    order.status = PurchaseStatus.PENDING
    create_approval_nodes(db, order)
    return _load_order(db, order.id)


@router.post("/{order_id}/approve", response_model=PurchaseOrderOut)
def approve_purchase_order(order_id: int, data: ApprovalRequest, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status != PurchaseStatus.PENDING:
        raise HTTPException(status_code=400, detail="仅待审批状态的采购单可审批")

    try:
        if data.action == ApprovalActionType.REJECT:
            order = process_reject(db, order, data.approver, data.comment)
        else:
            order = process_approve(db, order, data.approver, data.comment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _load_order(db, order.id)


@router.post("/{order_id}/countersign", response_model=PurchaseOrderOut)
def countersign_purchase_order(order_id: int, data: CountersignRequest, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status != PurchaseStatus.PENDING:
        raise HTTPException(status_code=400, detail="仅待审批状态可发起会签")

    try:
        order = process_countersign(db, order, data.initiator, data.approvers, data.comment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _load_order(db, order.id)


@router.post("/{order_id}/add-sign", response_model=PurchaseOrderOut)
def add_sign_purchase_order(order_id: int, data: AddSignRequest, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status != PurchaseStatus.PENDING:
        raise HTTPException(status_code=400, detail="仅待审批状态可加签")

    try:
        order = process_add_sign(db, order, data.approver, data.added_approver, data.comment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _load_order(db, order.id)


@router.post("/{order_id}/parallel-sign", response_model=PurchaseOrderOut)
def parallel_sign_purchase_order(order_id: int, data: ParallelSignRequest, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status != PurchaseStatus.PENDING:
        raise HTTPException(status_code=400, detail="仅待审批状态可并签")

    try:
        order = process_parallel_sign(db, order, data.initiator, data.approvers, data.comment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _load_order(db, order.id)


@router.post("/{order_id}/skip", response_model=PurchaseOrderOut)
def skip_purchase_order(order_id: int, data: SkipRequest, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status != PurchaseStatus.PENDING:
        raise HTTPException(status_code=400, detail="仅待审批状态可跳级")

    try:
        order = process_skip(db, order, data.approver, data.comment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _load_order(db, order.id)


@router.post("/{order_id}/return", response_model=PurchaseOrderOut)
def return_purchase_order(order_id: int, data: ReturnRequest, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status != PurchaseStatus.PENDING:
        raise HTTPException(status_code=400, detail="仅待审批状态可退回")

    try:
        order = process_return(db, order, data.approver, data.comment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _load_order(db, order.id)


@router.post("/{order_id}/withdraw", response_model=PurchaseOrderOut)
def withdraw_purchase_order(order_id: int, data: WithdrawRequest, db: Session = Depends(get_db)):
    order = _load_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    if order.status != PurchaseStatus.PENDING:
        raise HTTPException(status_code=400, detail="仅待审批状态可撤回")

    try:
        order = process_withdraw(db, order, data.applicant, data.comment)
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
    order.current_node_id = None
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

    init_default_rule_version(db)
    return get_approval_route(order.total_amount, db)


@router.get("/approval-route/preview", response_model=ApprovalRouteOut)
def preview_approval_route(amount: float = Query(..., gt=0, description="采购金额"), db: Session = Depends(get_db)):
    init_default_rule_version(db)
    return get_approval_route(amount, db)


def _load_order(db: Session, order_id: int) -> PurchaseOrder | None:
    return (
        db.query(PurchaseOrder)
        .options(
            selectinload(PurchaseOrder.items),
            selectinload(PurchaseOrder.nodes).selectinload(ApprovalNode.approvers),
            selectinload(PurchaseOrder.approvals),
        )
        .populate_existing()
        .filter(PurchaseOrder.id == order_id)
        .first()
    )


rule_router = APIRouter(prefix="/api/approval-rules", tags=["审批规则"])


@rule_router.get("/versions", response_model=list[RuleVersionSummary])
def list_rule_versions(db: Session = Depends(get_db)):
    return db.query(ApprovalRuleVersion).order_by(ApprovalRuleVersion.version_number).all()


@rule_router.get("/versions/{version_id}", response_model=RuleVersionOut)
def get_rule_version(version_id: int, db: Session = Depends(get_db)):
    version = db.query(ApprovalRuleVersion).filter(ApprovalRuleVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="审批规则版本不存在")
    return version


@rule_router.get("/active", response_model=RuleVersionOut)
def get_active_rules(db: Session = Depends(get_db)):
    init_default_rule_version(db)
    version = get_active_rule_version(db)
    if not version:
        raise HTTPException(status_code=404, detail="无活跃的审批规则版本")
    return version


@rule_router.post("/versions", response_model=RuleVersionOut, status_code=201)
def create_new_rule_version(data: RuleVersionCreate, db: Session = Depends(get_db)):
    try:
        return create_rule_version(db, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@rule_router.post("/versions/{version_id}/activate", response_model=RuleVersionOut)
def activate_version(version_id: int, db: Session = Depends(get_db)):
    try:
        return activate_rule_version(db, version_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@rule_router.post("/versions/{version_id}/deactivate", response_model=RuleVersionOut)
def deactivate_version(version_id: int, db: Session = Depends(get_db)):
    try:
        return deactivate_rule_version(db, version_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@rule_router.post("/versions/{version_id}/rollback", response_model=RuleVersionOut)
def rollback_version(version_id: int, db: Session = Depends(get_db)):
    try:
        return rollback_rule_version(db, version_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
