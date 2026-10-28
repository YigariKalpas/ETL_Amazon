# -*- coding: utf-8 -*-
"""【KEY-41】标题相似度扫描：找出"疑似同一作品的不同版本"候选对。

精确标准化后只剩很少重复，说明同名多版本在样本里是稀疏事件。
这里用序列相似度 + 导演/片长一致性给出候选对，用于任务4 的阈值设计。
"""
from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import os
import sys

ROOT = r"E:\ETL_Amazon"
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
CLS = os.path.join(ROOT, "data", "staging", "classification", "classification.csv")
WORKS = ("movie", "movie_weak", "series", "concert", "boxset", "anime")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--cls", default=CLS)
    ap.add_argument("--min-ratio", type=float, default=0.86)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv)

    works = set()
    with io.open(args.cls, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["classification"] in WORKS:
                works.add(r["asin"])

    items = []
    for name in os.listdir(args.staging):
        if not name.endswith(".json"):
            continue
        with io.open(os.path.join(args.staging, name), encoding="utf-8") as fh:
            r = json.load(fh)
        if r.get("asin") in works and r.get("title_raw"):
            items.append(r)
    items.sort(key=lambda r: r["asin"])
    print("works = %d" % len(items))

    pairs = []
    for i in range(len(items)):
        a = items[i]
        ta = a["title_raw"].lower()
        for j in range(i + 1, len(items)):
            b = items[j]
            tb = b["title_raw"].lower()
            if abs(len(ta) - len(tb)) > 40:
                continue
            ratio = difflib.SequenceMatcher(None, ta, tb).ratio()
            if ratio < args.min_ratio:
                continue
            da = sorted(p["name_raw"] for p in (a.get("people_raw") or []) if p.get("role") == "director")
            db = sorted(p["name_raw"] for p in (b.get("people_raw") or []) if p.get("role") == "director")
            pairs.append((round(ratio, 3), a["asin"], b["asin"], a["title_raw"][:60],
                          b["title_raw"][:60], "|".join(da)[:30], "|".join(db)[:30],
                          a.get("runtime_minutes"), b.get("runtime_minutes")))
    pairs.sort(reverse=True)
    print("candidate_pairs(ratio>=%.2f) = %d" % (args.min_ratio, len(pairs)))
    same_dir = sum(1 for p in pairs if p[5] and p[5] == p[6])
    print("其中导演一致 = %d" % same_dir)
    for p in pairs[:args.limit]:
        print("  %.3f %s | %s | %s || %s | dir=%s / %s | rt=%s/%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
