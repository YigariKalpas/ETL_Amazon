# -*- coding: utf-8 -*-
"""整理当前网页采集情况（只读本地文件，不联网）。

产出：design/网页采集现状.md + data/staging/page_inventory.json
用法：python crawler/page_inventory.py
"""
from __future__ import annotations

import collections
import glob
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = r"E:\ETL_Amazon"
DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
PAGES = os.path.join(ROOT, "data", "raw", "pages")
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
OUT_MD = os.path.join(ROOT, "design", "网页采集现状.md")
OUT_JSON = os.path.join(ROOT, "data", "staging", "page_inventory.json")


def main():
    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    rows = conn.execute(
        "SELECT asin,status,http_status,fetched_at,content_sha256,content_length,snapshot_path "
        "FROM product_page").fetchall()
    conn.close()

    by_status = collections.Counter(r[1] for r in rows)
    by_http = collections.Counter(r[2] for r in rows if r[2])
    by_day = collections.Counter((r[3] or "")[:10] for r in rows if r[3])
    # 【KEY-86】只对 success 页做哈希重复分析：404 页共用同一个模板，会把统计带偏
    hashes = collections.Counter(r[4] for r in rows if r[4] and r[1] == "success")
    dup_hashes = {h: n for h, n in hashes.items() if n > 1}

    files = glob.glob(os.path.join(PAGES, "*", "*.html.gz"))
    size = 0
    buckets = collections.Counter()
    for f in files:
        try:
            size += os.path.getsize(f)
        except OSError:
            pass
        buckets[os.path.basename(os.path.dirname(f))] += 1

    staged = sum(1 for n in os.listdir(STAGING) if n.endswith(".json"))
    success = by_status.get("success", 0)
    total = sum(by_status.values())

    lines = ["# 网页采集现状（自动生成）", "",
             "> 生成：crawler/page_inventory.py；只读本地状态库与快照目录，不联网。", "",
             "## 一、状态分布", "", "| 状态 | 条数 | 占比 |", "|---|---|---|"]
    for k, v in by_status.most_common():
        lines.append("| %s | %d | %.1f%% |" % (k, v, 100.0 * v / max(1, total)))
    lines.append("| **合计** | **%d** | 100%% |" % total)
    lines += ["", "## 二、HTTP 状态", "", "| HTTP | 条数 |", "|---|---|"]
    for k, v in by_http.most_common():
        lines.append("| %s | %d |" % (k, v))
    lines += ["", "## 三、快照文件", "", "| 指标 | 值 |", "|---|---|"]
    lines.append("| 文件数 | %d |" % len(files))
    lines.append("| 分桶数 | %d |" % len(buckets))
    lines.append("| 总体积 | %.1f MB |" % (size / 1024.0 / 1024.0))
    lines.append("| 平均每文件 | %.1f KB |" % (size / max(1, len(files)) / 1024.0))
    lines += ["", "## 四、解析与去重", "", "| 指标 | 值 |", "|---|---|"]
    lines.append("| 暂存 JSON | %d |" % staged)
    lines.append("| success 页 | %d |" % success)
    lines.append("| 已解析比例 | %.1f%% |" % (100.0 * staged / max(1, success)))
    lines.append("| 内容哈希重复组 | %d（涉及 %d 条） |" % (len(dup_hashes), sum(dup_hashes.values())))
    lines += ["", "## 五、按天抓取量（最近 10 天）", "", "| 日期 | 条数 |", "|---|---|"]
    for d, v in sorted(by_day.items())[-10:]:
        lines.append("| %s | %d |" % (d, v))
    lines += ["", "## 六、说明", "",
              "1. validation_page 属客观限流证据，每波开始前清理并回填待采集；",
              "2. success 与暂存 JSON 的差额是刷新滞后，跑一次 run_pipeline.py 即可对齐；",
              "3. 哈希重复只统计 success 页（404 页共用模板，不参与），重复说明同一页面挂在多个 ASIN 下；", ""]
    with io.open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": total,
        "by_status": dict(by_status),
        "by_http": dict(by_http),
        "by_day": dict(sorted(by_day.items())),
        "files": len(files),
        "buckets": len(buckets),
        "total_mb": round(size / 1024.0 / 1024.0, 1),
        "staged_json": staged,
        "duplicate_hash_groups": len(dup_hashes),
    }
    os.makedirs(os.path.dirname(os.path.abspath(OUT_JSON)), exist_ok=True)
    with io.open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print("rows=%d files=%d total_mb=%.1f staged=%d dup_groups=%d" % (
        total, len(files), size / 1024.0 / 1024.0, staged, len(dup_hashes)))
    print("md   = %s" % OUT_MD)
    print("json = %s" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
