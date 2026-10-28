# -*- coding: utf-8 -*-
"""【KEY-32】只读查询状态库，避免在 PowerShell 里和引号/转义打架。

用法：
  python crawler/db_query.py "SELECT status, COUNT(*) FROM product_page GROUP BY status"
  python crawler/db_query.py "PRAGMA table_info(product_page)"

安全：只允许单条 SELECT / PRAGMA / EXPLAIN，其他语句直接拒绝。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

DEFAULT_DB = r"E:\ETL_Amazon\data\crawler\products.sqlite"
ALLOWED = ("select", "pragma", "explain", "with")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="只读查询 SQLite")
    ap.add_argument("sql")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=200, help="最多打印多少行")
    args = ap.parse_args(argv)

    sql = args.sql.strip()
    if not sql.lower().startswith(ALLOWED):
        print("只允许 SELECT / PRAGMA / EXPLAIN / WITH 语句。")
        return 2

    conn = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        print(" | ".join(cols))
        print("-" * 60)
        for r in rows[:args.limit]:
            print(" | ".join("" if v is None else str(v) for v in tuple(r)))
        print("(%d rows)" % len(rows))
    except sqlite3.Error as exc:
        print("SQL 错误：%s" % exc)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
