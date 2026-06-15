from sqlalchemy.orm import Session

from app.models import ApprovalLevel, ApprovalRule, ApprovalRecord, ApprovalAction, PurchaseOrder, PurchaseStatus
from app.schemas import ApprovalRuleCreate, ApprovalRouteOut


DEFAULT_RULES = [
    {"level": ApprovalLevel.MANAGER, "min_amount": 0, "max_amount": 5000},
    {"level": ApprovalLevel.DIRECTOR, "min_amount": 5000, "max_amount": 50000},
    {"level": ApprovalLevel.VP, "min_amount": 50000, "max_amount": 200000},
    {"level": ApprovalLevel.CEO, "min_amount": 200000, "max_amount": None},
]

APPROVAL_CHAIN = [
    ApprovalLevel.MANAGER,
    ApprovalLevel.DIRECTOR,
    ApprovalLevel.VP,
    ApprovalLevel.CEO,
]


def init_default_rules(db: Session) -> None:
    existing = db.query(ApprovalRule).first()
    if existing:
        return
    for rule_data in DEFAULT_RULES:
        rule = ApprovalRule(**rule_data)
        db.add(rule)
    db.commit()


def get_approval_route(amount: float, db: Session) -> ApprovalRouteOut:
    rules = (
        db.query(ApprovalRule)
        .filter(ApprovalRule.is_active == True)
        .order_by(ApprovalRule.min_amount)
        .all()
    )

    if not rules:
        return _get_default_route(amount)

    required_levels = []
    for rule in rules:
        if amount >= rule.min_amount:
            if rule.max_amount is None or amount < rule.max_amount:
                required_levels = _build_chain_up_to(rule.level)
                break

    if not required_levels:
        required_levels = [APPROVAL_CHAIN[0]]

    return ApprovalRouteOut(
        amount=amount,
        required_levels=required_levels,
        description=_describe_route(required_levels, amount),
    )


def _build_chain_up_to(highest_level: ApprovalLevel) -> list[ApprovalLevel]:
    chain = []
    for level in APPROVAL_CHAIN:
        chain.append(level)
        if level == highest_level:
            break
    return chain


def _get_default_route(amount: float) -> ApprovalRouteOut:
    if amount < 5000:
        levels = [ApprovalLevel.MANAGER]
    elif amount < 50000:
        levels = [ApprovalLevel.MANAGER, ApprovalLevel.DIRECTOR]
    elif amount < 200000:
        levels = [ApprovalLevel.MANAGER, ApprovalLevel.DIRECTOR, ApprovalLevel.VP]
    else:
        levels = [ApprovalLevel.MANAGER, ApprovalLevel.DIRECTOR, ApprovalLevel.VP, ApprovalLevel.CEO]

    return ApprovalRouteOut(
        amount=amount,
        required_levels=levels,
        description=_describe_route(levels, amount),
    )


def _describe_route(levels: list[ApprovalLevel], amount: float) -> str:
    level_names = {
        ApprovalLevel.MANAGER: "经理",
        ApprovalLevel.DIRECTOR: "总监",
        ApprovalLevel.VP: "副总裁",
        ApprovalLevel.CEO: "CEO",
    }
    names = " → ".join(level_names[l] for l in levels)
    return f"金额 ¥{amount:,.2f}，审批链路：{names}"


def determine_initial_level(amount: float, db: Session) -> ApprovalLevel:
    route = get_approval_route(amount, db)
    return route.required_levels[0]


def get_next_level(current_level: ApprovalLevel, amount: float, db: Session) -> ApprovalLevel | None:
    route = get_approval_route(amount, db)
    required = route.required_levels
    try:
        idx = required.index(current_level)
    except ValueError:
        return None
    if idx + 1 < len(required):
        return required[idx + 1]
    return None


def is_final_level(level: ApprovalLevel, amount: float, db: Session) -> bool:
    route = get_approval_route(amount, db)
    return level == route.required_levels[-1]


def process_approval(
    db: Session,
    order: PurchaseOrder,
    approver: str,
    action: ApprovalAction,
    comment: str | None,
) -> PurchaseOrder:
    current = order.current_level
    if current is None:
        raise ValueError("采购单当前无待审批级别")

    record = ApprovalRecord(
        order_id=order.id,
        level=current,
        approver=approver,
        action=action,
        comment=comment,
    )
    db.add(record)

    if action == ApprovalAction.REJECT:
        order.status = PurchaseStatus.REJECTED
        order.current_level = None
    elif action == ApprovalAction.APPROVE:
        if is_final_level(current, order.total_amount, db):
            order.status = PurchaseStatus.APPROVED
            order.current_level = None
        else:
            next_level = get_next_level(current, order.total_amount, db)
            order.current_level = next_level

    db.commit()
    db.refresh(order)
    return order


def create_or_update_rule(db: Session, rule_data: ApprovalRuleCreate) -> ApprovalRule:
    existing = db.query(ApprovalRule).filter(ApprovalRule.level == rule_data.level).first()
    if existing:
        existing.min_amount = rule_data.min_amount
        existing.max_amount = rule_data.max_amount
        db.commit()
        db.refresh(existing)
        return existing

    rule = ApprovalRule(
        level=rule_data.level,
        min_amount=rule_data.min_amount,
        max_amount=rule_data.max_amount,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule
