# -*- coding: utf-8 -*-
"""【KEY-52】把"已确认"的成果建表落盘（目标层）。

数据来源全部是本地文件，不联网、不读原始 8.69 GB 评论文件：
  - 状态库      data/crawler/products.sqlite        -> source_record / quality_issue
  - 分类结果    data/staging/classification/*.csv   -> product_classification
  - 实体结果    data/staging/entities/*.csv         -> movie / product_edition / product_movie / merge_decision / boxset_member_candidate
  - 评论分片    data/staging/reviews/part-*.csv.gz  -> reviewer / review（只装已采集 ASIN 的评论）
  - 清单        collect_manifest.json / reviews/manifest.json -> source_record 哈希、source_file

用法：
  python crawler/build_target_db.py                  # 全量重建 data/target/target.db
  python crawler/build_target_db.py --keep           # 保留旧库（只补齐缺失表）
  python crawler/build_target_db.py --limit-reviews 0  # 0=不限
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = r"E:\ETL_Amazon"
DB_OUT = os.path.join(ROOT, "data", "target", "target.db")
SCHEMA = os.path.join(ROOT, "sql", "target_schema.sqlite.sql")
SRC_DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
CLS = os.path.join(ROOT, "data", "staging", "classification", "classification.csv")
ENT = os.path.join(ROOT, "data", "staging", "entities")
REV = os.path.join(ROOT, "data", "staging", "reviews")
MAN_PAGES = os.path.join(ROOT, "data", "crawler", "collect_manifest.json")
MAN_REV = os.path.join(ROOT, "data", "staging", "reviews", "manifest.json")
RULE_VERSION = "target_build/v1"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rid(prefix: str, *parts) -> str:
    h = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]
    return "%s-%s" % (prefix, h)


def read_csv(path: str):
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_OUT)
    ap.add_argument("--schema", default=SCHEMA)
    ap.add_argument("--keep", action="store_true", help="保留已有库")
    ap.add_argument("--limit-reviews", type=int, default=0, help="最多装入多少条评论（0=不限）")
    args = ap.parse_args(argv)

    started = now()
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    if not args.keep and os.path.isfile(args.db):
        os.remove(args.db)
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")
    with io.open(args.schema, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    run_id = "RUN-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    conn.execute("INSERT OR REPLACE INTO etl_run(run_id,tool,rule_version,started_at,status,note) "
                 "VALUES(?,?,?,?,?,?)", (run_id, "python", RULE_VERSION, started, "running", "build_target_db"))

    stats = {}

    # ---------- source_record ----------
    src = sqlite3.connect(SRC_DB)
    rows = src.execute("SELECT asin, source_url, snapshot_path, content_sha256, content_length, "
                       "http_status, fetched_at, status, note FROM product_page").fetchall()
    conn.executemany(
        "INSERT OR REPLACE INTO source_record(source_record_id,asin,source_url,snapshot_path,"
        "content_sha256,content_length,http_status,fetched_at,status,note) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(r[0], r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]) for r in rows])
    src.close()
    stats["source_record"] = len(rows)
    collected = {r[0] for r in rows if r[7] == "success"}
    stats["collected_success"] = len(collected)

    # ---------- source_file ----------
    files = []
    if os.path.isfile(MAN_REV):
        with io.open(MAN_REV, encoding="utf-8") as fh:
            man = json.load(fh)
        for f in man.get("files", []):
            files.append((f["file"], f["rows"], f["gz_bytes"], f["sha256"], "extract_reviews/v1"))
    conn.executemany("INSERT OR REPLACE INTO source_file(file_name,rows,gz_bytes,sha256,rule_version) "
                     "VALUES(?,?,?,?,?)", files)
    stats["source_file"] = len(files)

    # ---------- movie / edition / product_movie / merge_decision / boxset ----------
    movies = read_csv(os.path.join(ENT, "movie.csv"))
    conn.executemany(
        "INSERT OR REPLACE INTO movie(movie_id,canonical_title,norm_title,work_type,release_year,"
        "status,rule_version,edition_count) VALUES(?,?,?,?,?,?,?,?)",
        [(m["movie_id"], m["canonical_title"], m["norm_title"], m["work_type"],
          int(m["release_year"]) if m["release_year"] else None, m["status"],
          m["rule_version"], int(m["edition_count"] or 1)) for m in movies])
    stats["movie"] = len(movies)

    cls = {r["asin"]: r for r in read_csv(CLS)}
    editions = read_csv(os.path.join(ENT, "product_edition.csv"))
    edition_rows = []
    for e in editions:
        c = cls.get(e["product_id"], {})
        edition_rows.append((e["product_id"], e["movie_id"], e["media_format"], e["language"],
                             e["discs"], e["studio"], e["product_release_date"], e["snapshot_path"],
                             c.get("classification"), c.get("breadcrumb_top")))
    conn.executemany(
        "INSERT OR REPLACE INTO product_edition(product_id,movie_id,media_format,language,discs,"
        "studio,product_release_date,snapshot_path,classification,breadcrumb_top) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)", edition_rows)
    stats["product_edition"] = len(edition_rows)

    pm = read_csv(os.path.join(ENT, "product_movie.csv"))
    conn.executemany("INSERT OR REPLACE INTO product_movie(product_id,movie_id,relation_type,"
                     "rule_version,status) VALUES(?,?,?,?,?)",
                     [(r["product_id"], r["movie_id"], r["relation_type"], r["rule_version"], r["status"])
                      for r in pm])
    stats["product_movie"] = len(pm)

    md = read_csv(os.path.join(ENT, "merge_decision.csv"))
    conn.executemany(
        "INSERT OR REPLACE INTO merge_decision(decision_id,asin_a,asin_b,title_ratio,same_norm,"
        "director_match,director_conflict,cast_jaccard,runtime_delta,year_a,year_b,year_conflict,"
        "decision,rule_id,confidence,rule_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(rid("MD", r["asin_a"], r["asin_b"]), r["asin_a"], r["asin_b"],
          float(r["title_ratio"] or 0), int(r["same_norm"] == "True"),
          int(r["director_match"] == "True"), int(r["director_conflict"] == "True"),
          float(r["cast_jaccard"] or 0),
          int(r["runtime_delta"]) if r["runtime_delta"] else None,
          int(r["year_a"]) if r["year_a"] else None, int(r["year_b"]) if r["year_b"] else None,
          int(r["year_conflict"] == "True"), r["decision"], r["rule_id"],
          float(r["confidence"] or 0), "movie_entities/v1") for r in md])
    stats["merge_decision"] = len(md)

    bx = read_csv(os.path.join(ENT, "boxset_candidates.csv"))
    conn.executemany("INSERT OR REPLACE INTO boxset_member_candidate(product_id,member_index,"
                     "member_title_raw,status,rule_version) VALUES(?,?,?,?,?)",
                     [(r["product_id"], int(r["member_index"]), r["member_title_raw"],
                       r["status"], r["rule_version"]) for r in bx])
    stats["boxset_member_candidate"] = len(bx)

    # ---------- product_classification ----------
    conn.executemany(
        "INSERT OR REPLACE INTO product_classification(product_id,classification,labels,"
        "review_required,rule_id,strength,collection_status) VALUES(?,?,?,?,?,?,?)",
        [(r["asin"], r["classification"], r["labels"], int(r["review_required"] == "true"),
          r["rule_id"], r["strength"], r["collection_status"]) for r in cls.values()])
    stats["product_classification"] = len(cls)

    # ---------- quality_issue ----------
    issues = []
    for r in rows:
        if r[7] == "not_found":
            issues.append((rid("QI", r[0], "not_found"), "product", r[0], "page_not_found",
                           r[8] or "http_404", "products.sqlite", run_id))
        elif r[7] == "parse_failed":
            issues.append((rid("QI", r[0], "parse_failed"), "product", r[0], "template_unparsed",
                           r[8] or "", "products.sqlite", run_id))
    rejects = read_csv(os.path.join(REV, "rejects.csv"))
    for r in rejects:
        issues.append((rid("QI", r.get("record_index"), r.get("reasons", "")), "review",
                       r.get("product_id", ""), "review_field_invalid",
                       "%s; score=%s; time=%s; helpful=%s" % (r.get("reasons", ""), r.get("score_raw", ""),
                                                              r.get("time_raw", ""), r.get("helpfulness_raw", "")),
                       "rejects.csv", run_id))
    # 【KEY-84】任务7 的冲突/候选也进质量表，便于统一查询与统计
    conflict_csv = os.path.join(ROOT, "data", "staging", "conflicts", "conflicts.csv")
    for r in read_csv(conflict_csv):
        issues.append((rid("QI", "conflict", r.get("entity_id", ""), r.get("field_name", "")),
                       r.get("entity_type", ""), r.get("entity_id", ""),
                       "source_conflict_" + (r.get("status") or "candidate"),
                       "%s | %s" % (r.get("field_name", ""), r.get("detail", "")),
                       "conflicts.csv", run_id))
    conn.executemany("INSERT OR REPLACE INTO quality_issue(issue_id,entity_type,entity_id,issue_type,"
                     "detail,source,run_id) VALUES(?,?,?,?,?,?,?)", issues)
    stats["quality_issue"] = len(issues)

    # ---------- 任务5 人物 ----------
    PERSONS = os.path.join(ROOT, "data", "staging", "persons")
    persons = read_csv(os.path.join(PERSONS, "person.csv"))
    conn.executemany("INSERT OR REPLACE INTO person(person_id,canonical_name,status,rule_version) "
                     "VALUES(?,?,?,?)",
                     [(r["person_id"], r["canonical_name"], r["status"], r["rule_version"]) for r in persons])
    stats["person"] = len(persons)

    palias = read_csv(os.path.join(PERSONS, "person_alias.csv"))
    conn.executemany("INSERT OR REPLACE INTO person_alias(person_id,alias_raw,source) VALUES(?,?,?)",
                     [(r["person_id"], r["alias_raw"], r["source"]) for r in palias])
    stats["person_alias"] = len(palias)

    mperson = read_csv(os.path.join(PERSONS, "movie_person.csv"))
    conn.executemany("INSERT OR REPLACE INTO movie_person(movie_id,person_id,role,billing_order,source) "
                     "VALUES(?,?,?,?,?)",
                     [(r["movie_id"], r["person_id"], r["role"],
                       int(r["billing_order"] or 0), r["source"]) for r in mperson])
    stats["movie_person"] = len(mperson)

    # ---------- 任务8 去重映射 ----------
    DEDUP = os.path.join(ROOT, "data", "staging", "dedup")
    rmap = read_csv(os.path.join(DEDUP, "review_mapping.csv"))
    conn.executemany("INSERT OR REPLACE INTO review_mapping(review_id,logical_review_id,reason) "
                     "VALUES(?,?,?)",
                     [(r["review_id"], r["logical_review_id"], r["reason"]) for r in rmap])
    stats["review_mapping"] = len(rmap)

    # ---------- 任务9 血缘 + 任务7 选择 + 任务6 上映 ----------
    LIN = os.path.join(ROOT, "data", "staging", "lineage")
    asserts = read_csv(os.path.join(LIN, "field_assertion.csv"))
    conn.executemany(
        "INSERT OR REPLACE INTO field_assertion(assertion_id,entity_type,entity_id,field_name,"
        "raw_value,normalized_value,source_record_id,locator,rule_id,run_id,confidence,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["assertion_id"], r["entity_type"], r["entity_id"], r["field_name"], r["raw_value"],
          r["normalized_value"], r["source_record_id"], r["locator"], r["rule_id"], run_id,
          float(r["confidence"] or 0), r["created_at"]) for r in asserts])
    stats["field_assertion"] = len(asserts)

    CONF = os.path.join(ROOT, "data", "staging", "conflicts")
    sels = read_csv(os.path.join(CONF, "field_selection.csv"))
    conn.executemany(
        "INSERT OR REPLACE INTO field_selection(entity_type,entity_id,field_name,"
        "selected_assertion_id,candidates,reason,rule_version,status) VALUES(?,?,?,?,?,?,?,?)",
        [(r["entity_type"], r["entity_id"], r["field_name"], r["selected_assertion_id"],
          int(r["candidates"] or 0), r["reason"], r["rule_version"], r["status"]) for r in sels])
    stats["field_selection"] = len(sels)

    REL = os.path.join(ROOT, "data", "staging", "release_dates")
    rels = read_csv(os.path.join(REL, "release_date.csv"))
    conn.executemany(
        "INSERT OR REPLACE INTO release_date(movie_id,value_date,value_year,precision,status,"
        "source,method,condition,confidence,retrieved_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(r["movie_id"], r["value_date"], int(r["value_year"]) if r["value_year"] else None,
          r["precision"], r["status"], r["source"], r["method"], r["condition"],
          float(r["confidence"] or 0), r["retrieved_at"]) for r in rels])
    stats["release_date"] = len(rels)

    # ---------- reviewer / review（只装已采集 ASIN 的评论）----------
    parts = sorted(f for f in os.listdir(REV) if f.startswith("part-") and f.endswith(".csv.gz"))
    seq = 0
    used_parts = 0
    for name in parts:
        if args.limit_reviews and seq >= args.limit_reviews:
            break
        path = os.path.join(REV, name)
        batch_rv, batch_ub = [], []
        with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
            for rec in csv.DictReader(fh):
                asin = rec["asin"]
                if asin not in collected:
                    continue
                review_id = rid("RV", asin, rec["record_index"])
                batch_ub.append((rec["user_id"], rec["profile_name"]))
                batch_rv.append((review_id, asin, rec["user_id"],
                                 float(rec["score"]) if rec["score"] else None,
                                 int(rec["review_time_epoch"]) if rec["review_time_epoch"] else None,
                                 rec["review_time_iso"] or None,
                                 int(rec["helpful_num"]) if rec["helpful_num"] else None,
                                 int(rec["helpful_den"]) if rec["helpful_den"] else None,
                                 rec["summary"], rec["text"], int(rec["summary_len"] or 0),
                                 int(rec["text_len"] or 0), int(rec["record_index"]), name))
                seq += 1
                if args.limit_reviews and seq >= args.limit_reviews:
                    break
        conn.executemany("INSERT OR IGNORE INTO reviewer(reviewer_id,profile_name) VALUES(?,?)", batch_ub)
        conn.executemany(
            "INSERT OR REPLACE INTO review(review_id,product_id,reviewer_id,score,review_time_epoch,"
            "review_time_iso,helpful_num,helpful_den,summary,text,summary_len,text_len,record_index,"
            "part_file) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch_rv)
        used_parts += 1
        conn.commit()
    stats["review"] = seq
    stats["parts_scanned"] = used_parts
    stats["reviewer"] = conn.execute("SELECT COUNT(*) FROM reviewer").fetchone()[0]

    conn.execute("UPDATE etl_run SET status=?, finished_at=?, note=? WHERE run_id=?",
                 ("ok", now(), json.dumps(stats, ensure_ascii=False), run_id))
    conn.commit()

    summary = {"run_id": run_id, "db": args.db, "rule_version": RULE_VERSION,
               "built_at": now(), **stats}
    out = os.path.join(os.path.dirname(os.path.abspath(args.db)), "build_summary.json")
    with io.open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    for k, v in stats.items():
        print("%-26s = %s" % (k, v))
    print("target_db = %s" % args.db)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
