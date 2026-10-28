# -*- coding: utf-8 -*-
"""【KEY-55】脚本盘点：把"我们现在有哪些脚本、各自产出什么、产物在不在"变成可复算的表。

不联网、只读文件系统与仓库内产物。

产出：
  data/staging/script_inventory.csv    逐脚本明细
  design/脚本进度总览.md               Markdown 表格（自动生成）

用法：
  python crawler/script_inventory.py
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import os
import sys
from datetime import datetime

ROOT = r"E:\ETL_Amazon"
SCAN_DIRS = [os.path.join(ROOT, "crawler")]
OUT_CSV = os.path.join(ROOT, "data", "staging", "script_inventory.csv")
OUT_MD = os.path.join(ROOT, "design", "脚本进度总览.md")

# 脚本 -> (任务, 主要产物)；产物为 None 表示只打印不落盘
ARTIFACTS = {
    "extract_asins.py": ("任务2 前置", "data/crawler/product_list.csv"),
    "build_product_urls.py": ("任务2 前置", "data/crawler/product_urls.txt"),
    "crawl_product_pages.py": ("任务2", "data/raw/pages"),
    "wave_loop.py": ("任务2", "logs/wave_loop.log"),
    "crawl_supervisor.py": ("任务2", "logs/crawl_forever.log"),
    "pending_count.py": ("任务2", None),
    "crawl_stats.py": ("任务2", None),
    "purge_invalid_snapshots.py": ("任务2", None),
    "migrate_pages_to_buckets.py": ("任务2", None),
    "reclassify_snapshots.py": ("任务2", None),
    "limit_timeline.py": ("任务2 分析", None),
    "verify_snapshots.py": ("任务2 校验", "data/crawler/collect_manifest.json"),
    "parse_product_page.py": ("任务2/3", "data/staging/product_pages"),
    "classify_products.py": ("任务3", "data/staging/classification"),
    "probe_review_format.py": ("任务1 探针", None),
    "extract_reviews.py": ("任务1", "data/staging/reviews"),
    "verify_reviews.py": ("任务1 校验", "data/staging/reviews/manifest.json"),
    "review_count_stats.py": ("任务1 分析", None),
    "collection_audit.py": ("任务2/3 分析", "data/staging/audit_sample.csv"),
    "analyze_merge_candidates.py": ("任务4 分析", "data/staging/merge_candidates.csv"),
    "analyze_title_similarity.py": ("任务4 分析", None),
    "analyze_series_roots.py": ("任务4 分析", None),
    "movie_entities.py": ("任务4", "data/staging/entities/summary.json"),
    "show_products.py": ("任务4 人工", None),
    "build_target_db.py": ("目标层", "data/target/target.db"),
    "verify_target_db.py": ("目标层 校验", None),
    "load_target_pg.py": ("目标层", "data/target/pg_csv/load.sql"),
    "db_query.py": ("通用", None),
    "script_inventory.py": ("元", "data/staging/script_inventory.csv"),
}


def first_doc_line(path: str) -> str:
    try:
        with io.open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (SyntaxError, OSError, UnicodeDecodeError):
        return "(无法解析)"
    doc = ast.get_docstring(tree) or ""
    for line in doc.splitlines():
        s = line.strip()
        if s:
            return s
    return "(无说明)"


def cli_options(path: str) -> list:
    try:
        with io.open(path, encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return []
    opts = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith('ap.add_argument("--'):
            opts.append(s.split('"')[1])
    return opts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-csv", default=OUT_CSV)
    ap.add_argument("--out-md", default=OUT_MD)
    args = ap.parse_args(argv)

    rows = []
    for d in SCAN_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(d, name)
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                nlines = sum(1 for _ in fh)
            task, artifact = ARTIFACTS.get(name, ("未归入", None))
            has_artifact = "-"
            if artifact:
                target = os.path.normcase(os.path.join(ROOT, artifact))
                if target == os.path.normcase(args.out_csv):
                    has_artifact = "有"      # 本脚本的产物就是本次输出
                else:
                    has_artifact = "有" if os.path.exists(os.path.join(ROOT, artifact)) else "缺"
            rows.append({
                "script": name,
                "task": task,
                "lines": nlines,
                "mtime": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M"),
                "artifact": artifact or "-",
                "artifact_state": has_artifact,
                "cli": " ".join(cli_options(path)) or "-",
                "summary": first_doc_line(path),
            })

    os.makedirs(os.path.dirname(os.path.abspath(args.out_csv)), exist_ok=True)
    with io.open(args.out_csv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    total_lines = sum(r["lines"] for r in rows)
    missing = [r["script"] for r in rows if r["artifact_state"] == "缺"]
    tasks = {}
    for r in rows:
        tasks.setdefault(r["task"], []).append(r["script"])

    md = []
    md.append("# 脚本进度总览（自动生成）")
    md.append("")
    md.append("> 由 crawler/script_inventory.py 生成，请勿手工编辑；数据源：crawler/*.py 与仓库内产物。")
    md.append("")
    md.append("| 指标 | 值 |")
    md.append("|---|---|")
    md.append("| 脚本数 | %d |" % len(rows))
    md.append("| 代码行数合计 | %d |" % total_lines)
    md.append("| 产物缺失的脚本 | %d |" % len(missing))
    md.append("")
    md.append("## 按任务分组")
    md.append("")
    md.append("| 任务 | 脚本数 | 脚本 |")
    md.append("|---|---|---|")
    for t in sorted(tasks):
        md.append("| %s | %d | %s |" % (t, len(tasks[t]), ", ".join(tasks[t])))
    md.append("")
    md.append("## 逐脚本明细")
    md.append("")
    md.append("| 脚本 | 任务 | 行数 | 产物 | 产物状态 | 最后修改 | 说明 |")
    md.append("|---|---|---|---|---|---|---|")
    for r in rows:
        md.append("| %s | %s | %d | %s | %s | %s | %s |" % (
            r["script"], r["task"], r["lines"], r["artifact"], r["artifact_state"],
            r["mtime"], r["summary"][:50]))
    md.append("")
    md.append("## CLI 参数一览")
    md.append("")
    for r in rows:
        if r["cli"] != "-":
            md.append("- **%s**：%s" % (r["script"], r["cli"]))
    md.append("")
    with io.open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))

    print("按任务分组：")
    for t in sorted(tasks, key=lambda x: -len(tasks[x])):
        print("  %-14s %2d 个：%s" % (t, len(tasks[t]), ", ".join(tasks[t])))
    print()
    print("行数最多的 5 个脚本：")
    for r0 in sorted(rows, key=lambda x: -x["lines"])[:5]:
        print("  %-32s %5d 行  %s" % (r0["script"], r0["lines"], r0["task"]))
    print()
    print("scripts        = %d" % len(rows))
    print("total_lines    = %d" % total_lines)
    print("artifact_missing = %d %s" % (len(missing), missing if missing else ""))
    print("csv            = %s" % args.out_csv)
    print("md             = %s" % args.out_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
