from datetime import datetime

from sqlalchemy.orm import Session

from app.models import (
    ApprovalLevel, ApprovalRule, ApprovalRuleVersion,
    ApprovalRecord, ApprovalActionType,
    ApprovalNode, ApprovalNodeMode, ApprovalNodeStatus, ApprovalNodeApprover,
    PurchaseOrder, PurchaseStatus,
)
from app.schemas import ApprovalRouteOut, RuleVersionCreate


APPROVAL_CHAIN = [
    ApprovalLevel.MANAGER,
    ApprovalLevel.DIRECTOR,
    ApprovalLevel.VP,
    ApprovalLevel.CEO,
]

DEFAULT_RULES = [
    {"level": ApprovalLevel.MANAGER, "min_amount": 0, "max_amount": 5000},
    {"level": ApprovalLevel.DIRECTOR, "min_amount": 5000, "max_amount": 50000},
    {"level": ApprovalLevel.VP, "min_amount": 50000, "max_amount": 200000},
    {"level": ApprovalLevel.CEO, "min_amount": 200000, "max_amount": None},
]

ABSENT_APPROVER_TIMEOUT_HOURS = 24

LEVEL_ORDER = {
    ApprovalLevel.MANAGER: 0,
    ApprovalLevel.DIRECTOR: 1,
    ApprovalLevel.VP: 2,
    ApprovalLevel.CEO: 3,
}

APPROVER_ABSENT_THRESHOLD_MINUTES = 30

LEVEL_NAMES = {
    ApprovalLevel.MANAGER: "经理",
    ApprovalLevel.DIRECTOR: "总监",
    ApprovalLevel.VP: "副总裁",
    ApprovalLevel.CEO: "CEO",
}


def init_default_rule_version(db: Session) -> ApprovalRuleVersion:
    existing = db.query(ApprovalRuleVersion).first()
    if existing:
        return existing

    version = ApprovalRuleVersion(
        version_number=1,
        is_active=True,
        min_skip_amount=50000,
        description="默认审批规则",
    )
    db.add(version)
    db.flush()

    for rule_data in DEFAULT_RULES:
        rule = ApprovalRule(version_id=version.id, **rule_data)
        db.add(rule)

    db.commit()
    db.refresh(version)
    return version


def get_active_rule_version(db: Session) -> ApprovalRuleVersion | None:
    return (
        db.query(ApprovalRuleVersion)
        .filter(ApprovalRuleVersion.is_active == True)
        .first()
    )


def get_approval_route(amount: float, db: Session) -> ApprovalRouteOut:
    version = get_active_rule_version(db)

    if not version:
        init_default_rule_version(db)
        version = get_active_rule_version(db)

    rules = (
        db.query(ApprovalRule)
        .filter(ApprovalRule.version_id == version.id)
        .order_by(ApprovalRule.min_amount)
        .all()
    )

    required_levels = _resolve_levels(amount, rules)
    return ApprovalRouteOut(
        amount=amount,
        required_levels=required_levels,
        description=_describe_route(required_levels, amount),
    )


def _resolve_levels(amount: float, rules: list[ApprovalRule]) -> list[ApprovalLevel]:
    required_levels = []
    for rule in rules:
        if amount >= rule.min_amount:
            if rule.max_amount is None or amount < rule.max_amount:
                required_levels = _build_chain_up_to(rule.level)
                break

    if not required_levels:
        required_levels = [APPROVAL_CHAIN[0]]

    return required_levels


def _build_chain_up_to(highest_level: ApprovalLevel) -> list[ApprovalLevel]:
    chain = []
    for level in APPROVAL_CHAIN:
        chain.append(level)
        if level == highest_level:
            break
    return chain


def _describe_route(levels: list[ApprovalLevel], amount: float) -> str:
    names = " → ".join(LEVEL_NAMES[l] for l in levels)
    return f"金额 ¥{amount:,.2f}，审批链路：{names}"


def create_approval_nodes(db: Session, order: PurchaseOrder) -> list[ApprovalNode]:
    version = get_active_rule_version(db)
    if not version:
        init_default_rule_version(db)
        version = get_active_rule_version(db)

    order.rule_version_id = version.id

    route = get_approval_route(order.total_amount, db)
    nodes = []
    for idx, level in enumerate(route.required_levels, start=1):
        node = ApprovalNode(
            order_id=order.id,
            level=level,
            mode=ApprovalNodeMode.SEQUENTIAL,
            status=ApprovalNodeStatus.PENDING,
            sort_order=idx,
        )
        db.add(node)
        nodes.append(node)

    db.flush()

    if nodes:
        order.current_node_id = nodes[0].id

    db.commit()
    return nodes


