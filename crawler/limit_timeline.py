# -*- coding: utf-8 -*-
"""【KEY-25】把状态库按时间排开，看限制什么时候来、持续多久。

用途：设计/Stage0 限流观察 的证据生成器。只读状态库，不发任何请求。

输出：
  1) 按分钟聚合的请求/结果计数；
  2) 连续的「验证页段」与「成功段」（分界处即限制开始/解除时刻）；
  3) 每段的时长与成功率。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta

DEFAULT_DB = r"E:\ETL_Amazon\data\crawler\products.sqlite"


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按时间切片观察限流")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--since", default="", help="起始时间，如 2026-10-23T01:00:00")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    sql = "SELECT asin, status, fetched_at FROM product_page"
    params: tuple = ()
    if args.since:
        sql += " WHERE fetched_at >= ?"
        params = (args.since,)
    rows = conn.execute(sql + " ORDER BY fetched_at", params).fetchall()
    if not rows:
        print("没有记录。")
        return 0

    print("records       = %d" % len(rows))
    print("from          = %s" % rows[0][2])
    print("to            = %s" % rows[-1][2])

    # 1) 按分钟聚合
    buckets: dict[str, dict[str, int]] = {}
    for _asin, status, ts in rows:
        key = ts[:16]  # YYYY-MM-DDTHH:MM
        b = buckets.setdefault(key, {})
        b[status] = b.get(status, 0) + 1
    print("\n[按分钟] 分钟 | success | validation | not_found | parse_failed")
    for key in sorted(buckets):
        b = buckets[key]
        print("  %s | %3d | %3d | %3d | %3d" % (
            key, b.get("success", 0), b.get("validation_page", 0),
            b.get("not_found", 0), b.get("parse_failed", 0)))

    # 2) 连续段：以「是否验证页」为分界
    segments: list[tuple[str, datetime, datetime, int]] = []
    cur_kind = "validation" if rows[0][1] == "validation_page" else "ok"
    start = parse_ts(rows[0][2])
    last = start
    count = 1
    for _asin, status, ts in rows[1:]:
        t = parse_ts(ts)
        kind = "validation" if status == "validation_page" else "ok"
        if kind != cur_kind:
            segments.append((cur_kind, start, last, count))
            cur_kind, start, count = kind, t, 0
        last = t
        count += 1
    segments.append((cur_kind, start, last, count))

    print("\n[连续段] kind | 起 | 止 | 分钟 | 次数 | 备注")
    for kind, s, e, n in segments:
        mins = (e - s).total_seconds() / 60.0
        print("  %-10s | %s | %s | %6.1f | %4d" % (kind, s.strftime("%H:%M:%S"),
                                                    e.strftime("%H:%M:%S"), mins, n))

    v = [r for r in rows if r[1] == "validation_page"]
    if v:
        print("\n限制窗口：第一次验证页 %s，最后一次 %s，共 %d 次" % (
            v[0][2], v[-1][2], len(v)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
