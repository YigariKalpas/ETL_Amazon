#!/usr/bin/env python3
"""产品分类引擎 v0.3（任务3）。

口径来源：design/产品分类口径_待确认.md 中由课程负责人补充的答复
   Q1 concert 与 other 分开，新增 music 类
   Q2 Sports 按时长可入 movie；Special Interest -> pending；Documentary 分集->series，否则 movie；
      Anime 单集且 >=45min -> movie，否则 anime
   Q3 单列 movie_weak
   Q4 标题线索可用，但必须标记人工复核
   Q5 有明确线索时 product 可同时属于 movie 与 collection（多标签）
   Q6 无可用页面单列 no_page
   Q7 识别率分母 = 全部产品
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import io
import json
import os
import re
import sqlite3

STAGING = r"E:\ETL_Amazon\data\staging\product_pages"
DB = r"E:\ETL_Amazon\data\crawler\products.sqlite"
LIST = r"E:\ETL_Amazon\data\crawler\product_list.csv"
OUT_DIR = r"E:\ETL_Amazon\data\staging\classification"

COVERAGE_FIELDS = [
    "title_raw", "breadcrumb_categories", "product_release_date_raw", "studio",
    "people_raw", "actors_raw", "genre_labels_raw", "media_format", "language",
    "runtime_minutes", "discs", "mpaa_rating",
]

# 分集标记：补充 Vol./V./ep. 等实际出现在页面标题中的缩写形式
RE_SERIES = re.compile(
    r"(Season\s+\d+|Complete Series|TV Series|The Complete Season|Volume\s+\d+"
    r"|Vol\.?\s*\d+|\bV\.\s*\d+|ep\.?\s*\d+|Episodes?\b)",
    re.I,
)
RE_BOXSET = re.compile(r"(Box ?Set|Collection|Trilogy|Anthology|Double Feature)", re.I)


def features(rec: dict) -> dict:
    crumbs = rec.get("breadcrumb_categories") or []
    genres = rec.get("genre_labels_raw") or []
    title = rec.get("title_normalized") or rec.get("title_raw") or ""
    discs = rec.get("discs")
    discs_i = int(str(discs)) if discs is not None and str(discs).isdigit() else None
    return {
        "breadcrumb_top": crumbs[0] if crumbs else None,
        "breadcrumb_leaf": crumbs[-1] if crumbs else None,
        "breadcrumb_path": " > ".join(crumbs),
        "genre": ", ".join(genres),
        "media_format": rec.get("media_format"),
        "mpaa_rating": rec.get("mpaa_rating"),
        "title": title,
        "discs": discs_i,
        "runtime_minutes": rec.get("runtime_minutes"),
        "title_has_series_hint": bool(RE_SERIES.search(title)),
        "title_has_boxset_hint": bool(RE_BOXSET.search(title)),
    }


def match_one(feat, cond):
    field, op, val = cond["field"], cond["op"], cond.get("value")
    actual = feat.get(field)
    if op == "empty":
        return actual in (None, "", [])
    if op == "not_empty":
        return actual not in (None, "", [])
    if op == "eq":
        return actual == val
    if op == "in":
        return actual in (val or [])
    if op == "contains_any":
        return actual is not None and any(str(v).lower() in str(actual).lower() for v in (val or []))
    if op == "regex":
        return actual is not None and re.search(str(val), str(actual), re.I) is not None
    if op == "gte":
        return actual is not None and actual >= val
    raise ValueError("unknown op: " + op)


def match_rule(feat, rule):
    return all(match_one(feat, c) for c in rule.get("all", []))


def classify(feat, rules):
    for r in rules:
        if match_rule(feat, r):
            labels = [r["then"]] + list(r.get("also", []))
            return r["then"], labels, r.get("review", False), r["id"], r.get("strength", "weak")
    return "pending", ["pending"], False, "", "none"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default=r"E:\ETL_Amazon\design\产品分类规则_v0.3.json")
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--list", default=LIST)
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args(argv)

    with io.open(args.rules, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    rules = cfg["rules"]

    records = {}
    for p in sorted(glob.glob(os.path.join(args.staging, "*.json"))):
        with io.open(p, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
        records[rec["asin"]] = rec

    conn = sqlite3.connect(args.db)
    db = {r[0]: {"status": r[1], "http": r[2], "note": r[3] or ""}
          for r in conn.execute("SELECT asin,status,http_status,COALESCE(note,'') FROM product_page")}

    os.makedirs(args.out, exist_ok=True)
    rows = []
    for asin in sorted(set(db) | set(records)):
        info = db.get(asin, {"status": "not_collected", "http": None, "note": ""})
        rec = records.get(asin)
        if rec is None:
            rows.append({"asin": asin, "classification": "no_page", "labels": "no_page",
                         "review_required": "true", "rule_id": "", "strength": "none",
                         "collection_status": info["status"], "collection_note": info["note"],
                         "breadcrumb_top": "", "breadcrumb_leaf": "", "genre": "",
                         "media_format": "", "runtime_minutes": "", "discs": "", "title": ""})
            continue
        feat = features(rec)
        primary, labels, review, rid, strength = classify(feat, rules)
        rows.append({
            "asin": asin, "classification": primary, "labels": ";".join(labels),
            "review_required": "true" if review else "false",
            "rule_id": rid, "strength": strength,
            "collection_status": info["status"], "collection_note": info["note"],
            "breadcrumb_top": feat["breadcrumb_top"] or "",
            "breadcrumb_leaf": feat["breadcrumb_leaf"] or "",
            "genre": feat["genre"], "media_format": feat["media_format"] or "",
            "runtime_minutes": feat["runtime_minutes"] if feat["runtime_minutes"] is not None else "",
            "discs": feat["discs"] if feat["discs"] is not None else "",
            "title": feat["title"] or "",
        })

    with io.open(os.path.join(args.out, "classification.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["asin"])
        w.writeheader(); w.writerows(rows)

    by_cat = collections.Counter(r["classification"] for r in rows)
    label_ct = collections.Counter()
    for r in rows:
        for x in r["labels"].split(";"):
            if x: label_ct[x] += 1
    review_n = sum(1 for r in rows if r["review_required"] == "true")
    by_coll = collections.Counter(r["collection_status"] for r in rows)

    # C4 裁定：movie_weak 计入"已识别作品"（作覆盖率分母），但不计入"真实 movie"
    identified_asins = [r["asin"] for r in rows if r["classification"] in ("movie", "movie_weak")]
    true_movie_asins = [r["asin"] for r in rows if r["classification"] == "movie"]
    cov = {}
    for f in COVERAGE_FIELDS:
        n = sum(1 for a in identified_asins if records.get(a, {}).get(f))
        cov[f] = {"present": n, "of": len(identified_asins),
                  "rate": round(100.0 * n / len(identified_asins), 1) if identified_asins else None}

    total = 0
    if os.path.isfile(args.list):
        with io.open(args.list, "r", encoding="utf-8", newline="") as fh:
            fh.readline()
            total = sum(1 for line in fh if line.strip())

    summary = {
        "rules_version": cfg.get("version"),
        "scope": {"processed": len(rows), "products_in_list": total},
        "by_classification_primary": dict(by_cat),
        "by_label_multi": dict(label_ct),
        "review_required": review_n,
        "by_collection_status": dict(by_coll),
        "identification": {
            "identified_including_weak": len(identified_asins),
            "true_movie_only": len(true_movie_asins),
            "denominator": total,
            "rate_identified_percent": round(100.0 * len(identified_asins) / total, 4) if total else None,
            "rate_true_movie_percent": round(100.0 * len(true_movie_asins) / total, 4) if total else None,
            "note": "C4：movie_weak 计入已识别（覆盖率分母），但不计入真实 movie；Q7 分母=全部产品",
        },
        "field_coverage_of_identified_works": cov,
    }
    with io.open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2); fh.write("\n")

    print("rules =", cfg.get("version"))
    print("processed =", len(rows), " total_products =", total)
    print("primary:", dict(by_cat))
    print("multi-label:", dict(label_ct))
    print("review_required =", review_n)
    print("collection_status:", dict(by_coll))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