def _get_current_node(db: Session, order: PurchaseOrder, lock: bool = False) -> ApprovalNode | None:
    if order.current_node_id is None:
        return None
    from sqlalchemy.orm import selectinload
    query = (
        db.query(ApprovalNode)
        .options(selectinload(ApprovalNode.approvers))
        .filter(ApprovalNode.id == order.current_node_id)
    )
    if lock:
        query = query.with_for_update()
    return query.first()


def _check_idempotency_key(
    db: Session, idempotency_key: str | None, order_id: int,
) -> ApprovalRecord | None:
    if not idempotency_key:
        return None
    return (
        db.query(ApprovalRecord)
        .filter(ApprovalRecord.idempotency_key == idempotency_key)
        .filter(ApprovalRecord.order_id == order_id)
        .first()
    )


def _create_audit_record(
    db: Session, order: PurchaseOrder, node: ApprovalNode | None,
    approver: str, action_type: ApprovalActionType, comment: str | None,
    idempotency_key: str | None = None,
) -> ApprovalRecord:
    record = ApprovalRecord(
        order_id=order.id,
        node_id=node.id if node else None,
        approver=approver,
        action_type=action_type,
        idempotency_key=idempotency_key,
        comment=comment,
    )
    db.add(record)
    db.flush()
    return record


def _get_order_nodes(db: Session, order: PurchaseOrder) -> list[ApprovalNode]:
    return (
        db.query(ApprovalNode)
        .filter(ApprovalNode.order_id == order.id)
        .order_by(ApprovalNode.sort_order)
        .all()
    )


def _advance_to_next_node(db: Session, order: PurchaseOrder, current_node: ApprovalNode) -> None:
    nodes = _get_order_nodes(db, order)
    current_idx = None
    for i, n in enumerate(nodes):
        if n.id == current_node.id:
            current_idx = i
            break

    next_node = None
    if current_idx is not None:
        for n in nodes[current_idx + 1:]:
            if n.status == ApprovalNodeStatus.PENDING:
                next_node = n
                break

    if next_node is None:
        order.status = PurchaseStatus.APPROVED
        order.current_node_id = None
    else:
        order.current_node_id = next_node.id


def _handle_absent_approver(
    db: Session, node: ApprovalNode, approver: ApprovalNodeApprover,
) -> None:
    if not approver.backup_approver:
        return

    backup = ApprovalNodeApprover(
        node_id=node.id,
        approver=approver.backup_approver,
        acted=False,
        is_absent=False,
        backup_approver=None,
    )
    db.add(backup)
    approver.is_absent = True
    db.flush()


def _check_skip_threshold(db: Session, order: PurchaseOrder) -> None:
    version = get_active_rule_version(db)
    if not version:
        return

    if order.total_amount < version.min_skip_amount:
        raise ValueError(
            f"跳级审批仅适用于金额大于等于 ¥{version.min_skip_amount:,.2f} 的采购单，"
            f"当前金额为 ¥{order.total_amount:,.2f}"
        )

    nodes = _get_order_nodes(db, order)
    current_idx = None
    for i, n in enumerate(nodes):
        if n.id == order.current_node_id:
            current_idx = i
            break

    if current_idx is not None:
        remaining = len(nodes) - current_idx - 1
        if remaining < 1:
            raise ValueError("当前已是最后一级审批，无法跳级")


def _cleanup_pending_orders_on_rollback(db: Session, old_version_id: int) -> list[int]:
    pending_orders = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.rule_version_id == old_version_id)
        .filter(PurchaseOrder.status == PurchaseStatus.PENDING)
        .all()
    )

    affected_order_ids = []
    for order in pending_orders:
        db.query(ApprovalNodeApprover).filter(
            ApprovalNodeApprover.node_id.in_(
                db.query(ApprovalNode.id).filter(ApprovalNode.order_id == order.id)
            )
        ).delete(synchronize_session=False)

        db.query(ApprovalNode).filter(
            ApprovalNode.order_id == order.id
        ).delete(synchronize_session=False)

        order.rule_version_id = None
        order.current_node_id = None
        order.status = PurchaseStatus.DRAFT
        affected_order_ids.append(order.id)

    db.flush()
    return affected_order_ids


