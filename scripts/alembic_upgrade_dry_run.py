#!/usr/bin/env python
"""
Alembic 升级 Dry-Run 检测脚本

在 CI/CD 部署门禁中使用，检测待执行的迁移步骤而不实际执行。

使用方法:
    .venv/bin/python scripts/alembic_upgrade_dry_run.py

功能:
    1. 检查当前数据库迁移版本
    2. 列出待执行的升级步骤
    3. 不实际执行任何迁移

退出码:
    0 - 检测成功（无论是否有待执行迁移）
    1 - 检测过程出错
"""
import subprocess
import sys
import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_alembic_current() -> str | None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def get_alembic_heads() -> str | None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def get_pending_upgrades() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
    )
    if result.returncode != 0:
        return []

    sql_output = result.stdout.strip()
    if not sql_output:
        return []

    statements = []
    current = []
    for line in sql_output.split("\n"):
        if line.startswith("--") and "Running upgrade" in line:
            if current:
                statements.append("\n".join(current))
                current = []
            statements.append(line)
        else:
            current.append(line)
    if current:
        statements.append("\n".join(current))

    return statements


def main():
    print("=" * 60)
    print("🔍 Alembic 升级 Dry-Run 检测")
    print("=" * 60)
    print()

    current = get_alembic_current()
    heads = get_alembic_heads()

    print(f"📌 当前版本: {current if current else '未初始化'}")
    print(f"📌 目标版本: {heads if heads else '未知'}")
    print()

    pending = get_pending_upgrades()

    if not pending:
        print("✅ 数据库已是最新版本，无需升级")
        print()
        print("=" * 60)
        print("📊 Dry-Run 结果: 0 个待执行迁移")
        print("=" * 60)
        return 0

    print(f"📋 发现 {len([s for s in pending if 'Running upgrade' in s])} 个待执行迁移步骤:")
    print()

    for stmt in pending:
        if "Running upgrade" in stmt:
            print(f"   {stmt}")
        else:
            for line in stmt.split("\n"):
                if line.strip() and not line.strip().startswith("--"):
                    print(f"      {line}")
    print()

    print("=" * 60)
    upgrade_count = len([s for s in pending if "Running upgrade" in s])
    print(f"📊 Dry-Run 结果: {upgrade_count} 个待执行迁移")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ Dry-Run 检测出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
