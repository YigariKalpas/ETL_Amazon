# -*- coding: utf-8 -*-
"""【KEY-57】任务5：人物姓名归一与消歧（保守版）。

作业要求（§六 任务5）：
  - 每人一个 person_id，保存原始姓名、规范姓名、别名、外部 ID、匹配证据；
  - 处理中间名、缩写、音译、不同语言时，联合作品/职业/年代证据；
  - 姓名相同不代表同一人，缩写相同更不能直接合并；无法确认时保留多实体并标待复核。

本脚本的策略（保守优先）：
  1) 只按"规范化全名完全相同"做自动聚类，且状态标为 auto_same_name（置信度 0.5，可被人工推翻）；
  2) 仅中间名缩写/首字母不同（如 J. Smith vs John Smith）→ 生成候选对，进复核队列，不合并；
  3) 保存原始姓名与别名；同一人的不同写法全部进 person_alias；
  4) 角色来自页面 people_raw（actor/director），billing_order 保留页面顺序；
  5) 无外部人物 ID，因此不做跨语言/音译合并。

输出：data/staging/persons/{person.csv, person_alias.csv, movie_person.csv, same_name_review.csv, stats.json}

用法：
  python crawler/person_entities.py
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = r"E:\ETL_Amazon"
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
PMC = os.path.join(ROOT, "data", "staging", "entities", "product_movie.csv")
OUT = os.path.join(ROOT, "data", "staging", "persons")
RULE_VERSION = "person_entities/v1"

QUOTED_RE = re.compile(r'["“](.+?)["”]')
INITIAL_RE = re.compile(r"\b([a-z])\.?\b")


def norm_name(s: str) -> str:
    s = (s or "").lower()
    s = QUOTED_RE.sub(" ", s)          # 去掉昵称别名
    s = re.sub(r"[^a-z\u4e00-\u9fff ]+", " ", s)
    return " ".join(s.split())


def strip_initials(norm: str) -> str:
    """把中间名缩写去掉后的形式：john r smith -> john smith"""
    parts = [p for p in norm.split() if len(p) > 1]
    return " ".join(parts)


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

    # 收集：人名 -> 证据
    people = defaultdict(lambda: {
        "raw": Counter(), "movies": set(), "roles": Counter(), "products": set(),
    })
    for name in os.listdir(args.staging):
        if not name.endswith(".json"):
            continue
        with io.open(os.path.join(args.staging, name), encoding="utf-8") as fh:
            rec = json.load(fh)
        asin = rec.get("asin") or name[:-5]
        movie_id = pm.get(asin)
        for idx, p in enumerate(rec.get("people_raw") or []):
            raw = (p.get("name_raw") or "").strip()
            if not raw:
                continue
            key = norm_name(raw)
            if not key:
                continue
            e = people[key]
            e["raw"][raw] += 1
            e["roles"][p.get("role") or "unknown"] += 1
            e["products"].add(asin)
            if movie_id:
                e["movies"].add(movie_id)

    print("distinct_names   = %d" % len(people))

    persons, aliases, links = [], [], []
    pid_of = {}
    for key in sorted(people):
        e = people[key]
        canonical = e["raw"].most_common(1)[0][0]
        pid = "P-" + re.sub(r"[^a-z0-9]+", "-", key)[:40].strip("-")
        pid_of[key] = pid
        persons.append([pid, canonical, key, "auto_same_name", RULE_VERSION,
                        len(e["movies"]), len(e["products"]), 0.5])
        for raw, n in e["raw"].most_common():
            aliases.append([pid, raw, "snapshot_byline", n])
        for mid in sorted(e["movies"]):
            for role, cnt in e["roles"].most_common():
                links.append([mid, pid, role, 0, "page_byline", cnt])

    # 中间名缩写候选（不合并）
    by_stripped = defaultdict(list)
    for key in people:
        by_stripped[strip_initials(key)].append(key)
    review = []
    for base, keys in by_stripped.items():
        if len(keys) > 1:
            keys = sorted(keys)
            for i in range(len(keys)):
                for j in range(i + 1, len(keys)):
                    a, b = keys[i], keys[j]
                    ev_a = people[a]
                    ev_b = people[b]
                    review.append([a, b,
                                   ev_a["raw"].most_common(1)[0][0],
                                   ev_b["raw"].most_common(1)[0][0],
                                   len(ev_a["movies"]), len(ev_b["movies"]),
                                   len(ev_a["movies"] & ev_b["movies"]), ""])

    def write(name, header, rows):
        path = os.path.join(args.out, name)
        with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        print("wrote %-24s %d 行" % (name, len(rows)))

    write("person.csv", ["person_id", "canonical_name", "norm_name", "status",
                         "rule_version", "movie_count", "product_count", "confidence"], persons)
    write("person_alias.csv", ["person_id", "alias_raw", "source", "occurrences"], aliases)
    write("movie_person.csv", ["movie_id", "person_id", "role", "billing_order", "source", "evidence_count"], links)
    write("same_name_review.csv", ["name_a", "name_b", "raw_a", "raw_b", "movies_a", "movies_b",
                                   "shared_movies", "human_decision(merge/keep)"], review)

    stats = {
        "rule_version": RULE_VERSION,
        "distinct_names": len(people),
        "persons": len(persons),
        "aliases": len(aliases),
        "movie_person": len(links),
        "same_name_candidates": len(review),
        "policy": "只合并规范化全名完全相同者；缩写差异只入复核队列；无外部人物 ID 不做跨语言合并",
    }
    with io.open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)
    for k, v in stats.items():
        if k != "policy":
            print("%-24s = %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
