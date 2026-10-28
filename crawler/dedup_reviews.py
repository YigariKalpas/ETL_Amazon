# -*- coding: utf-8 -*-
"""【KEY-56】任务8：评论清洗与重复识别。

作业要求（§七 任务8）：
  - 声明去重目的与键；相同 ASIN/用户/时间/评分/文本可做候选，但必须核验差异；
  - 用户在不同版本上的相似评论不能因文本接近而无条件删除；
  - 原始记录全部保留，逻辑评论与原始记录用映射关联；
  - helpfulness 分子不得大于分母、票数不得为负，异常要记录；
  - 合并后的电影评分须说明是否去重、是否含合集、分母是什么。

做法：
  1) 只读 data/target/target.db 的 review 表（已采集页面范围内的 3 万余条），不碰 8.69 GB 原文；
  2) 生成三类候选：
       D1 完全重复：同一 product + reviewer + time + score + 文本完全一致
       D2 文本近似：同一 product 内规范化文本一致（去 HTML、大小写、标点）
       D3 跨商品复用：同一 reviewer + 规范化文本，出现在不同 product（只标记，不删除）
  3) 原始记录一条不改；输出 review_mapping.csv（review_id → logical_review_id + reason）；
  4) helpfulness 异常写 quality_issues.csv；
  5) 输出 movie_rating.csv（按 movie_id 聚合，明确去重口径与分母）。

用法：
  python crawler/dedup_reviews.py
  python crawler/dedup_reviews.py --db data/target/target.db --out data/staging/dedup
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

ROOT = r"E:\ETL_Amazon"
DEFAULT_DB = os.path.join(ROOT, "data", "target", "target.db")
DEFAULT_OUT = os.path.join(ROOT, "data", "staging", "dedup")
RULE_VERSION = "dedup/v1"

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def norm_text(s: str) -> str:
    s = html.unescape(s or "")
    s = TAG_RE.sub(" ", s)
    s = s.lower()
    s = PUNCT_RE.sub(" ", s)
    return WS_RE.sub(" ", s).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    conn = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    rows = conn.execute(
        "SELECT r.review_id, r.product_id, r.reviewer_id, r.score, r.review_time_epoch, "
        "r.helpful_num, r.helpful_den, r.text, e.movie_id "
        "FROM review r LEFT JOIN product_edition e ON e.product_id=r.product_id").fetchall()
    conn.close()
    print("reviews_loaded   = %d" % len(rows))

    issues = []
    mapping = []
    stats = {"D1_exact": 0, "D2_near_text": 0, "D3_cross_product": 0}

    # ---- helpfulness 与基础校验 ----
    for (rid_, pid, uid, score, tepoch, hn, hd, text, movie_id) in rows:
        if hn is not None and hd is not None and (hn > hd or hn < 0 or hd < 0):
            issues.append(["review", rid_, "helpfulness_invalid", "num=%s den=%s" % (hn, hd)])
        if score is not None and not (1.0 <= float(score) <= 5.0):
            issues.append(["review", rid_, "score_out_of_range", str(score)])

    # ---- D1 完全重复（同商品同用户同时间同分同文本）----
    key_map = defaultdict(list)
    for (rid_, pid, uid, score, tepoch, hn, hd, text, movie_id) in rows:
        k = (pid, uid, tepoch, score, (text or ""))
        key_map[k].append(rid_)
    for k, ids in key_map.items():
        if len(ids) > 1:
            keep = min(ids)
            for r in ids:
                if r != keep:
                    mapping.append([r, keep, "D1_exact_duplicate"])
                    stats["D1_exact"] += 1

    # ---- D2 同商品内文本规范化后一致 ----
    norm_map = defaultdict(list)
    for (rid_, pid, uid, score, tepoch, hn, hd, text, movie_id) in rows:
        n = norm_text(text)
        if len(n) >= 30:          # 太短的文本不做近似判定，避免误伤
            norm_map[(pid, n)].append(rid_)
    for k, ids in norm_map.items():
        if len(ids) > 1:
            keep = min(ids)
            for r in ids:
                if r != keep and not any(m[0] == r for m in mapping):
                    mapping.append([r, keep, "D2_near_text"])
                    stats["D2_near_text"] += 1

    # ---- D3 跨商品复用（同一用户同一文本，不同商品）----
    user_text = defaultdict(list)
    for (rid_, pid, uid, score, tepoch, hn, hd, text, movie_id) in rows:
        n = norm_text(text)
        if uid and len(n) >= 40:
            user_text[(uid, n)].append((rid_, pid))
    for k, lst in user_text.items():
        pids = {p for _r, p in lst}
        if len(pids) > 1:
            # 不删除：只标记为同一逻辑评论的跨版本候选
            for r, _p in lst:
                if not any(m[0] == r for m in mapping):
                    mapping.append([r, min(x[0] for x in lst), "D3_cross_product_candidate"])
                    stats["D3_cross_product"] += 1

    # ---- 输出映射与异常 ----
    with io.open(os.path.join(args.out, "review_mapping.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["review_id", "logical_review_id", "reason", "rule_version"])
        for r, keep, reason in mapping:
            w.writerow([r, keep, reason, RULE_VERSION])

    with io.open(os.path.join(args.out, "quality_issues.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["entity_type", "entity_id", "issue_type", "detail"])
        w.writerows(issues)

    # ---- 电影评分（明确去重口径）----
    # 口径：只用 D1/D2 去重后的逻辑评论；跨商品候选(D3) 不去重；合集商品评论默认归属商品本身
    logical = {}
    for (rid_, pid, uid, score, tepoch, hn, hd, text, movie_id) in rows:
        logical[rid_] = (pid, score, movie_id)
    drop = {m[0] for m in mapping if m[2] in ("D1_exact_duplicate", "D2_near_text")}
    agg = defaultdict(lambda: [0, 0.0])
    for rid_, (pid, score, movie_id) in logical.items():
        if rid_ in drop or score is None or not movie_id:
            continue
        agg[movie_id][0] += 1
        agg[movie_id][1] += float(score)
    rated = []
    for movie_id, (n, s) in agg.items():
        rated.append([movie_id, n, round(s / n, 3)])
    rated.sort(key=lambda x: -x[1])
    with io.open(os.path.join(args.out, "movie_rating.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["movie_id", "review_count_deduped", "avg_score"])
        w.writerows(rated)

    summary = {
        "rule_version": RULE_VERSION,
        "reviews_loaded": len(rows),
        "mapping_rows": len(mapping),
        "stats": stats,
        "quality_issues": len(issues),
        "movie_rating_rows": len(rated),
        "policy": {
            "D1": "同商品+同用户+同时间+同分+同文本 -> 保留最小 review_id，其余映射到它",
            "D2": "同商品内规范化文本一致（去 HTML/大小写/标点）且长度>=30 -> 保留最小 id",
            "D3": "同用户同文本跨商品 -> 仅标记为候选，不删除（作业要求不能无条件删除）",
            "movie_rating": "D1/D2 去重后的逻辑评论；D3 不去重；合集评论归属商品自身",
        },
    }
    with io.open(os.path.join(args.out, "dedup_stats.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    for k, v in summary.items():
        if k != "policy":
            print("%-20s = %s" % (k, v))
    print("out              = %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
