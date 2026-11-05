# -*- coding: utf-8 -*-
"""生成交付状态快照（docs/交付状态快照.md）：把当前所有关键数字冻结成一份文档。

用法：python crawler/handover_snapshot.py
"""
from __future__ import annotations

import glob
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

ROOT = r"E:\\ETL_Amazon"
CRAWL = os.path.join(ROOT, "data", "crawler", "products.sqlite")
TARGET = os.path.join(ROOT, "data", "target", "target.db")
EFF = os.path.join(ROOT, "data", "staging", "efficiency.json")
COV = os.path.join(ROOT, "data", "staging", "coverage", "summary.json")
OUT = os.path.join(ROOT, "docs", "交付状态快照.md")
TOTAL = 253059


def main():
    conn = sqlite3.connect("file:%s?mode=ro" % CRAWL, uri=True)
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM product_page GROUP BY status"))
    last = conn.execute("SELECT MAX(fetched_at) FROM product_page").fetchone()[0]
    conn.close()
    rows = sum(by_status.values())
    ok = by_status.get("success", 0)

    t = {}
    if os.path.isfile(TARGET):
        tc = sqlite3.connect("file:%s?mode=ro" % TARGET, uri=True)
        for tab in ("movie", "product_edition", "review", "review_mapping", "person",
                    "movie_person", "field_assertion", "field_selection",
                    "merge_decision", "release_date", "quality_issue"):
            try:
                t[tab] = tc.execute("SELECT COUNT(*) FROM %s" % tab).fetchone()[0]
            except sqlite3.Error:
                t[tab] = 0
        tc.close()
    eff = json.load(io.open(EFF, encoding="utf-8")) if os.path.isfile(EFF) else {}
    cov = json.load(io.open(COV, encoding="utf-8")) if os.path.isfile(COV) else {}
    files = glob.glob(os.path.join(ROOT, "data", "raw", "pages", "*", "*.html.gz"))
    size_mb = sum(os.path.getsize(f) for f in files) / 1024.0 / 1024.0 if files else 0

    L = ["# 交付状态快照", "",
         "> 生成：crawler/handover_snapshot.py ｜ %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "> 本文件是提交时的数字基线，报告正文引用这里的数字即可。", "",
         "## 一、采集", "",
         "| 项 | 值 |", "|---|---|"]
    L.append("| 状态行 | %d |" % rows)
    for k, v in sorted(by_status.items(), key=lambda kv: -kv[1]):
        L.append("| %s | %d（%.1f%%） |" % (k, v, 100.0 * v / max(1, rows)))
    L.append("| 快照文件 | %d 个 / %.1f MB |" % (len(files), size_mb))
    L.append("| 最近抓取 | %s |" % last)
    L.append("| 占全部产品 | %.2f%%（%d / %d） |" % (100.0 * ok / TOTAL, ok, TOTAL))

    L += ["", "## 二、效率", "", "| 指标 | 值 |", "|---|---|"]
    if eff:
        L.append("| 轮次 | %d |" % eff.get("runs", 0))
        L.append("| 请求 / 成功 / 验证页 | %d / %d / %d |" % (
            eff.get("requests", 0), eff.get("success", 0), eff.get("validation", 0)))
        L.append("| 转化率 / 验证页占比 | %.1f%% / %.1f%% |" % (
            eff.get("success_rate", 0), eff.get("validation_rate", 0)))
        L.append("| 每成功 1 页所需请求 | %s |" % eff.get("requests_per_success"))
        L.append("| 近 12 小时平均 | %s 成功/小时 |" % eff.get("avg_success_per_hour"))
        L.append("| 剩余未采 | %s |" % eff.get("remaining"))

    L += ["", "## 三、目标层", "", "| 表 | 行数 |", "|---|---|"]
    for k, v in t.items():
        L.append("| %s | %d |" % (k, v))

    L += ["", "## 四、覆盖率", "", "| 指标 | 值 |", "|---|---|"]
    if cov:
        L.append("| 成功页面 | %s |" % cov.get("success_pages"))
        L.append("| 已识别作品 | %s |" % cov.get("identified_works"))
        L.append("| 识别率（全部产品） | %s%% |" % cov.get("identification_rate"))
        L.append("| 采样率 | %s%% |" % cov.get("sample_rate"))
        fm = cov.get("field_coverage_movie", {})
        fp = cov.get("field_coverage_page", {})
        if fm and fp:
            L += ["", "| 字段 | 作品级 | 页面级 |", "|---|---|---|"]
            for k in fm:
                L.append("| %s | %s | %s |" % (k, fm[k], fp.get(k, "-")))

    L += ["", "## 五、复现入口", "",
          "    .venv\\Scripts\\python.exe crawler\\run_pipeline.py      # 全量管线",
          "    crawler\\export_viz.py                                 # 可视化数据包",
          "    crawler\\build_dashboard.py --refresh 30               # 控制台",
          "    scripts\\pdi.cmd kitchen /file:E:\\ETL_Amazon\\etl\\jobs\\j_etl_pipeline.kjb  # ETL 作业",
          ""]
    with io.open(OUT, "w", encoding="utf-8") as fh:
        fh.write(chr(10).join(L))
    print("rows=%d ok=%d movie=%d review=%d assertion=%d" % (
        rows, ok, t.get("movie", 0), t.get("review", 0), t.get("field_assertion", 0)))
    print("md = %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
