# -*- coding: utf-8 -*-
"""采集状态与实际落盘状况的逐层比对。

层级：
  L0 状态库   data/crawler/products.sqlite（请求结果与快照路径登记）
  L1 快照文件 data/raw/pages/<sha1[:2]>/<ASIN>.html.gz
  L2 解析暂存 data/staging/product_pages/<ASIN>.json
  L3 目标层   data/target/target.db（source_record / product_edition / review）
  L4 PostgreSQL etl_amazon（与 L3 行数比对）

产出：design/采集与落盘比对.md、data/staging/landing_compare.json
用法：python crawler/landing_compare.py
"""
from __future__ import annotations

import glob
import io
import json
import os
import sqlite3
import subprocess
import sys

ROOT = r"E:\\ETL_Amazon"
DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
PAGES = os.path.join(ROOT, "data", "raw", "pages")
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
TARGET = os.path.join(ROOT, "data", "target", "target.db")
PSQL = os.path.join(ROOT, "tools", "pgsql", "bin", "psql.exe")
OUT_MD = os.path.join(ROOT, "design", "采集与落盘比对.md")
OUT_JSON = os.path.join(ROOT, "data", "staging", "landing_compare.json")


def main():
    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    rows = conn.execute(
        "SELECT asin,status,snapshot_path,content_length FROM product_page").fetchall()
    conn.close()
    by_status = {}
    for r in rows:
        by_status[r[1]] = by_status.get(r[1], 0) + 1
    success = {r[0] for r in rows if r[1] == "success"}

    # L1 快照
    files = glob.glob(os.path.join(PAGES, "*", "*.html.gz"))
    on_disk = {os.path.basename(f)[:-8] for f in files}
    db_paths = {r[0]: r[2] for r in rows if r[2]}
    missing_files = [a for a, p in db_paths.items() if not os.path.isfile(p)]
    orphan_files = sorted(on_disk - set(db_paths))
    size_mismatch = 0
    for r in rows:
        path, length = r[2], r[3]
        if path and length and os.path.isfile(path):
            try:
                import gzip
                with gzip.open(path, "rb") as fh:
                    n = len(fh.read())
                if n != length:
                    size_mismatch += 1
            except OSError:
                size_mismatch += 1

    # L2 暂存
    staged = {n[:-5] for n in os.listdir(STAGING) if n.endswith(".json")}
    success_without_json = sorted(success - staged)
    json_without_success = sorted(staged - {r[0] for r in rows})

    # L3 目标层
    t_counts = {}
    if os.path.isfile(TARGET):
        tc = sqlite3.connect("file:%s?mode=ro" % TARGET, uri=True)
        for t in ("source_record", "product_edition", "product_movie", "review",
                  "movie", "person", "field_assertion"):
            try:
                t_counts[t] = tc.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except sqlite3.Error:
                t_counts[t] = 0
        tc.close()

    # L4 PG
    pg = {}
    try:
        env = dict(os.environ)
        env.update({"PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": "etl",
                    "PGDATABASE": "etl_amazon",
                    "PGOPTIONS": "-c statement_timeout=15000"})
        q = ("SELECT 'source_record', COUNT(*) FROM source_record UNION ALL "
             "SELECT 'movie', COUNT(*) FROM movie UNION ALL "
             "SELECT 'review', COUNT(*) FROM review")
        r = subprocess.run([PSQL, "-t", "-A", "-F", "|", "-c", q], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=40)
        for line in r.stdout.decode("utf-8", "replace").strip().splitlines():
            if "|" in line:
                k, v = line.split("|", 1)
                pg[k] = int(v)
    except (OSError, subprocess.TimeoutExpired):
        pg = {}

    lines = ["# 采集状态与实际落盘比对（自动生成）", "",
             "> 生成：crawler/landing_compare.py；只读本地文件与目标库。", "",
             "## 一、L0 状态库", "",
             "| 状态 | 条数 |", "|---|---|"]
    for k, v in sorted(by_status.items(), key=lambda kv: -kv[1]):
        lines.append("| %s | %d |" % (k, v))
    lines.append("| **合计** | **%d** |" % sum(by_status.values()))
    lines += ["", "## 二、L1 快照落盘", "", "| 检查 | 结果 |", "|---|---|"]
    lines.append("| 磁盘快照文件 | %d |" % len(files))
    lines.append("| 状态库登记路径 | %d |" % len(db_paths))
    lines.append("| 登记了但文件缺失 | %d |" % len(missing_files))
    lines.append("| 磁盘有但库中无登记 | %d |" % len(orphan_files))
    lines.append("| 解压后长度与登记不一致 | %d |" % size_mismatch)
    lines += ["", "## 三、L2 解析暂存", "", "| 检查 | 结果 |", "|---|---|"]
    lines.append("| 暂存 JSON | %d |" % len(staged))
    lines.append("| success 页 | %d |" % len(success))
    lines.append("| success 缺口（未解析） | %d |" % len(success_without_json))
    lines.append("| 暂存多余（无对应行） | %d |" % len(json_without_success))
    lines += ["", "## 四、L3 目标层（SQLite）", "", "| 表 | 行数 |", "|---|---|"]
    for k, v in t_counts.items():
        lines.append("| %s | %d |" % (k, v))
    lines += ["", "## 五、L4 PostgreSQL 与 L3 比对", "", "| 表 | SQLite | PostgreSQL | 状态 |", "|---|---|---|---|"]
    for k in ("source_record", "movie", "review"):
        a = t_counts.get(k, 0)
        b = pg.get(k)
        if b is None:
            lines.append("| %s | %d | 不可用 | PG 未连接 |" % (k, a))
        else:
            lines.append("| %s | %d | %d | %s |" % (k, a, b, "一致" if a == b else "不一致"))
    lines += ["", "## 六、结论", ""]
    ok = (not missing_files and not orphan_files and size_mismatch == 0)
    lines.append("1. 快照层：%s；" % ("状态库与磁盘完全对齐" if ok else "存在缺失或孤儿，需检查"))
    lines.append("2. 解析层：success 与暂存差 %d 条（刷新滞后，跑 run_pipeline.py 收敛）；" % len(success_without_json))
    lines.append("3. 目标层：source_record 与状态库差 %d 条（同上）；" % abs(t_counts.get("source_record", 0) - len(rows)))
    lines.append("4. 双库：%s。" % ("SQLite 与 PostgreSQL 行数一致" if pg and all(pg.get(k) == t_counts.get(k) for k in pg) else "需检查 PG 同步"))
    lines.append("")
    with io.open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    summary = {
        "l0_rows": len(rows), "l0_by_status": by_status,
        "l1_files": len(files), "l1_missing": len(missing_files),
        "l1_orphans": len(orphan_files), "l1_size_mismatch": size_mismatch,
        "l2_staged": len(staged), "l2_success": len(success),
        "l2_gap": len(success_without_json), "l2_extra": len(json_without_success),
        "l3_counts": t_counts, "l4_pg": pg,
    }
    os.makedirs(os.path.dirname(os.path.abspath(OUT_JSON)), exist_ok=True)
    with io.open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print("L0 rows=%d  L1 files=%d missing=%d orphan=%d sizemis=%d" % (
        len(rows), len(files), len(missing_files), len(orphan_files), size_mismatch))
    print("L2 staged=%d success=%d gap=%d extra=%d" % (
        len(staged), len(success), len(success_without_json), len(json_without_success)))
    print("L3 %s" % t_counts)
    print("L4 pg %s" % (pg or "unavailable"))
    print("md   = %s" % OUT_MD)
    return 0


if __name__ == "__main__":
    sys.exit(main())
