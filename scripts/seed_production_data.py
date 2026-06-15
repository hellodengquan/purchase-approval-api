#!/usr/bin/env python
"""
生产环境数据回填脚本

使用方法:
    .venv/bin/python scripts/seed_production_data.py [--dry-run] [--force] [--yes]

功能:
    1. 初始化默认审批规则版本（如不存在）
    2. 预置常用审批人配置
    3. 支持 dry-run 预览模式
    4. 支持失败自动回滚
    5. 支持强制重置

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


def seed_approval_rules(db: Session, force: bool = False, dry_run: bool = False) -> ApprovalRuleVersion | None:
    existing = db.query(ApprovalRuleVersion).first()

    if existing and not force:
        print(f"✅ 审批规则版本已存在 (v{existing.version_number})，跳过初始化")
        return existing

    if force and existing:
        print(f"⚠️  强制重置模式，将删除现有版本 v{existing.version_number}")
        if dry_run:
            print(f"   [DRY-RUN] 将删除版本 v{existing.version_number} 及其规则")
        else:
            db.query(ApprovalRule).filter(ApprovalRule.version_id == existing.id).delete()
            db.delete(existing)
            db.flush()

    if dry_run:
        print("   [DRY-RUN] 将创建新版本 v1")
        for rule_data in DEFAULT_RULES:
            level_name = {
                ApprovalLevel.MANAGER: "经理",
                ApprovalLevel.DIRECTOR: "总监",
                ApprovalLevel.VP: "副总裁",
                ApprovalLevel.CEO: "CEO",
            }[rule_data["level"]]
            max_str = f"¥{rule_data['max_amount']:,.2f}" if rule_data["max_amount"] else "∞"
            print(f"   [DRY-RUN]   └─ {level_name}: ¥{rule_data['min_amount']:,.2f} ~ {max_str}")
        return None

    version = ApprovalRuleVersion(
        version_number=1,
        is_active=True,
        min_skip_amount=50000,
        min_skip_levels=2,
        description="生产环境默认审批规则",
    )
    db.add(version)
    db.flush()

    for rule_data in DEFAULT_RULES:
        rule = ApprovalRule(version_id=version.id, **rule_data)
        db.add(rule)

    db.flush()
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


def seed_production_data(db: Session, force: bool = False, dry_run: bool = False) -> bool:
    print("=" * 60)
    mode = "🔍 DRY-RUN 模式" if dry_run else "🚀 生产模式"
    print(f"{mode} - 开始执行生产环境数据回填")
    print("=" * 60)
    print()

    try:
        result = seed_approval_rules(db, force=force, dry_run=dry_run)

        if result is None and dry_run:
            print()
            print("📋 DRY-RUN 预览完成，未实际写入数据库")
        else:
            if not dry_run:
                db.commit()

        print()
        print("=" * 60)
        if dry_run:
            print("📋 DRY-RUN 执行完成")
        else:
            print("✅ 数据回填完成")
        print("=" * 60)
        return True

    except Exception as e:
        db.rollback()
        print()
        print("=" * 60)
        print(f"❌ 数据回填失败，已自动回滚: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    force_reset = "--force" in sys.argv
    dry_run_mode = "--dry-run" in sys.argv
    auto_yes = "--yes" in sys.argv

    if force_reset and not auto_yes:
        confirm = input("⚠️  确认要强制重置所有数据吗？此操作不可恢复！请输入 YES 确认: ")
        if confirm != "YES":
            print("操作已取消")
            sys.exit(0)

    if dry_run_mode:
        print("🔍 DRY-RUN 模式：仅预览，不实际写入数据库")
        print()

    db = SessionLocal()
    try:
        success = seed_production_data(db, force=force_reset, dry_run=dry_run_mode)
        sys.exit(0 if success else 1)
    finally:
        db.close()
