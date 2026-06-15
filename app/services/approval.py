import hashlib
import json
import random
import time
from datetime import datetime
from functools import wraps

from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError, DBAPIError

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

MAX_LOCK_RETRY_ATTEMPTS = 5
LOCK_RETRY_BASE_DELAY_MS = 50
LOCK_RETRY_JITTER_MS = 30

DEADLOCK_ERROR_CODES = {
    "mysql": ["1213", "1205"],
    "postgresql": ["40P01", "55P03"],
    "sqlite": ["database is locked"],
}

DEFAULT_MIN_SKIP_LEVELS = 2

VP_DELEGATE_LEVEL = ApprovalLevel.DIRECTOR
VP_SECONDARY_FALLBACK_LEVEL = ApprovalLevel.MANAGER


def _is_deadlock_error(exception: Exception) -> bool:
    error_str = str(exception).lower()
    for dialect, codes in DEADLOCK_ERROR_CODES.items():
        for code in codes:
            if code.lower() in error_str:
                return True
    if "deadlock" in error_str or "lock wait timeout" in error_str:
        return True
    return False


def with_lock_retry(max_retries: int = MAX_LOCK_RETRY_ATTEMPTS):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (OperationalError, DBAPIError) as e:
                    last_exception = e
                    if not _is_deadlock_error(e):
                        raise
                    if attempt < max_retries - 1:
                        base_delay = (LOCK_RETRY_BASE_DELAY_MS * (2 ** attempt)) / 1000.0
                        jitter = random.uniform(0, LOCK_RETRY_JITTER_MS) / 1000.0
                        time.sleep(base_delay + jitter)
                        if "db" in kwargs:
                            kwargs["db"].rollback()
                        elif args:
                            for arg in args:
                                if isinstance(arg, Session):
                                    arg.rollback()
                                    break
                    else:
                        raise RuntimeError(
                            f"获取锁失败，已重试 {max_retries} 次: {e}"
                        ) from e
            raise last_exception
        return wrapper
    return decorator


def _hash_payload(payload: dict | None) -> str | None:
    if payload is None:
        return None
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def init_default_rule_version(db: Session) -> ApprovalRuleVersion:
    existing = db.query(ApprovalRuleVersion).first()
    if existing:
        return existing

    version = ApprovalRuleVersion(
        version_number=1,
        is_active=True,
        min_skip_amount=50000,
        min_skip_levels=DEFAULT_MIN_SKIP_LEVELS,
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
    payload: dict | None = None,
) -> ApprovalRecord | None:
    if not idempotency_key:
        return None

    record = (
        db.query(ApprovalRecord)
        .filter(ApprovalRecord.idempotency_key == idempotency_key)
        .first()
    )

    if record is None:
        return None

    if record.order_id != order_id:
        raise ValueError("幂等键已被其他订单使用，请使用唯一的幂等键")

    if payload is not None and record.idempotency_payload_hash is not None:
        payload_hash = _hash_payload(payload)
        if payload_hash != record.idempotency_payload_hash:
            raise ValueError(
                "幂等键已存在但请求内容不匹配，"
                "请使用不同的幂等键发起新请求"
            )

    return record


def _create_audit_record(
    db: Session, order: PurchaseOrder, node: ApprovalNode | None,
    approver: str, action_type: ApprovalActionType, comment: str | None,
    idempotency_key: str | None = None,
    idempotency_payload_hash: str | None = None,
) -> ApprovalRecord:
    record = ApprovalRecord(
        order_id=order.id,
        node_id=node.id if node else None,
        approver=approver,
        action_type=action_type,
        idempotency_key=idempotency_key,
        idempotency_payload_hash=idempotency_payload_hash,
        comment=comment,
    )
    db.add(record)
    db.flush()
    return record


