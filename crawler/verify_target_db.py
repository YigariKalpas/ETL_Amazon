# -*- coding: utf-8 -*-
"""【KEY-53】目标层自检：建表落盘后的完整性、引用一致性与基本业务约束。

不联网、只读 data/target/target.db。退出码 0 表示全部通过。

用法：
  python crawler/verify_target_db.py
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

ROOT = r"E:\ETL_Amazon"
DEFAULT_DB = os.path.join(ROOT, "data", "target", "target.db")

CHECKS = [
    ("product_edition 的 movie_id 必须存在于 movie",
     "SELECT COUNT(*) FROM product_edition e LEFT JOIN movie m ON e.movie_id=m.movie_id WHERE m.movie_id IS NULL"),
    ("product_movie 的 movie_id 必须存在于 movie",
     "SELECT COUNT(*) FROM product_movie pm LEFT JOIN movie m ON pm.movie_id=m.movie_id WHERE m.movie_id IS NULL"),
    ("product_movie 的 product_id 必须存在于 product_edition",
     "SELECT COUNT(*) FROM product_movie pm LEFT JOIN product_edition e ON pm.product_id=e.product_id WHERE e.product_id IS NULL"),
    ("review 的 product_id 必须是已采集成功页",
     "SELECT COUNT(*) FROM review r LEFT JOIN source_record s ON r.product_id=s.source_record_id WHERE s.source_record_id IS NULL OR s.status<>'success'"),
    # 【KEY-89】helpful 异常不要求为 0（原始数据确实存在），但必须在 quality_issue 登记留痕
    ("review 的 helpful 异常必须已登记到 quality_issue",
     "SELECT COUNT(*) FROM review r WHERE r.helpful_num IS NOT NULL AND r.helpful_den IS NOT NULL "
     "AND r.helpful_num > r.helpful_den AND NOT EXISTS (SELECT 1 FROM quality_issue q "
     "WHERE q.entity_id = r.review_id AND q.issue_type IN ('helpfulness_invalid',"
     "'review_helpfulness_invalid'))"),
    ("review 的 helpful 票数不得为负",
     "SELECT COUNT(*) FROM review WHERE helpful_num < 0 OR helpful_den < 0"),
    ("review 的评分必须在 1..5 或为空",
     "SELECT COUNT(*) FROM review WHERE score IS NOT NULL AND (score < 1 OR score > 5)"),
    ("merge_decision 的 decision 只能是 auto/review/reject",
     "SELECT COUNT(*) FROM merge_decision WHERE decision NOT IN ('auto','review','reject')"),
    ("movie.edition_count 必须与实际版本数一致",
     "SELECT COUNT(*) FROM movie m WHERE m.edition_count <> (SELECT COUNT(*) FROM product_edition e WHERE e.movie_id=m.movie_id)"),
    ("product_edition 必须有分类",
     "SELECT COUNT(*) FROM product_edition WHERE classification IS NULL OR classification=''"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args(argv)
    if not os.path.isfile(args.db):
        print("数据库不存在: %s" % args.db)
        return 2
    conn = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)

    print("=" * 66)
    print("目标层表规模")
    print("=" * 66)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        print("  %-28s %8d" % (t, n))

    print()
    print("=" * 66)
    print("约束检查（失败数应为 0）")
    print("=" * 66)
    failed = 0
    for desc, sql in CHECKS:
        n = conn.execute(sql).fetchone()[0]
        flag = "OK " if n == 0 else "FAIL"
        if n:
            failed += 1
        print("  [%s] %-52s %d" % (flag, desc[:52], n))

    print()
    print("=" * 66)
    print("抽样")
    print("=" * 66)
    for sql, title in [
        ("SELECT classification, COUNT(*) FROM product_classification GROUP BY classification ORDER BY 2 DESC LIMIT 6", "分类分布 Top6"),
        ("SELECT decision, rule_id, COUNT(*) FROM merge_decision GROUP BY decision, rule_id", "合并判定"),
        ("SELECT COUNT(*) FROM movie WHERE edition_count > 1", "多版本 movie 数"),
        ("SELECT MIN(review_time_iso), MAX(review_time_iso) FROM review", "评论时间范围"),
    ]:
        print("  %s:" % title)
        for row in conn.execute(sql):
            print("     %s" % (tuple(row),))
    conn.close()
    print()
    print("结果: %s" % ("全部通过" if failed == 0 else "%d 项未通过" % failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
