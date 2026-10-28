# -*- coding: utf-8 -*-
"""【KEY-58】任务9：字段级数据血缘（候选值 + 最终选择）。

作业要求（§九）：
  - 原始证据可定位：网页 URL 与快照哈希，或数据文件版本与记录序号；同时记录获取时间；
  - 候选值可追踪：entity_type / entity_id / field_name / raw_value / normalized_value /
    source_record_id / 字段定位信息 / 抽取规则版本；
  - 最终选择可解释：selected_assertion_id、决策理由与版本，未采用候选仍可查看。

做法：
  1) 逐条产品字段生成 assertion：locator = 快照哈希 + JSON 路径（如 $.detail.studio）；
  2) 电影级字段（canonical_title）由某个成员商品的 assertion 支撑，记录其 asin 与定位；
  3) release_year 目前**没有可信来源**（页面只有商品发售日），因此不生成断言，
     在 field_selection 里显式记 status=missing，而不是编造；
  4) 输出 field_assertion.csv / field_selection.csv / assertion_stats.json。

用法：
  python crawler/build_field_assertions.py
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter

ROOT = r"E:\ETL_Amazon"
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
PMC = os.path.join(ROOT, "data", "staging", "entities", "product_movie.csv")
OUT = os.path.join(ROOT, "data", "staging", "lineage")
RULE_VERSION = "field_assertion/v1"

# 产品级字段 -> JSON 路径
PRODUCT_FIELDS = [
    ("title_raw", "$.title_raw"),
    ("media_format", "$.media_format"),
    ("studio", "$.studio"),
    ("runtime_minutes", "$.runtime_minutes"),
    ("actors_raw", "$.actors_raw"),
    ("product_release_date_raw", "$.product_release_date_raw"),
    ("language", "$.language"),
    ("discs", "$.discs"),
    ("mpaa_rating", "$.mpaa_rating"),
    ("genre_labels_raw", "$.genre_labels_raw"),
    ("breadcrumb_categories", "$.breadcrumb_categories"),
    ("people_raw", "$.people_raw"),
]


def val(rec: dict, path: str):
    key = path.split(".", 1)[1]
    return rec.get(key)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--product-movie", default=PMC)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    pm = {}
    if os.path.isfile(args.product_movie):
        with io.open(args.product_movie, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                pm[r["product_id"]] = r["movie_id"]

    assertions = []
    movie_src = {}   # movie_id -> (asin, sha, title)
    counter = Counter()
    for name in os.listdir(args.staging):
        if not name.endswith(".json"):
            continue
        with io.open(os.path.join(args.staging, name), encoding="utf-8") as fh:
            rec = json.load(fh)
        asin = rec.get("asin") or name[:-5]
        sha = rec.get("snapshot_sha256") or ""
        movie_id = pm.get(asin)
        if movie_id and movie_id not in movie_src:
            movie_src[movie_id] = (asin, sha, rec.get("title_raw") or "")
        for field, path in PRODUCT_FIELDS:
            v = val(rec, path)
            if v in (None, "", [], {}):
                continue
            a_id = "FA-%s-%s" % (asin, field)
            raw = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
            assertions.append([
                a_id, "product", asin, field, raw[:2000],
                raw[:2000], asin, "%s#%s" % (sha, path), "parse_product_page/v1",
                "GO-BUILD", 1.0, rec.get("extracted_at") or "",
            ])
            counter[field] += 1

    for movie_id, (asin, sha, title) in movie_src.items():
        if not title:
            continue
        a_id = "FA-%s-canonical_title" % movie_id
        assertions.append([
            a_id, "movie", movie_id, "canonical_title", title, title, asin,
            "%s#$.title_raw" % sha, "movie_entities/v1", "GO-BUILD", 0.8,
            "",
        ])
        counter["movie.canonical_title"] += 1

    # ---------- 评论级断言（任务9：原始评论可定位到分片+记录号） ----------
    review_assertions = 0
    db_path = os.path.join(ROOT, "data", "target", "target.db")
    if os.path.isfile(db_path):
        import hashlib
        import sqlite3
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        cur = conn.execute(
            "SELECT review_id, product_id, score, review_time_epoch, review_time_iso, "
            "helpful_num, helpful_den, summary, text, record_index, part_file FROM review")
        for (rid_, pid, score, tepoch, tiso, hn, hd, summary, text, ridx, part) in cur:
            loc = "%s#record=%s" % (part or "?", ridx)
            if score is not None:
                assertions.append(["FA-%s-score" % rid_, "review", rid_, "score",
                                   str(score), str(score), pid, loc,
                                   "extract_reviews/v1", "GO-BUILD", 1.0, tiso or ""])
            if tepoch is not None:
                assertions.append(["FA-%s-review_time" % rid_, "review", rid_,
                                   "review_time_epoch", str(tepoch), tiso or "", pid, loc,
                                   "extract_reviews/v1", "GO-BUILD", 1.0, tiso or ""])
            if text:
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                assertions.append(["FA-%s-text" % rid_, "review", rid_, "text",
                                   "sha256:%s;len=%d" % (digest, len(text)),
                                   text[:100], pid, loc, "extract_reviews/v1",
                                   "GO-BUILD", 1.0, tiso or ""])
                review_assertions += 1
        conn.close()
    print("review_assertions = %d" % review_assertions)

    # 电影级选择
    selections = []
    for movie_id in sorted(movie_src):
        selections.append(["movie", movie_id, "canonical_title",
                           "FA-%s-canonical_title" % movie_id, 1,
                           "唯一候选：取该 movie 首个成员商品的页面标题", "movie_entities/v1", "selected"])
        selections.append(["movie", movie_id, "release_year", "", 0,
                           "无可信来源：页面 Release date 是商品发售日，不是上映年", "field_selection/v1", "missing"])

    with io.open(os.path.join(args.out, "field_assertion.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["assertion_id", "entity_type", "entity_id", "field_name", "raw_value",
                    "normalized_value", "source_record_id", "locator", "rule_id", "run_id",
                    "confidence", "created_at"])
        w.writerows(assertions)

    with io.open(os.path.join(args.out, "field_selection.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["entity_type", "entity_id", "field_name", "selected_assertion_id",
                    "candidates", "reason", "rule_version", "status"])
        w.writerows(selections)

    stats = {"rule_version": RULE_VERSION, "assertions": len(assertions),
             "movies": len(movie_src), "selections": len(selections),
             "per_field": dict(counter)}
    with io.open(os.path.join(args.out, "assertion_stats.json"), "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)
    print("assertions   = %d" % len(assertions))
    print("movies       = %d" % len(movie_src))
    print("selections   = %d" % len(selections))
    print("per_field    = %s" % dict(sorted(counter.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
