#!/usr/bin/env python3
"""产品分类引擎（作业任务3：电影页面筛选）。

设计要点：
  - **判定规则不写死在代码里**，而是放在 design/产品分类规则_*.json，
    便于审阅、版本化与修改（口径属业务决策，应由人确认）；
  - 每条规则给出 rule_id、匹配条件、结论、证据强度（strong/weak）；
  - 未命中任何规则 => pending（待确认），符合文档「证据不足保留待确认」；
  - 每个产品输出：分类、命中规则、理由、所用信号，形成可复核证据。

用法:
    python classify_products.py --rules design/产品分类规则_v0.1-draft.json
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

STAGING = r"E:\ETL_Amazon\data\staging\product_pages"
DB = r"E:\ETL_Amazon\data\crawler\products.sqlite"
OUT_DIR = r"E:\ETL_Amazon\data\staging\classification"


def features(rec: dict) -> dict:
    crumbs = rec.get("breadcrumb_categories") or []
    genres = rec.get("genre_labels_raw") or []
    title = rec.get("title_normalized") or rec.get("title_raw") or ""
    m = re.search(r"[\(\[]([^\)\]]{2,40})[\)\]]\s*$", rec.get("title_raw") or "")
    discs = rec.get("discs")
    try:
        discs_i = int(str(discs)) if discs is not None and str(discs).isdigit() else None
    except Exception:
        discs_i = None
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
    field = cond["field"]
    op = cond["op"]
    val = cond.get("value")
    actual = feat.get(field)

    if op == "empty":
        return actual in (None, "", [])
    if op == "eq":
        return actual == val
    if op == "in":
        return actual in (val or [])
    if op == "contains_any":
        if actual is None:
            return False
        a = str(actual).lower()
        return any(str(v).lower() in a for v in (val or []))
    if op == "regex":
        if actual is None:
            return False
        return re.search(str(val), str(actual), re.I) is not None
    if op == "gte":
        return actual is not None and actual >= val
    raise ValueError("unknown op: " + op)


def match_rule(feat: dict, rule: dict) -> bool:
    return all(match_one(feat, c) for c in rule.get("all", []))


def classify(feat: dict, rules: list[dict]):
    for r in rules:
        if match_rule(feat, r):
            return r["then"], r["id"], r.get("evidence", ""), r.get("strength", "weak")
    return "pending", None, "no_rule_matched", "none"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default=r"E:\ETL_Amazon\design\产品分类规则_v0.1-draft.json")
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args(argv)

    with io.open(args.rules, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    rules = cfg["rules"]
    os.makedirs(args.out, exist_ok=True)

    rows = []
    for p in sorted(glob.glob(os.path.join(args.staging, "*.json"))):
        with io.open(p, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
        feat = features(rec)
        cat, rid, ev, strength = classify(feat, rules)
        rows.append({
            "asin": rec["asin"],
            "category": cat,
            "rule_id": rid or "",
            "evidence": ev,
            "strength": strength,
            "breadcrumb_top": feat["breadcrumb_top"] or "",
            "breadcrumb_leaf": feat["breadcrumb_leaf"] or "",
            "genre": feat["genre"],
            "media_format": feat["media_format"] or "",
            "title": feat["title"] or "",
        })

    out_csv = os.path.join(args.out, "classification.csv")
    with io.open(out_csv, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["asin"])
        w.writeheader()
        w.writerows(rows)

    by_cat = collections.Counter(r["category"] for r in rows)
    by_rule = collections.Counter(r["rule_id"] or "(none)" for r in rows)
    by_strength = collections.Counter(r["strength"] for r in rows)
    summary = {
        "rules_version": cfg.get("version"),
        "rules_file": os.path.abspath(args.rules),
        "products": len(rows),
        "by_category": dict(by_cat),
        "by_rule": dict(by_rule),
        "by_strength": dict(by_strength),
    }
    with io.open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("rules_version =", cfg.get("version"))
    print("products      =", len(rows))
    for k, v in by_cat.most_common():
        print("  %-12s %4d" % (k, v))
    print("by_strength   =", dict(by_strength))
    print("out           =", out_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