def process_approve(
    db: Session, order: PurchaseOrder, approver: str, comment: str | None,
    idempotency_key: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    approver_entry = None
    if node.mode == ApprovalNodeMode.COUNTERSIGN:
        approver_entry = db.query(ApprovalNodeApprover).filter(
            ApprovalNodeApprover.node_id == node.id,
            ApprovalNodeApprover.approver == approver,
        ).first()
        if approver_entry and approver_entry.is_absent:
            _handle_absent_approver(db, node, approver_entry)
            approver = approver_entry.backup_approver

    _create_audit_record(
        db, order, node, approver, ApprovalActionType.APPROVE, comment, idempotency_key,
    )

    if node.mode == ApprovalNodeMode.SEQUENTIAL:
        _mark_approver(db, node, approver, ApprovalActionType.APPROVE, comment)
        node.status = ApprovalNodeStatus.APPROVED
        _advance_to_next_node(db, order, node)

    elif node.mode == ApprovalNodeMode.COUNTERSIGN:
        _mark_approver(db, node, approver, ApprovalActionType.APPROVE, comment)
        db.flush()
        pending = db.query(ApprovalNodeApprover).filter(
            ApprovalNodeApprover.node_id == node.id,
            ApprovalNodeApprover.acted == False,
            ApprovalNodeApprover.is_absent == False,
        ).count()
        if pending == 0:
            node.status = ApprovalNodeStatus.APPROVED
            _advance_to_next_node(db, order, node)

    elif node.mode == ApprovalNodeMode.PARALLEL:
        node.status = ApprovalNodeStatus.APPROVED
        _mark_approver(db, node, approver, ApprovalActionType.APPROVE, comment)
        _advance_to_next_node(db, order, node)

    db.commit()
    db.refresh(order)
    return order


def process_reject(
    db: Session, order: PurchaseOrder, approver: str, comment: str | None,
    idempotency_key: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, approver, ApprovalActionType.REJECT, comment, idempotency_key,
    )
    _mark_approver(db, node, approver, ApprovalActionType.REJECT, comment)

    node.status = ApprovalNodeStatus.REJECTED
    order.status = PurchaseStatus.REJECTED
    order.current_node_id = None

    db.commit()
    db.refresh(order)
    return order


def process_countersign(
    db: Session, order: PurchaseOrder, initiator: str,
    approvers: list[str], comment: str | None,
    idempotency_key: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, initiator, ApprovalActionType.COUNTERSIGN, comment, idempotency_key,
    )

    node.mode = ApprovalNodeMode.COUNTERSIGN

    existing_approvers = {
        a.approver for a in
        db.query(ApprovalNodeApprover).filter(ApprovalNodeApprover.node_id == node.id).all()
    }

    for name in approvers:
        if name not in existing_approvers:
            is_acted = name == initiator
            db.add(ApprovalNodeApprover(
                node_id=node.id,
                approver=name,
                acted=is_acted,
                is_absent=False,
                backup_approver=None,
                action_type=ApprovalActionType.COUNTERSIGN if is_acted else None,
                comment=comment if is_acted else None,
                acted_at=datetime.utcnow() if is_acted else None,
            ))
        else:
            if name == initiator:
                a = db.query(ApprovalNodeApprover).filter(
                    ApprovalNodeApprover.node_id == node.id,
                    ApprovalNodeApprover.approver == name,
                ).first()
                if a and not a.acted:
                    a.acted = True
                    a.action_type = ApprovalActionType.COUNTERSIGN
                    a.comment = comment
                    a.acted_at = datetime.utcnow()

    db.flush()

    pending = db.query(ApprovalNodeApprover).filter(
        ApprovalNodeApprover.node_id == node.id,
        ApprovalNodeApprover.acted == False,
        ApprovalNodeApprover.is_absent == False,
    ).count()
    if pending == 0:
        node.status = ApprovalNodeStatus.APPROVED
        _advance_to_next_node(db, order, node)

    db.commit()
    db.refresh(order)
    return order


def process_add_sign(
    db: Session, order: PurchaseOrder, approver: str,
    added_approver: str, comment: str | None,
    backup_approver: str | None = None,
    idempotency_key: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, approver, ApprovalActionType.ADD_SIGN, comment, idempotency_key,
    )

    existing = db.query(ApprovalNodeApprover).filter(
        ApprovalNodeApprover.node_id == node.id,
    ).all()

    if not existing:
        db.add(ApprovalNodeApprover(
            node_id=node.id,
            approver=approver,
            acted=False,
            is_absent=False,
            backup_approver=None,
        ))

    db.add(ApprovalNodeApprover(
        node_id=node.id,
        approver=added_approver,
        acted=False,
        is_absent=False,
        backup_approver=backup_approver,
    ))

    node.mode = ApprovalNodeMode.COUNTERSIGN

    db.commit()
    db.refresh(order)
    return order


def process_parallel_sign(
    db: Session, order: PurchaseOrder, initiator: str,
    approvers: list[str], comment: str | None,
    idempotency_key: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, initiator, ApprovalActionType.PARALLEL_SIGN, comment, idempotency_key,
    )

    node.mode = ApprovalNodeMode.PARALLEL

    for name in approvers:
        db.add(ApprovalNodeApprover(
            node_id=node.id,
            approver=name,
            acted=False,
            is_absent=False,
            backup_approver=None,
        ))

    db.commit()
    db.refresh(order)
    return order


def process_skip(
    db: Session, order: PurchaseOrder, approver: str, comment: str | None,
    idempotency_key: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    _check_skip_threshold(db, order)

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, approver, ApprovalActionType.SKIP, comment, idempotency_key,
    )

    node.status = ApprovalNodeStatus.SKIPPED
    _advance_to_next_node(db, order, node)

    db.commit()
    db.refresh(order)
    return order


def process_return(
    db: Session, order: PurchaseOrder, approver: str, comment: str | None,
    idempotency_key: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, approver, ApprovalActionType.RETURN, comment, idempotency_key,
    )

    nodes = _get_order_nodes(db, order)
    prev_node = None
    for n in nodes:
        if n.id == node.id:
            break
        if n.status in (ApprovalNodeStatus.APPROVED, ApprovalNodeStatus.SKIPPED):
            prev_node = n

    if prev_node is None:
        raise ValueError("没有可退回的上一级审批节点")

    prev_node.status = ApprovalNodeStatus.PENDING
    db.query(ApprovalNodeApprover).filter(
        ApprovalNodeApprover.node_id == prev_node.id,
    ).delete()

    order.current_node_id = prev_node.id

    db.commit()
    db.refresh(order)
    return order


def process_withdraw(
    db: Session, order: PurchaseOrder, applicant: str, comment: str | None,
    idempotency_key: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    if order.applicant != applicant:
        raise ValueError("仅申请人可撤回采购单")

    _create_audit_record(
        db, order, None, applicant, ApprovalActionType.WITHDRAW, comment, idempotency_key,
    )

    db.query(ApprovalNodeApprover).filter(
        ApprovalNodeApprover.node_id.in_(
            db.query(ApprovalNode.id).filter(ApprovalNode.order_id == order.id)
        )
    ).delete(synchronize_session=False)

    db.query(ApprovalNode).filter(ApprovalNode.order_id == order.id).delete()

    order.status = PurchaseStatus.DRAFT
    order.current_node_id = None
    order.rule_version_id = None

    db.commit()
    db.refresh(order)
    return order


def _mark_approver(
    db: Session, node: ApprovalNode, approver: str,
    action_type: ApprovalActionType, comment: str | None,
) -> None:
    existing = db.query(ApprovalNodeApprover).filter(
        ApprovalNodeApprover.node_id == node.id,
        ApprovalNodeApprover.approver == approver,
    ).first()

    if existing:
        existing.acted = True
        existing.action_type = action_type
        existing.comment = comment
        existing.acted_at = datetime.utcnow()
    else:
        db.add(ApprovalNodeApprover(
            node_id=node.id,
            approver=approver,
            acted=True,
            action_type=action_type,
            comment=comment,
            acted_at=datetime.utcnow(),
        ))


def create_rule_version(db: Session, data: RuleVersionCreate) -> ApprovalRuleVersion:
    max_ver = db.query(ApprovalRuleVersion).order_by(
        ApprovalRuleVersion.version_number.desc()
    ).first()
    next_version = (max_ver.version_number + 1) if max_ver else 1

    current_active = get_active_rule_version(db)
    if current_active:
        current_active.is_active = False

    version = ApprovalRuleVersion(
        version_number=next_version,
        is_active=True,
        min_skip_amount=data.min_skip_amount,
        description=data.description,
    )
    db.add(version)
    db.flush()

    for rule_data in data.rules:
        rule = ApprovalRule(
            version_id=version.id,
            level=rule_data.level,
            min_amount=rule_data.min_amount,
            max_amount=rule_data.max_amount,
        )
        db.add(rule)

    db.commit()
    db.refresh(version)
    return version


def activate_rule_version(db: Session, version_id: int) -> ApprovalRuleVersion:
    version = db.query(ApprovalRuleVersion).filter(ApprovalRuleVersion.id == version_id).first()
    if not version:
        raise ValueError("审批规则版本不存在")

    current_active = get_active_rule_version(db)
    if current_active:
        current_active.is_active = False

    version.is_active = True
    db.commit()
    db.refresh(version)
    return version


def deactivate_rule_version(db: Session, version_id: int) -> ApprovalRuleVersion:
    version = db.query(ApprovalRuleVersion).filter(ApprovalRuleVersion.id == version_id).first()
    if not version:
        raise ValueError("审批规则版本不存在")

    version.is_active = False
    db.commit()
    db.refresh(version)
    return version


def rollback_rule_version(db: Session, version_id: int) -> tuple[ApprovalRuleVersion, list[int]]:
    current_active = get_active_rule_version(db)
    old_version_id = current_active.id if current_active else None

    version = activate_rule_version(db, version_id)

    affected_order_ids = []
    if old_version_id:
        affected_order_ids = _cleanup_pending_orders_on_rollback(db, old_version_id)

    db.commit()
    db.refresh(version)
    return version, affected_order_ids
