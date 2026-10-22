#!/usr/bin/env python3
"""产品分类引擎（任务3），按 design/产品分类规则_*.json 执行。

裁定依据：design/产品分类口径_裁定.md
  - 分类词表严格采用文档原词：movie / non_movie / boxset / series / concert_or_other / pending
  - 标题线索只作为 flag，不决定分类（证据不足）
  - 采集状态与分类状态是两个维度（§十：状态之和 = ASIN 总数）
  - 覆盖率两套分母：已识别作品 / 全部产品（§十）
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


def features(rec: dict) -> dict:
    crumbs = rec.get("breadcrumb_categories") or []
    genres = rec.get("genre_labels_raw") or []
    title = rec.get("title_normalized") or rec.get("title_raw") or ""
    m = re.search(r"[\(\[]([^\)\]]{2,40})[\)\]]\s*$", rec.get("title_raw") or "")
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
        "title_suffix": m.group(1) if m else None,
        "discs": discs_i,
        "runtime_minutes": rec.get("runtime_minutes"),
    }


def match_one(feat: dict, cond: dict) -> bool:
    field, op, val = cond["field"], cond["op"], cond.get("value")
    actual = feat.get(field)
    if op == "empty":
        return actual in (None, "", [])
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


def match_rule(feat: dict, rule: dict) -> bool:
    return all(match_one(feat, c) for c in rule.get("all", []))


def classify(feat, rules):
    for r in rules:
        if match_rule(feat, r):
            return r["then"], r["id"], r.get("evidence", ""), r.get("strength", "weak")
    return "pending", "", "no_rule_matched", "none"


def flags_of(feat, flag_rules):
    out = []
    for r in flag_rules:
        if match_rule(feat, r):
            out.append(r["id"])
    return ";".join(out)


def read_db(path):
    conn = sqlite3.connect(path)
    return {r[0]: {"status": r[1], "http": r[2], "note": r[3] or ""}
            for r in conn.execute("SELECT asin, status, http_status, COALESCE(note,'') FROM product_page")}


def count_list(path):
    if not os.path.isfile(path):
        return 0
    n = 0
    with io.open(path, "r", encoding="utf-8", newline="") as fh:
        fh.readline()
        for line in fh:
            if line.strip():
                n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default=r"E:\ETL_Amazon\design\产品分类规则_v0.2.json")
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--list", default=LIST)
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args(argv)

    with io.open(args.rules, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    rules, flag_rules = cfg["rules"], cfg.get("flag_rules", [])

    records = {}
    for p in sorted(glob.glob(os.path.join(args.staging, "*.json"))):
        with io.open(p, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
        records[rec["asin"]] = rec

    db = read_db(args.db)
    os.makedirs(args.out, exist_ok=True)

    rows = []
    all_asins = sorted(set(db) | set(records))
    for asin in all_asins:
        rec = records.get(asin)
        info = db.get(asin, {"status": "not_collected", "http": None, "note": ""})
        if rec is None:
            rows.append({
                "asin": asin, "classification": "pending", "rule_id": "",
                "strength": "none", "flags": "no_page",
                "collection_status": info["status"], "collection_note": info["note"],
                "breadcrumb_top": "", "breadcrumb_leaf": "", "genre": "",
                "media_format": "", "title": "",
            })
            continue
        feat = features(rec)
        cat, rid, ev, strength = classify(feat, rules)
        rows.append({
            "asin": asin, "classification": cat, "rule_id": rid,
            "strength": strength, "flags": flags_of(feat, flag_rules),
            "collection_status": info["status"], "collection_note": info["note"],
            "breadcrumb_top": feat["breadcrumb_top"] or "",
            "breadcrumb_leaf": feat["breadcrumb_leaf"] or "",
            "genre": feat["genre"], "media_format": feat["media_format"] or "",
            "title": feat["title"] or "",
        })

    with io.open(os.path.join(args.out, "classification.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["asin"])
        w.writeheader()
        w.writerows(rows)

    by_cat = collections.Counter(r["classification"] for r in rows)
    by_rule = collections.Counter(r["rule_id"] or "(none)" for r in rows)
    by_strength = collections.Counter(r["strength"] for r in rows)
    by_coll = collections.Counter(r["collection_status"] for r in rows)

    identified = by_cat.get("movie", 0)
    identified_asins = [r["asin"] for r in rows if r["classification"] == "movie"]
    cov = {}
    for f in COVERAGE_FIELDS:
        n = sum(1 for a in identified_asins if records.get(a, {}).get(f))
        cov[f] = {"present": n, "of_identified": identified,
                  "rate": round(100.0 * n / identified, 1) if identified else None}

    products_total = count_list(args.list)
    summary = {
        "rules_version": cfg.get("version"),
        "rules_basis": cfg.get("basis"),
        "scope": {"processed_products": len(rows), "products_in_list": products_total},
        "by_classification": dict(by_cat),
        "by_rule": dict(by_rule),
        "by_strength": dict(by_strength),
        "by_collection_status": dict(by_coll),
        "coverage": {
            "denominator_identified_works": identified,
            "field_coverage_of_identified": cov,
            "identification_coverage_of_all_products": {
                "identified": identified, "total": products_total,
                "rate": round(100.0 * identified / products_total, 4) if products_total else None,
            },
        },
        "notes": [
            "分类词表严格采用 docs/作业说明 §四任务3 的原词",
            "标题线索仅作为 flags，不决定分类（证据不足）",
            "collection_status 为采集维度，与分类维度相互独立（§十）",
            "Q3（弱证据是否算电影）尚未确定：当前 strength=weak 的 movie 可随时按口径重算",
        ],
    }
    with io.open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("rules =", cfg.get("version"))
    print("processed =", len(rows), " list_total =", products_total)
    print("by_classification:", dict(by_cat))
    print("by_strength:", dict(by_strength))
    print("by_collection_status:", dict(by_coll))
    print("identified_works =", identified)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
