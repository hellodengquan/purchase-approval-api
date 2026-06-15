#!/usr/bin/env python
"""
生产部署门禁脚本

在 CI/CD 流水线中作为部署前置校验使用。
任何一项检查失败都会以非零退出码终止部署。

使用方法:
    .venv/bin/python scripts/deploy_gate.py

检查项:
    1. Alembic 孤儿 revision 检测
    2. 数据库迁移状态一致性
    3. Alembic 升级 dry-run 检测

退出码:
    0 - 所有检查通过
    1 - 检查失败，阻止部署
"""
import subprocess
import sys
import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_orphan_revisions() -> bool:
    print("=" * 60)
    print("🔍 [门禁检查] Alembic 孤儿 Revision 检测")
    print("=" * 60)

    script_path = os.path.join(PROJECT_DIR, "scripts", "check_orphan_revisions.py")
    result = subprocess.run(
        [sys.executable, script_path],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
    )

    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if result.returncode != 0:
        print("❌ 孤儿 revision 检测脚本执行失败")
        return False

    if "发现" in result.stdout and "孤儿" in result.stdout and "0 个孤儿" not in result.stdout:
        print("❌ 存在孤儿 revision，请清理后再部署")
        return False

    if "未发现孤儿" in result.stdout or "0 个孤儿" in result.stdout:
        print("✅ 孤儿 revision 检测通过")
        return True

    print("⚠️  无法确认孤儿 revision 状态，请人工检查")
    return False


def check_alembic_upgrade_dry_run() -> bool:
    print()
    print("=" * 60)
    print("🔍 [门禁检查] Alembic 升级 Dry-Run 检测")
    print("=" * 60)

    script_path = os.path.join(PROJECT_DIR, "scripts", "alembic_upgrade_dry_run.py")

    if not os.path.exists(script_path):
        print("⚠️  未找到 alembic_upgrade_dry_run.py 脚本，跳过此检查")
        return True

    result = subprocess.run(
        [sys.executable, script_path],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
    )

    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if result.returncode != 0:
        print("❌ Alembic 升级 dry-run 检测失败")
        return False

    print("✅ Alembic 升级 dry-run 检测通过")
    return True


def check_migration_heads() -> bool:
    print()
    print("=" * 60)
    print("🔍 [门禁检查] 迁移链路完整性检测")
    print("=" * 60)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "heads"],
            capture_output=True,
            text=True,
            cwd=PROJECT_DIR,
        )

        heads_output = result.stdout.strip()
        if not heads_output:
            print("⚠️  未检测到任何 head，请检查迁移配置")
            return False

        head_count = len([line for line in heads_output.split("\n") if line.strip()])
        if head_count > 1:
            print(f"❌ 检测到 {head_count} 个 head，存在分支冲突")
            print(f"   Heads: {heads_output}")
            return False

        print(f"✅ 迁移链路完整，唯一 head: {heads_output}")
        return True

    except Exception as e:
        print(f"❌ 迁移链路检测异常: {e}")
        return False


def main():
    print("=" * 60)
    print("🚧 生产部署门禁检查")
    print("=" * 60)
    print()

    checks = [
        ("孤儿 Revision 检测", check_orphan_revisions),
        ("迁移链路完整性", check_migration_heads),
        ("Alembic 升级 Dry-Run", check_alembic_upgrade_dry_run),
    ]

    results = {}
    for name, check_fn in checks:
        try:
            results[name] = check_fn()
        except Exception as e:
            print(f"❌ 检查 [{name}] 发生异常: {e}")
            results[name] = False

    print()
    print("=" * 60)
    print("📊 部署门禁检查结果汇总")
    print("=" * 60)

    all_passed = True
    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"   {name}: {status}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("🎉 所有门禁检查通过，可以部署")
        sys.exit(0)
    else:
        print("🚫 存在未通过的门禁检查，禁止部署")
        sys.exit(1)


if __name__ == "__main__":
    main()
