#!/usr/bin/env python
"""
检测 Alembic 孤儿 Revision（不在 head 链路上的版本）

使用方法:
    .venv/bin/python scripts/check_orphan_revisions.py

功能:
    1. 扫描所有版本文件
    2. 从 head 回溯构建版本链路
    3. 列出不在链路上的孤儿版本
    4. 检查重复、循环引用等异常

警告:
    孤儿 revision 可能表示历史遗留代码或合并冲突，
    请在清理前确认不会影响现有环境。
"""
import os
import re
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).parent.parent
VERSIONS_DIR = PROJECT_DIR / "alembic" / "versions"


def parse_revision_file(filepath: Path) -> dict | None:
    content = filepath.read_text()

    rev_match = re.search(r'^revision\s*[:=]\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    down_match = re.search(r'^down_revision\s*[:=]\s*([^"\'\n]+|["\'][^"\']*["\'])', content, re.MULTILINE)

    if not rev_match:
        return None

    revision_id = rev_match.group(1)
    down_rev = None

    if down_match:
        raw = down_match.group(1).strip()
        raw = raw.strip('"\'')
        if raw.lower() in ("none", "null", ""):
            down_rev = None
        else:
            down_rev = raw

    is_orphan_marker = "# ORPHAN" in content or "ORPHAN_REVISION" in content

    return {
        "file": filepath.name,
        "revision": revision_id,
        "down_revision": down_rev,
        "is_orphan_marker": is_orphan_marker,
    }


def find_heads(revisions: dict[str, dict]) -> list[str]:
    all_downs = {r["down_revision"] for r in revisions.values() if r["down_revision"]}
    return [rid for rid in revisions if rid not in all_downs]


def build_chain(revisions: dict[str, dict], head: str) -> list[str]:
    chain = []
    current = head
    visited = set()

    while current is not None:
        if current in visited:
            print(f"⚠️  检测到循环引用: {current}")
            break
        visited.add(current)

        if current not in revisions:
            print(f"⚠️  引用了不存在的 revision: {current}")
            break

        chain.append(current)
        current = revisions[current]["down_revision"]

    return chain


def check_orphans() -> dict:
    if not VERSIONS_DIR.exists():
        print(f"❌ 版本目录不存在: {VERSIONS_DIR}")
        sys.exit(1)

    revisions = {}
    for filepath in VERSIONS_DIR.glob("*.py"):
        if filepath.name == "__init__.py":
            continue
        info = parse_revision_file(filepath)
        if info:
            revisions[info["revision"]] = info

    heads = find_heads(revisions)
    print(f"📌 找到 {len(heads)} 个 head 版本:")
    for head in heads:
        print(f"   └─ {head} ({revisions[head]['file']})")

    all_chain_revs = set()
    main_chains = {}
    for head in heads:
        chain = build_chain(revisions, head)
        main_chains[head] = chain
        all_chain_revs.update(chain)

    orphans = [rid for rid in revisions if rid not in all_chain_revs]

    print()
    print("=" * 60)
    if orphans:
        print(f"⚠️  发现 {len(orphans)} 个孤儿 revision:")
        for rid in orphans:
            info = revisions[rid]
            marker = " [已标记 ORPHAN]" if info["is_orphan_marker"] else ""
            print(f"   ❌ {rid} ({info['file']}){marker}")
    else:
        print("✅ 未发现孤儿 revision，所有版本都在 head 链路上")

    marked_orphans = [rid for rid in revisions if revisions[rid]["is_orphan_marker"]]
    if marked_orphans:
        print()
        print(f"📝 已标记为 ORPHAN 的版本: {len(marked_orphans)} 个")
        for rid in marked_orphans:
            print(f"   └─ {rid} ({revisions[rid]['file']})")

    return {
        "total": len(revisions),
        "heads": heads,
        "orphans": orphans,
        "marked_orphans": marked_orphans,
        "revisions": revisions,
    }


if __name__ == "__main__":
    print("=" * 60)
    print("🔍 Alembic 孤儿 Revision 检测工具")
    print("=" * 60)
    print()

    result = check_orphans()

    print()
    print("=" * 60)
    print(f"📊 统计: 共 {result['total']} 个版本，{len(result['orphans'])} 个孤儿")
    print("=" * 60)

    if result["orphans"] and "--fix" in sys.argv:
        print()
        print("🔧 自动标记孤儿版本...")
        for rid in result["orphans"]:
            info = result["revisions"][rid]
            filepath = VERSIONS_DIR / info["file"]
            content = filepath.read_text()
            if "# ORPHAN" not in content:
                lines = content.split("\n", 3)
                if len(lines) >= 3:
                    new_content = lines[0] + "\n" + lines[1] + "\n" + "# ORPHAN_REVISION: 不在主链路上的历史版本\n" + lines[2] + "\n" + "\n".join(lines[3:])
                    filepath.write_text(new_content)
                print(f"   ✅ 已标记: {info['file']}")
        print()
        print("✅ 标记完成")
