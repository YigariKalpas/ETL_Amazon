# -*- coding: utf-8 -*-
"""【KEY-22】清理无效快照：删除验证页(validation_page)等无效件的快照与状态行。

为什么要删：
  验证页(validateCaptcha)不是产品页，既不能用于字段解析与覆盖率统计，也占磁盘。
  保留它们没有证据价值——限流统计已经在 design/Stage0_采集结果与限流观察.md 里记录了。
  删除状态行等于把这些 ASIN 重新放回 pending，待限流缓解后再采。

不删什么：
  not_found(http_404) 是真实结论，parse_failed(模板不符) 是真实页面，都保留。

用法：
  python crawler/purge_invalid_snapshots.py --dry-run     # 只统计
  python crawler/purge_invalid_snapshots.py --apply       # 真删
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

DEFAULT_DB = r"E:\ETL_Amazon\data\crawler\products.sqlite"

# 默认只清理这两种：确实不是产品页
DEFAULT_STATUSES = ("validation_page",)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="删除无效快照并把对应 ASIN 放回 pending")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--statuses", default=",".join(DEFAULT_STATUSES),
                    help="要清理的状态，逗号分隔")
    ap.add_argument("--apply", action="store_true", help="真删（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不修改（默认行为，显式可用）")
    args = ap.parse_args(argv)
    if args.dry_run:
        args.apply = False

    statuses = tuple(s.strip() for s in args.statuses.split(",") if s.strip())
    if not statuses:
        print("没有指定状态。")
        return 2

    conn = sqlite3.connect(args.db)
    q = "SELECT asin, status, snapshot_path FROM product_page WHERE status IN (%s)" % (
        ",".join("?" * len(statuses)),)
    rows = conn.execute(q, statuses).fetchall()
    print("db            = %s" % args.db)
    print("statuses      = %s" % (statuses,))
    print("matched_rows  = %d" % len(rows))

    freed = 0
    missing = 0
    for asin, status, path in rows:
        if not path:
            missing += 1
            continue
        try:
            if os.path.isfile(path):
                freed += os.path.getsize(path)
            else:
                missing += 1
        except OSError:
            missing += 1

    print("snapshot_bytes= %d (%.2f MB)" % (freed, freed / 1024.0 / 1024.0))
    print("missing_files = %d" % missing)

    if not args.apply:
        print("dry-run：未做任何修改。加 --apply 执行。")
        return 0

    deleted_files = 0
    for asin, status, path in rows:
        if path and os.path.isfile(path):
            try:
                os.remove(path)
                deleted_files += 1
            except OSError as exc:
                print("  删除失败 %s: %s" % (path, exc))
    cur = conn.execute(
        "DELETE FROM product_page WHERE status IN (%s)" % (",".join("?" * len(statuses)),),
        statuses)
    conn.commit()
    print("deleted_files = %d" % deleted_files)
    print("deleted_rows  = %d" % cur.rowcount)
    print("remaining     = %d" % conn.execute("SELECT COUNT(*) FROM product_page").fetchone()[0])
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
