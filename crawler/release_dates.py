# -*- coding: utf-8 -*-
"""【KEY-59】任务6：上映信息补全（含 earliest_review_time 条件性上界）。

作业要求（§七 任务6）：
  - 先明确上映时间语义：首次公开上映 / 指定地区院线上映 / 商品发售，三者分别保存；
  - 可用 IMDb/豆瓣等允许的数据源，经实体匹配后抽取年份/日期；
  - 只有年份时保存 release_year + precision=year，不得补成当年 1 月 1 日；
  - 每次补全记录来源、原始值、抽取时间、匹配证据、选择规则、置信度；查不到保留缺失；
  - 最早评论时间只能作为"上映不晚于该时间"的条件性上界，须单独存 inferred_release_bound 与条件。

本脚本（当前无外部数据源，故如实产出缺失与推断）：
  1) 读 data/external/release_dates.csv（模板由本脚本生成，存在则作为 verified 候选）；
  2) 从 data/target/target.db 计算每个 movie 的 earliest_review_time；
  3) 输出 data/staging/release_dates/release_date.csv：

       movie_id, value_date, value_year, precision, status(verified/inferred/missing),
       source, method, condition, confidence, retrieved_at

用法：
  python crawler/release_dates.py            # 无外部源时只产出 inferred/missing
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
from collections import defaultdict

ROOT = r"E:\ETL_Amazon"
EXT = os.path.join(ROOT, "data", "external", "release_dates.csv")
DB = os.path.join(ROOT, "data", "target", "target.db")
OUT = os.path.join(ROOT, "data", "staging", "release_dates")
RULE_VERSION = "release_dates/v1"
TEMPLATE = ["movie_id", "source", "release_date", "release_year", "precision", "retrieved_at", "match_evidence"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--external", default=EXT)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(args.external)), exist_ok=True)
    os.makedirs(args.out, exist_ok=True)

    if not os.path.isfile(args.external):
        with io.open(args.external, "w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerow(TEMPLATE)
        print("外部源模板已创建（当前为空）: %s" % args.external)

    verified = {}
    with io.open(args.external, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            mid = (r.get("movie_id") or "").strip()
            if mid:
                verified[mid] = r
    print("external_rows = %d" % len(verified))

    # earliest review per movie
    earliest = {}
    if os.path.isfile(args.db):
        conn = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
        for movie_id, t in conn.execute(
                "SELECT e.movie_id, MIN(r.review_time_epoch) FROM review r "
                "JOIN product_edition e ON e.product_id=r.product_id GROUP BY e.movie_id"):
            if t:
                earliest[movie_id] = int(t)
        conn.close()
    print("movies_with_reviews = %d" % len(earliest))

    rows = []
    for movie_id, r in verified.items():
        rows.append([movie_id, r.get("release_date", ""), r.get("release_year", ""),
                     r.get("precision", "year"), "verified", r.get("source", ""),
                     "external_lookup", r.get("match_evidence", ""), 0.8, r.get("retrieved_at", "")])
    flagged = 0
    conn = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True) if os.path.isfile(args.db) else None
    movie_ids = [r[0] for r in conn.execute("SELECT movie_id FROM movie")] if conn else []
    if conn:
        conn.close()
    for movie_id in movie_ids:
        if movie_id in verified:
            continue
        if movie_id in earliest:
            flagged += 1
            rows.append([movie_id, "", "", "day", "inferred", "reviews", "earliest_review_time",
                         "上映不晚于最早评论时间（评论非预售且作品匹配成立时方可作为上界）",
                         0.3, ""])
        else:
            rows.append([movie_id, "", "", "", "missing", "", "", "", 0.0, ""])

    with io.open(os.path.join(args.out, "release_date.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["movie_id", "value_date", "value_year", "precision", "status", "source",
                    "method", "condition", "confidence", "retrieved_at"])
        w.writerows(rows)

    dist = defaultdict(int)
    for r in rows:
        dist[r[4]] += 1
    stats = {"rule_version": RULE_VERSION, "movies": len(rows), "status_dist": dict(dist),
             "external_rows": len(verified), "earliest_review_upper_bounds": flagged}
    with io.open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)
    for k, v in stats.items():
        print("%-26s = %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
