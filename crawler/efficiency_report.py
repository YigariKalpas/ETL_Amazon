# -*- coding: utf-8 -*-
"""【KEY-70】爬虫效率分析：每一波/每一次运行投入了多少请求、换回多少成功。

只读本地文件（状态库 + logs/），不联网。

指标口径：
  请求数         = 日志中 "-> " 的行数（每个目标一次尝试）
  成功率         = success / 请求数
  验证页占比     = validation_page / 请求数
  成功/验证页    = 每拿到 1 个成功页要付多少次验证页
  请求/成功      = 总尝试 / 总成功（越低越好）
  实际吞吐       = success / 实际运行秒数
  wall 效率      = 运行秒数 / 日历秒数（被冷却与暂停吃掉多少）

用法：
  python crawler/efficiency_report.py
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

ROOT = r"E:\ETL_Amazon"
LOGS = os.path.join(ROOT, "logs")
DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
TOTAL_TARGETS = 253059
STATUS_RE = re.compile(r"->\s+(\w+)")
ELAPSED_RE = re.compile(r"elapsed\s*=\s*([0-9.]+)")


def parse_run_log(path: str) -> dict:
    done = 0
    success = 0
    validation = 0
    backoff = 0
    elapsed = None
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "-> " in line:
                done += 1
                m = STATUS_RE.search(line)
                if m:
                    if m.group(1) == "success":
                        success += 1
                    elif m.group(1) == "validation_page":
                        validation += 1
            if "BACKOFF" in line:
                backoff += 1
            m = ELAPSED_RE.search(line)
            if m:
                elapsed = float(m.group(1))
    return {"file": os.path.basename(path), "done": done, "success": success,
            "validation": validation, "backoff": backoff, "elapsed": elapsed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    args = ap.parse_args(argv)

    # ---------- 按运行日志统计 ----------
    runs = []
    for p in sorted(glob.glob(os.path.join(LOGS, "wave_0*.log"))):
        r = parse_run_log(p)
        if r["done"]:
            runs.append(("wave", r))
    for p in sorted(glob.glob(os.path.join(LOGS, "worker_*.log"))):
        r = parse_run_log(p)
        if r["done"]:
            runs.append(("worker", r))

    print("=" * 78)
    print("每次运行：请求 / 成功 / 验证页 / 退避 / 耗时")
    print("=" * 78)
    print("%-10s %-16s %6s %5s %5s %6s %5s %9s" % ("类型", "文件", "请求", "成功", "验证", "退避", "成功率", "耗时(s)"))
    tot_done = tot_ok = tot_val = 0
    for kind, r in runs:
        rate = 100.0 * r["success"] / r["done"] if r["done"] else 0
        print("%-10s %-16s %6d %5d %5d %6d %4.0f%% %9s" % (
            kind, r["file"][:16], r["done"], r["success"], r["validation"],
            r["backoff"], rate, r["elapsed"] or "-"))
        tot_done += r["done"]
        tot_ok += r["success"]
        tot_val += r["validation"]

    if tot_done:
        print("-" * 78)
        print("合计       请求 %d / 成功 %d / 验证页 %d" % (tot_done, tot_ok, tot_val))
        print("整体成功率        = %.1f%%" % (100.0 * tot_ok / tot_done))
        print("验证页占比        = %.1f%%" % (100.0 * tot_val / tot_done))
        if tot_ok:
            print("每成功 1 页的验证页 = %.2f" % (float(tot_val) / tot_ok))
            print("请求/成功         = %.2f" % (float(tot_done) / tot_ok))

    # ---------- 状态库总量 ----------
    conn = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM product_page GROUP BY status"))
    rows = sum(counts.values())
    conn.close()
    print()
    print("=" * 78)
    print("累计状态")
    print("=" * 78)
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("  %-16s %6d  (%.1f%%)" % (k, v, 100.0 * v / max(1, rows)))
    print("  合计             %6d" % rows)
    ok = counts.get("success", 0)
    print("  终态成功率        = %.1f%%（success / 全部终态）" % (
        100.0 * ok / max(1, rows - counts.get("validation_page", 0))))

    # ---------- 按小时吞吐 ----------
    conn = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    hourly = conn.execute(
        "SELECT substr(fetched_at,1,13) h, SUM(status='success'), COUNT(*) "
        "FROM product_page WHERE fetched_at IS NOT NULL GROUP BY h ORDER BY h DESC LIMIT 12").fetchall()
    conn.close()
    print()
    print("=" * 78)
    print("最近 12 个活跃小时（UTC）")
    print("=" * 78)
    print("  %-16s %6s %6s %6s" % ("小时", "请求", "成功", "成功率"))
    for h, s, n in hourly:
        print("  %-16s %6d %6d %5.0f%%" % (h, n, s or 0, 100.0 * (s or 0) / max(1, n)))

    # ---------- 剩余量与 ETA ----------
    left = TOTAL_TARGETS - rows
    print()
    print("=" * 78)
    print("剩余与 ETA")
    print("=" * 78)
    print("  未采集目标        = %d" % left)
    if hourly:
        avg_per_hour = sum(s or 0 for _h, s, _n in hourly) / float(len(hourly))
        print("  近 12 小时平均    = %.1f 成功/小时" % avg_per_hour)
        if avg_per_hour > 0:
            print("  按此速度需        = %.0f 小时 = %.1f 天" % (
                left / avg_per_hour, left / avg_per_hour / 24.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