def _log_idempotency_conflict(
    db: Session, order_id: int, idempotency_key: str | None,
    conflict_reason: str, payload: dict | None = None,
) -> None:
    payload_hash = _hash_payload(payload) if payload else None
    record = ApprovalRecord(
        order_id=order_id,
        node_id=None,
        approver="system",
        action_type=ApprovalActionType.APPROVE,
        idempotency_key=None,
        idempotency_payload_hash=payload_hash,
        comment=f"[IDEMPOTENCY_CONFLICT] key={idempotency_key}, reason={conflict_reason}",
    )
    db.add(record)
    db.commit()


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
) -> str | None:
    if approver.backup_approver:
        backup_name = approver.backup_approver
        backup_entry = db.query(ApprovalNodeApprover).filter(
            ApprovalNodeApprover.node_id == node.id,
            ApprovalNodeApprover.approver == backup_name,
        ).first()
        if backup_entry and backup_entry.is_absent:
            if node.level == ApprovalLevel.VP:
                fallback_name = f"{VP_SECONDARY_FALLBACK_LEVEL.value}_fallback"
                fallback = ApprovalNodeApprover(
                    node_id=node.id,
                    approver=fallback_name,
                    acted=False,
                    is_absent=False,
                    backup_approver=None,
                )
                db.add(fallback)
                approver.is_absent = True
                db.flush()
                return fallback_name
            return None

        if not backup_entry:
            backup = ApprovalNodeApprover(
                node_id=node.id,
                approver=backup_name,
                acted=False,
                is_absent=False,
                backup_approver=None,
            )
            db.add(backup)
        approver.is_absent = True
        db.flush()
        return backup_name

    if node.level == ApprovalLevel.VP:
        delegate_level = VP_DELEGATE_LEVEL
        delegate_name = f"{delegate_level.value}_delegate"

        existing_delegate = db.query(ApprovalNodeApprover).filter(
            ApprovalNodeApprover.node_id == node.id,
            ApprovalNodeApprover.approver == delegate_name,
        ).first()

        if existing_delegate and existing_delegate.is_absent:
            fallback_name = f"{VP_SECONDARY_FALLBACK_LEVEL.value}_fallback"
            existing_fallback = db.query(ApprovalNodeApprover).filter(
                ApprovalNodeApprover.node_id == node.id,
                ApprovalNodeApprover.approver == fallback_name,
            ).first()
            if not existing_fallback:
                fallback = ApprovalNodeApprover(
                    node_id=node.id,
                    approver=fallback_name,
                    acted=False,
                    is_absent=False,
                    backup_approver=None,
                )
                db.add(fallback)
                db.flush()
            approver.is_absent = True
            return fallback_name

        if not existing_delegate:
            delegate = ApprovalNodeApprover(
                node_id=node.id,
                approver=delegate_name,
                acted=False,
                is_absent=False,
                backup_approver=None,
            )
            db.add(delegate)
        approver.is_absent = True
        db.flush()
        return delegate_name

    return None


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

        min_skip_levels = getattr(version, "min_skip_levels", DEFAULT_MIN_SKIP_LEVELS)
        if remaining < min_skip_levels:
            raise ValueError(
                f"跳级审批要求至少剩余 {min_skip_levels} 级待审批，"
                f"当前仅剩余 {remaining} 级，组织架构调整后请使用正常审批流程"
            )


def _cleanup_pending_orders_on_rollback(db: Session, old_version_id: int, dry_run: bool = False) -> list[int]:
    affected_orders = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.rule_version_id == old_version_id)
        .filter(PurchaseOrder.status.in_([
            PurchaseStatus.PENDING,
        ]))
        .all()
    )

    affected_order_ids = []
    for order in affected_orders:
        has_active_nodes = (
            db.query(ApprovalNode)
            .filter(ApprovalNode.order_id == order.id)
            .filter(ApprovalNode.status == ApprovalNodeStatus.PENDING)
            .first() is not None
        )

        has_incomplete_nodes = (
            db.query(ApprovalNode)
            .filter(ApprovalNode.order_id == order.id)
            .filter(ApprovalNode.status.notin_([
                ApprovalNodeStatus.APPROVED,
                ApprovalNodeStatus.REJECTED,
                ApprovalNodeStatus.SKIPPED,
            ]))
            .first() is not None
        )

        if order.status == PurchaseStatus.PENDING or has_active_nodes or has_incomplete_nodes:
            if not dry_run:
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

    if not dry_run:
        db.flush()
    return affected_order_ids


def recalculate_pending_orders_route(db: Session) -> list[dict]:
    pending_orders = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.status == PurchaseStatus.PENDING)
        .all()
    )

    recalculated = []
    active_version = get_active_rule_version(db)
    if not active_version:
        return recalculated

    for order in pending_orders:
        old_nodes = _get_order_nodes(db, order)
        approved_levels = set()
        for node in old_nodes:
            if node.status == ApprovalNodeStatus.APPROVED:
                approved_levels.add(node.level)

        current_route = get_approval_route(order.total_amount, db)
        new_levels = current_route.required_levels

        if [n.level for n in old_nodes] != new_levels:
            db.query(ApprovalNodeApprover).filter(
                ApprovalNodeApprover.node_id.in_(
                    db.query(ApprovalNode.id).filter(ApprovalNode.order_id == order.id)
                )
            ).delete(synchronize_session=False)

            db.query(ApprovalNode).filter(
                ApprovalNode.order_id == order.id
            ).delete(synchronize_session=False)

            order.rule_version_id = active_version.id

            new_nodes = []
            for idx, level in enumerate(new_levels, start=1):
                node = ApprovalNode(
                    order_id=order.id,
                    level=level,
                    mode=ApprovalNodeMode.SEQUENTIAL,
                    status=ApprovalNodeStatus.APPROVED if level in approved_levels else ApprovalNodeStatus.PENDING,
                    sort_order=idx,
                )
                db.add(node)
                new_nodes.append(node)

            db.flush()

            current_node = None
            for node in new_nodes:
                if node.status == ApprovalNodeStatus.PENDING:
                    current_node = node
                    break

            order.current_node_id = current_node.id if current_node else None
            if not current_node:
                order.status = PurchaseStatus.APPROVED

            recalculated.append({
                "order_id": order.id,
                "old_levels": [n.level for n in old_nodes],
                "new_levels": new_levels,
            })

    db.flush()
    return recalculated


