#!/usr/bin/env python
"""
生产环境数据回填脚本

使用方法:
    .venv/bin/python scripts/seed_production_data.py

功能:
    1. 初始化默认审批规则版本（如不存在）
    2. 预置常用审批人配置
    3. 初始化系统默认数据

警告:
    此脚本仅用于首次部署或数据恢复。
    执行前请确保已备份数据库。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ApprovalRuleVersion, ApprovalRule, ApprovalLevel
from app.services.approval import init_default_rule_version, DEFAULT_RULES


def seed_approval_rules(db: Session, force: bool = False) -> ApprovalRuleVersion:
    existing = db.query(ApprovalRuleVersion).first()
    if existing and not force:
        print(f"✅ 审批规则版本已存在 (v{existing.version_number})，跳过初始化")
        return existing

    if force and existing:
        print(f"⚠️  强制重置模式，将删除现有版本 v{existing.version_number}")
        db.query(ApprovalRule).filter(ApprovalRule.version_id == existing.id).delete()
        db.delete(existing)
        db.commit()

    version = ApprovalRuleVersion(
        version_number=1,
        is_active=True,
        min_skip_amount=50000,
        description="生产环境默认审批规则",
    )
    db.add(version)
    db.flush()

    for rule_data in DEFAULT_RULES:
        rule = ApprovalRule(version_id=version.id, **rule_data)
        db.add(rule)

    db.commit()
    db.refresh(version)
    print(f"✅ 已创建审批规则版本 v{version.version_number}")

    for rule in version.rules:
        level_name = {
            ApprovalLevel.MANAGER: "经理",
            ApprovalLevel.DIRECTOR: "总监",
            ApprovalLevel.VP: "副总裁",
            ApprovalLevel.CEO: "CEO",
        }[rule.level]
        max_str = f"¥{rule.max_amount:,.2f}" if rule.max_amount else "∞"
        print(f"   └─ {level_name}: ¥{rule.min_amount:,.2f} ~ {max_str}")

    return version


def seed_production_data(db: Session, force: bool = False) -> None:
    print("=" * 60)
    print("🚀 开始执行生产环境数据回填")
    print("=" * 60)
    print()

    try:
        seed_approval_rules(db, force=force)
        print()
        print("=" * 60)
        print("✅ 数据回填完成")
        print("=" * 60)

    except Exception as e:
        db.rollback()
        print(f"❌ 数据回填失败: {e}")
        raise


if __name__ == "__main__":
    force_reset = "--force" in sys.argv

    if force_reset:
        confirm = input("⚠️  确认要强制重置所有数据吗？此操作不可恢复！请输入 YES 确认: ")
        if confirm != "YES":
            print("操作已取消")
            sys.exit(0)

    db = SessionLocal()
    try:
        seed_production_data(db, force=force_reset)
    finally:
        db.close()
