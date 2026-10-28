# -*- coding: utf-8 -*-
"""数据溯源的可验证演示：从字段反查到原始证据，并当场复核哈希。

两条链路：
  A. 页面字段 -> field_assertion -> source_record -> 快照文件（重算 SHA-256 比对）
  B. 评论字段 -> field_assertion -> 分片文件 + record_index（重读该记录并比对文本 SHA-256）

用法：python crawler/trace_evidence.py
"""
from __future__ import annotations

import gzip
import hashlib
import io
import csv
import os
import sqlite3
import sys

ROOT = r"E:\\ETL_Amazon"
TARGET = os.path.join(ROOT, "data", "target", "target.db")
REVIEWS = os.path.join(ROOT, "data", "staging", "reviews")


def trace_product(conn):
    row = conn.execute(
        "SELECT assertion_id, entity_id, field_name, raw_value, source_record_id, locator, "
        "rule_id, confidence FROM field_assertion "
        "WHERE entity_type='movie' AND field_name='canonical_title' LIMIT 1").fetchone()
    if not row:
        print("[A] 没有 movie.canonical_title 断言")
        return False
    aid, mid, field, raw, sid, loc, rule, conf = row
    print("[A] 字段溯源")
    print("    assertion_id   = %s" % aid)
    print("    entity         = movie/%s  field=%s  value=%r" % (mid, field, raw[:60]))
    print("    source_record  = %s" % sid)
    print("    locator        = %s" % loc)
    print("    rule_id        = %s  confidence=%s" % (rule, conf))
    src = conn.execute(
        "SELECT snapshot_path, content_sha256, fetched_at, source_url FROM source_record "
        "WHERE source_record_id=?", (sid,)).fetchone()
    if not src:
        print("    [FAIL] source_record 不存在")
        return False
    path, sha_db, fetched, url = src
    print("    snapshot_path  = %s" % path)
    print("    fetched_at     = %s" % fetched)
    print("    url            = %s" % url)
    if not os.path.isfile(path):
        print("    [FAIL] 快照文件缺失")
        return False
    with gzip.open(path, "rb") as fh:
        body = fh.read()
    sha_calc = hashlib.sha256(body).hexdigest()
    loc_sha = loc.split("#", 1)[0]
    ok = (sha_calc == sha_db == loc_sha)
    print("    sha256(库)     = %s" % sha_db)
    print("    sha256(重算)   = %s" % sha_calc)
    print("    locator 前缀   = %s" % loc_sha)
    print("    [%s] 三处哈希一致" % ("PASS" if ok else "FAIL"))
    return ok


def trace_review(conn):
    row = conn.execute(
        "SELECT assertion_id, entity_id, field_name, raw_value, locator, rule_id "
        "FROM field_assertion WHERE entity_type='review' AND field_name='text' LIMIT 1").fetchone()
    if not row:
        print("[B] 没有 review.text 断言")
        return False
    aid, rid_, field, raw, loc, rule = row
    print("[B] 评论溯源")
    print("    assertion_id = %s" % aid)
    print("    review_id    = %s  field=%s" % (rid_, field))
    print("    raw_value    = %s" % raw)
    print("    locator      = %s" % loc)
    print("    rule_id      = %s" % rule)
    part = loc.split("#", 1)[0]
    want_idx = int(loc.split("record=", 1)[1])
    path = os.path.join(REVIEWS, part)
    if not os.path.isfile(path):
        print("    [FAIL] 分片文件缺失: %s" % path)
        return False
    found = None
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh):
            if int(rec["record_index"]) == want_idx:
                found = rec
                break
    if not found:
        print("    [FAIL] 分片里找不到 record_index=%d" % want_idx)
        return False
    digest = hashlib.sha256(found["text"].encode("utf-8")).hexdigest()
    ok = raw.startswith("sha256:" + digest)
    print("    asin=%s user=%s score=%s time=%s" % (
        found["asin"], found["user_id"], found["score"], found["review_time_iso"]))
    print("    sha256(重算) = %s" % digest)
    print("    [%s] 断言里的摘要与重算一致" % ("PASS" if ok else "FAIL"))
    return ok


def main():
    if not os.path.isfile(TARGET):
        print("目标库不存在")
        return 2
    conn = sqlite3.connect("file:%s?mode=ro" % TARGET, uri=True)
    a = trace_product(conn)
    print()
    b = trace_review(conn)
    conn.close()
    print()
    print("overall = %s" % ("PASS" if a and b else "CHECK"))
    return 0 if a and b else 1


if __name__ == "__main__":
    sys.exit(main())
