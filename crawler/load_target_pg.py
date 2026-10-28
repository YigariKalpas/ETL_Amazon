# -*- coding: utf-8 -*-
r"""【KEY-54】把本地 SQLite 目标层装载进 PostgreSQL（已确认部分）。

流程：
  1) 从 data/target/target.db 导出每张表为 CSV（UTF-8）；
  2) 生成 load.sql（SET client_encoding + \copy ... FROM ...）；
  3) 调 psql 执行 sql/target_schema.pg.sql 与 load.sql；
  4) 打印 PG 端各表行数，便于与 SQLite 对照。

不联网；PG 服务需由调用方启动（scripts/pg.ps1 serve 或直接 postgres.exe）。

用法：
  python crawler/load_target_pg.py
  python crawler/load_target_pg.py --db etl_amazon --port 5432
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sqlite3
import subprocess
import sys

ROOT = r"E:\ETL_Amazon"
SQLITE_DB = os.path.join(ROOT, "data", "target", "target.db")
CSV_DIR = os.path.join(ROOT, "data", "target", "pg_csv")
PG_SCHEMA = os.path.join(ROOT, "sql", "target_schema.pg.sql")
PSQL = os.path.join(ROOT, "tools", "pgsql", "bin", "psql.exe")

# 按外键依赖排序
TABLES = [
    "etl_run", "source_record", "source_file", "movie", "product_edition",
    "product_movie", "merge_decision", "boxset_member_candidate",
    "product_classification", "reviewer", "review", "quality_issue",
    "person", "person_alias", "movie_person", "field_assertion", "review_mapping",
    "field_selection", "release_date",
]


COLS = {
    "etl_run": "run_id,tool,rule_version,started_at,finished_at,status,note",
    "source_record": "source_record_id,asin,source_url,snapshot_path,content_sha256,content_length,http_status,fetched_at,status,note",
    "source_file": "file_name,rows,gz_bytes,sha256,rule_version",
    "movie": "movie_id,canonical_title,norm_title,work_type,release_year,status,rule_version,edition_count",
    "product_edition": "product_id,movie_id,media_format,language,discs,studio,product_release_date,snapshot_path,classification,breadcrumb_top",
    "product_movie": "product_id,movie_id,relation_type,rule_version,status",
    "merge_decision": "decision_id,asin_a,asin_b,title_ratio,same_norm,director_match,director_conflict,cast_jaccard,runtime_delta,year_a,year_b,year_conflict,decision,rule_id,confidence,rule_version",
    "boxset_member_candidate": "product_id,member_index,member_title_raw,status,rule_version",
    "product_classification": "product_id,classification,labels,review_required,rule_id,strength,collection_status",
    "reviewer": "reviewer_id,profile_name",
    "review": "review_id,product_id,reviewer_id,score,review_time_epoch,review_time_iso,helpful_num,helpful_den,summary,text,summary_len,text_len,record_index,part_file",
    "quality_issue": "issue_id,entity_type,entity_id,issue_type,detail,source,run_id",
    "person": "person_id,canonical_name,status,rule_version",
    "person_alias": "person_id,alias_raw,source",
    "movie_person": "movie_id,person_id,role,billing_order,source",
    "field_assertion": "assertion_id,entity_type,entity_id,field_name,raw_value,normalized_value,source_record_id,locator,rule_id,run_id,confidence,created_at",
    "review_mapping": "review_id,logical_review_id,reason",
    "field_selection": "entity_type,entity_id,field_name,selected_assertion_id,candidates,reason,rule_version,status",
    "release_date": "movie_id,value_date,value_year,precision,status,source,method,condition,confidence,retrieved_at",
}


def export_csvs(sqlite_path: str, outdir: str) -> dict:
    os.makedirs(outdir, exist_ok=True)
    conn = sqlite3.connect("file:%s?mode=ro" % sqlite_path, uri=True)
    counts = {}
    for t in TABLES:
        cur = conn.execute("SELECT %s FROM %s" % (COLS.get(t, "*"), t))
        cols = [d[0] for d in cur.description]
        path = os.path.join(outdir, t + ".csv")
        n = 0
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for row in cur:
                w.writerow(["" if v is None else v for v in row])
                n += 1
        counts[t] = n
    conn.close()
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=SQLITE_DB)
    ap.add_argument("--csvdir", default=CSV_DIR)
    ap.add_argument("--schema", default=PG_SCHEMA)
    ap.add_argument("--psql", default=PSQL)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--user", default="etl")
    ap.add_argument("--db", default="etl_amazon")
    args = ap.parse_args(argv)

    counts = export_csvs(args.sqlite, args.csvdir)
    print("导出 CSV 完成：%s" % args.csvdir)

    load_sql = os.path.join(args.csvdir, "load.sql")
    with io.open(load_sql, "w", encoding="utf-8") as fh:
        fh.write("SET client_encoding TO 'UTF8';\n")
        fh.write("BEGIN;\n")
        for t in reversed(TABLES):
            fh.write("DELETE FROM %s;\n" % t)
        for t in TABLES:
            path = os.path.join(args.csvdir, t + ".csv").replace("\\", "/")
            fh.write("\\copy %s FROM '%s' WITH (FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8');\n"
                     % (t, path))
        fh.write("COMMIT;\n")

    env = dict(os.environ)
    env.update({"PGHOST": args.host, "PGPORT": str(args.port),
                "PGUSER": args.user, "PGDATABASE": args.db,
                "PGCLIENTENCODING": "UTF8",
                # 【KEY-76】防止 COPY 无限等锁：锁超时 15s、语句超时 10min
                "PGOPTIONS": "-c statement_timeout=600000 -c lock_timeout=15000"})
    base = [args.psql, "-v", "ON_ERROR_STOP=1", "-q"]

    # 【KEY-77】装载前清掉本库残留后端（被中断的 COPY 可能还占着锁）
    subprocess.run(base + ["-c",
                           "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                           "WHERE datname='%s' AND pid <> pg_backend_pid()" % args.db],
                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)

    print("应用 schema: %s" % args.schema)
    r = subprocess.run(base + ["-f", args.schema], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        return 1

    print("装载数据: %s" % load_sql)
    r = subprocess.run(base + ["-f", load_sql], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace").strip()
    if out:
        print(out[:2000])
    if r.returncode != 0:
        return 1

    q = " UNION ALL ".join(
        ["SELECT '%s' AS t, COUNT(*) FROM %s" % (t, t) for t in TABLES])
    r = subprocess.run(base + ["-t", "-A", "-F", "|", "-c", q], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print("\nPG 端行数（与 SQLite 对照）:")
    bad = 0
    for line in r.stdout.decode("utf-8", "replace").strip().splitlines():
        t, n = line.split("|")
        local = counts.get(t, 0)
        flag = "OK" if int(n) == local else "DIFF"
        if flag == "DIFF":
            bad += 1
        print("  %-26s PG=%-8s SQLite=%-8s %s" % (t, n, local, flag))
    print("\n结果: %s" % ("全部一致" if bad == 0 else "%d 张表不一致" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
