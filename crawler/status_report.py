# -*- coding: utf-8 -*-
"""【KEY-67】采集状态 + 落库情况统一报表（只读，不联网）。

回答四个问题：
  1) 现在采到什么程度（状态分布、快照体积、最近一小时吞吐）；
  2) 有没有卡住（最近一条 fetched_at、未完成状态数）；
  3) 落库落到哪一层（SQLite 目标层 + PostgreSQL，逐表行数）；
  4) 采集与落库的差（成功页数 vs 实体化版本数 vs 评论数），即"还有多少没进目标层"。

输出：data/staging/status_report.json + 控制台摘要。

用法：
  python crawler/status_report.py
  python crawler/status_report.py --no-pg
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = r"E:\ETL_Amazon"
CRAWL_DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
TARGET_DB = os.path.join(ROOT, "data", "target", "target.db")
PAGES = os.path.join(ROOT, "data", "raw", "pages")
PSQL = os.path.join(ROOT, "tools", "pgsql", "bin", "psql.exe")
OUT = os.path.join(ROOT, "data", "staging", "status_report.json")

PG_TABLES = ["source_record", "movie", "product_edition", "product_movie", "merge_decision",
             "person", "movie_person", "review", "review_mapping",
             "field_assertion", "field_selection", "release_date", "quality_issue"]


def crawl_state() -> dict:
    conn = sqlite3.connect("file:%s?mode=ro" % CRAWL_DB, uri=True)
    rows = conn.execute("SELECT status, COUNT(*) FROM product_page GROUP BY status").fetchall()
    total = sum(n for _s, n in rows)
    recent = conn.execute(
        "SELECT COUNT(*) FROM product_page WHERE fetched_at >= ?",
        ((datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),)).fetchone()[0]
    last = conn.execute("SELECT MAX(fetched_at) FROM product_page").fetchone()[0]
    conn.close()
    files = glob.glob(os.path.join(PAGES, "*", "*.html.gz"))
    size = 0
    for f in files:
        try:
            size += os.path.getsize(f)
        except OSError:
            pass
    return {"status": dict(rows), "rows": total, "last_hour": recent,
            "last_fetched_at": last, "snapshot_files": len(files),
            "snapshot_mb": round(size / 1024.0 / 1024.0, 1)}


def target_state() -> dict:
    if not os.path.isfile(TARGET_DB):
        return {"exists": False}
    conn = sqlite3.connect("file:%s?mode=ro" % TARGET_DB, uri=True)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    counts = {}
    for t in tables:
        counts[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    run = conn.execute("SELECT run_id, started_at, finished_at, status FROM etl_run "
                       "ORDER BY started_at DESC LIMIT 1").fetchone()
    conn.close()
    return {"exists": True, "tables": counts, "last_run": run}


def pg_state() -> dict:
    env = dict(os.environ)
    env.update({"PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": "etl",
                "PGDATABASE": "etl_amazon", "PGCLIENTENCODING": "UTF8",
                # 【KEY-73】避免客户端超时后后端查询继续挂着，把后续查询堵住
                "PGOPTIONS": "-c statement_timeout=20000 -c max_parallel_workers_per_gather=0"})
    q = " UNION ALL ".join(["SELECT '%s', COUNT(*) FROM %s" % (t, t) for t in PG_TABLES])
    try:
        r = subprocess.run([PSQL, "-t", "-A", "-F", "|", "-c", q], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"up": False, "error": str(exc)}
    if r.returncode != 0:
        return {"up": False, "error": r.stdout.decode("utf-8", "replace")[:200]}
    out = {}
    for line in r.stdout.decode("utf-8", "replace").strip().splitlines():
        if "|" in line:
            k, v = line.split("|", 1)
            out[k] = int(v)
    return {"up": True, "tables": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pg", action="store_true")
    args = ap.parse_args(argv)

    crawl = crawl_state()
    target = target_state()
    pg = {"up": False, "skipped": True} if args.no_pg else pg_state()

    success = crawl["status"].get("success", 0)
    editions = target.get("tables", {}).get("product_edition", 0)
    reviews = target.get("tables", {}).get("review", 0)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "crawl": crawl,
        "target_sqlite": target,
        "target_pg": pg,
        "gap": {
            "success_not_yet_parsed_or_entity": max(0, success - editions),
            "review_rows_in_target": reviews,
            "status_sum_check": success + crawl["status"].get("not_found", 0)
                                 + crawl["status"].get("parse_failed", 0)
                                 + crawl["status"].get("validation_page", 0),
        },
    }
    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print("== 采集 ==")
    print("  rows            = %d" % crawl["rows"])
    for k, v in sorted(crawl["status"].items(), key=lambda kv: -kv[1]):
        print("  %-15s = %d" % (k, v))
    print("  last_hour       = %d 条" % crawl["last_hour"])
    print("  last_fetched_at = %s" % crawl["last_fetched_at"])
    print("  snapshots       = %d 个 / %.1f MB" % (crawl["snapshot_files"], crawl["snapshot_mb"]))

    print("\n== 目标层 SQLite ==")
    if target.get("exists"):
        for t, n in target["tables"].items():
            print("  %-26s %8d" % (t, n))
        print("  last_run        = %s" % (target["last_run"],))
    else:
        print("  未建库")

    print("\n== 目标层 PostgreSQL ==")
    if pg.get("skipped"):
        print("  跳过")
    elif pg.get("up"):
        for t, n in pg["tables"].items():
            local = target.get("tables", {}).get(t, 0)
            flag = "" if n == local else "  <-- 与 SQLite 不一致"
            print("  %-26s %8d%s" % (t, n, flag))
    else:
        print("  不可用: %s" % pg.get("error"))

    print("\n== 差距 ==")
    print("  success - 已实体化版本 = %d（待重跑 parse→classify→entities）" % report["gap"]["success_not_yet_parsed_or_entity"])
    print("  状态合计校验          = %d" % report["gap"]["status_sum_check"])
    print("report = %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
