# -*- coding: utf-8 -*-
"""导出可视化数据包：JSON + CSV，供控制台/幻灯片/报告引用。

内容：进度、状态分布、按天产出、目标层规模、效率、覆盖率。
用法：python crawler/export_viz.py
"""
from __future__ import annotations

import collections
import csv
import glob
import io
import json
import os
import sqlite3
import sys

ROOT = r"E:\\ETL_Amazon"
CRAWL = os.path.join(ROOT, "data", "crawler", "products.sqlite")
TARGET = os.path.join(ROOT, "data", "target", "target.db")
EFF = os.path.join(ROOT, "data", "staging", "efficiency.json")
COV = os.path.join(ROOT, "data", "staging", "coverage", "summary.json")
OUT_DIR = os.path.join(ROOT, "data", "export")
TOTAL = 253059


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect("file:%s?mode=ro" % CRAWL, uri=True)
    by_status = dict(conn.execute(
        "SELECT status, COUNT(*) FROM product_page GROUP BY status"))
    by_day = conn.execute(
        "SELECT substr(fetched_at,1,10) d, SUM(status='success'), COUNT(*) "
        "FROM product_page WHERE fetched_at IS NOT NULL GROUP BY d ORDER BY d").fetchall()
    conn.close()

    t = {}
    if os.path.isfile(TARGET):
        tc = sqlite3.connect("file:%s?mode=ro" % TARGET, uri=True)
        for tab in ("movie", "product_edition", "review", "review_mapping", "person",
                    "movie_person", "field_assertion", "field_selection",
                    "merge_decision", "release_date", "quality_issue", "source_record"):
            try:
                t[tab] = tc.execute("SELECT COUNT(*) FROM %s" % tab).fetchone()[0]
            except sqlite3.Error:
                t[tab] = 0
        tc.close()

    eff = {}
    if os.path.isfile(EFF):
        with io.open(EFF, encoding="utf-8") as fh:
            eff = json.load(fh)
    cov = {}
    if os.path.isfile(COV):
        with io.open(COV, encoding="utf-8") as fh:
            cov = json.load(fh)

    target_goal = 10000
    marker = os.path.join(ROOT, "data", "crawler", "runtime", "forever.json")
    if os.path.isfile(marker):
        try:
            with io.open(marker, encoding="utf-8") as fh:
                target_goal = int(json.load(fh).get("target") or 10000)
        except (OSError, ValueError):
            pass

    success = by_status.get("success", 0)
    rows = sum(by_status.values())
    bundle = {
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "target_goal": target_goal,
        "success": success,
        "rows": rows,
        "remaining_to_goal": max(0, target_goal - success),
        "progress_pct": round(100.0 * success / max(1, target_goal), 1),
        "total_products": TOTAL,
        "sample_pct": round(100.0 * success / TOTAL, 3),
        "by_status": by_status,
        "by_day": [{"date": d, "success": s or 0, "requests": n} for d, s, n in by_day],
        "target_layer": t,
        "efficiency": eff,
        "coverage": cov,
    }
    with io.open(os.path.join(OUT_DIR, "viz_bundle.json"), "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False, indent=2)

    with io.open(os.path.join(OUT_DIR, "viz_series.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "requests", "success", "success_rate"])
        for d, s, n in by_day:
            w.writerow([d, n, s or 0, round(100.0 * (s or 0) / max(1, n), 1)])

    print("success=%d/%d (%.1f%%)  rows=%d  status=%s"
          % (success, target_goal, 100.0 * success / max(1, target_goal), rows, by_status))
    print("days=%d  target_layer_keys=%d" % (len(by_day), len(t)))
    print("json = %s" % os.path.join(OUT_DIR, "viz_bundle.json"))
    print("csv  = %s" % os.path.join(OUT_DIR, "viz_series.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
