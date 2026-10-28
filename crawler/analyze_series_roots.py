# -*- coding: utf-8 -*-
"""【KEY-47】剧集根标题聚合分析：有多少 series 根下挂了多个卷/季。"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from collections import defaultdict

ROOT = r"E:\ETL_Amazon"
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
CLS = os.path.join(ROOT, "data", "staging", "classification", "classification.csv")

SERIES_RE = re.compile(r"(?i)\b(season\s*\d+|vol\.?\s*\d+|volume\s*\d+|episode\s*\d+|ep\.?\s*\d+)\b")
PUNCT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff ]+")


def norm(t):
    s = PUNCT_RE.sub(" ", (t or "").lower())
    return " ".join(s.split())


def root_of(title):
    s = SERIES_RE.sub(" ", title or "")
    s = re.split(r"[:\-]", s)[0]
    return norm(s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--cls", default=CLS)
    args = ap.parse_args(argv)

    series = []
    with io.open(args.cls, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["classification"] == "series":
                series.append(r)
    titles = {}
    for r in series:
        p = os.path.join(args.staging, r["asin"] + ".json")
        if os.path.isfile(p):
            with io.open(p, encoding="utf-8") as fh:
                titles[r["asin"]] = json.load(fh).get("title_raw") or ""
    groups = defaultdict(list)
    for a, t in titles.items():
        groups[root_of(t)].append((a, t))
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    print("series_products = %d" % len(titles))
    print("roots           = %d" % len(groups))
    print("roots_with_multi= %d" % len(multi))
    print("products_in_multi_roots = %d" % sum(len(v) for v in multi.values()))
    for k, v in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:12]:
        print("  [%s] %d 个:" % (k[:40], len(v)))
        for a, t in v[:3]:
            print("      %s %s" % (a, t[:70]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