@with_lock_retry(max_retries=MAX_LOCK_RETRY_ATTEMPTS)
def process_approve(
    db: Session, order: PurchaseOrder, approver: str, comment: str | None,
    idempotency_key: str | None = None,
    idempotency_payload_hash: str | None = None,
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
            backup_approver = _handle_absent_approver(db, node, approver_entry)
            if backup_approver:
                approver = backup_approver

    _create_audit_record(
        db, order, node, approver, ApprovalActionType.APPROVE, comment,
        idempotency_key, idempotency_payload_hash,
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


@with_lock_retry(max_retries=MAX_LOCK_RETRY_ATTEMPTS)
def process_reject(
    db: Session, order: PurchaseOrder, approver: str, comment: str | None,
    idempotency_key: str | None = None,
    idempotency_payload_hash: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, approver, ApprovalActionType.REJECT, comment,
        idempotency_key, idempotency_payload_hash,
    )
    _mark_approver(db, node, approver, ApprovalActionType.REJECT, comment)

    node.status = ApprovalNodeStatus.REJECTED
    order.status = PurchaseStatus.REJECTED
    order.current_node_id = None

    db.commit()
    db.refresh(order)
    return order


@with_lock_retry(max_retries=MAX_LOCK_RETRY_ATTEMPTS)
def process_countersign(
    db: Session, order: PurchaseOrder, initiator: str,
    approvers: list[str], comment: str | None,
    idempotency_key: str | None = None,
    idempotency_payload_hash: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, initiator, ApprovalActionType.COUNTERSIGN, comment,
        idempotency_key, idempotency_payload_hash,
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


@with_lock_retry(max_retries=MAX_LOCK_RETRY_ATTEMPTS)
def process_add_sign(
    db: Session, order: PurchaseOrder, approver: str,
    added_approver: str, comment: str | None,
    backup_approver: str | None = None,
    idempotency_key: str | None = None,
    idempotency_payload_hash: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, approver, ApprovalActionType.ADD_SIGN, comment,
        idempotency_key, idempotency_payload_hash,
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


@with_lock_retry(max_retries=MAX_LOCK_RETRY_ATTEMPTS)
def process_parallel_sign(
    db: Session, order: PurchaseOrder, initiator: str,
    approvers: list[str], comment: str | None,
    idempotency_key: str | None = None,
    idempotency_payload_hash: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, initiator, ApprovalActionType.PARALLEL_SIGN, comment,
        idempotency_key, idempotency_payload_hash,
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


@with_lock_retry(max_retries=MAX_LOCK_RETRY_ATTEMPTS)
def process_skip(
    db: Session, order: PurchaseOrder, approver: str, comment: str | None,
    idempotency_key: str | None = None,
    idempotency_payload_hash: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    _check_skip_threshold(db, order)

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, approver, ApprovalActionType.SKIP, comment,
        idempotency_key, idempotency_payload_hash,
    )

    node.status = ApprovalNodeStatus.SKIPPED
    _advance_to_next_node(db, order, node)

    db.commit()
    db.refresh(order)
    return order


@with_lock_retry(max_retries=MAX_LOCK_RETRY_ATTEMPTS)
def process_return(
    db: Session, order: PurchaseOrder, approver: str, comment: str | None,
    idempotency_key: str | None = None,
    idempotency_payload_hash: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    node = _get_current_node(db, order, lock=True)
    if not node:
        raise ValueError("采购单当前无待审批节点")

    _create_audit_record(
        db, order, node, approver, ApprovalActionType.RETURN, comment,
        idempotency_key, idempotency_payload_hash,
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


@with_lock_retry(max_retries=MAX_LOCK_RETRY_ATTEMPTS)
def process_withdraw(
    db: Session, order: PurchaseOrder, applicant: str, comment: str | None,
    idempotency_key: str | None = None,
    idempotency_payload_hash: str | None = None,
) -> PurchaseOrder:
    existing = _check_idempotency_key(db, idempotency_key, order.id)
    if existing:
        return order

    if order.applicant != applicant:
        raise ValueError("仅申请人可撤回采购单")

    _create_audit_record(
        db, order, None, applicant, ApprovalActionType.WITHDRAW, comment,
        idempotency_key, idempotency_payload_hash,
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
        min_skip_levels=data.min_skip_levels,
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


def rollback_rule_version(db: Session, version_id: int, dry_run: bool = False) -> tuple[ApprovalRuleVersion, list[int], list[dict]]:
    current_active = get_active_rule_version(db)
    old_version_id = current_active.id if current_active else None

    version = activate_rule_version(db, version_id)

    affected_order_ids = []
    if old_version_id:
        affected_order_ids = _cleanup_pending_orders_on_rollback(db, old_version_id, dry_run=dry_run)

    recalculated = []
    if not dry_run:
        recalculated = recalculate_pending_orders_route(db)

    if not dry_run:
        db.commit()
        db.refresh(version)
    return version, affected_order_ids, recalculated
