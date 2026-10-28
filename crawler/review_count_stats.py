# -*- coding: utf-8 -*-
"""【KEY-39】按产品统计评论条数分布（任务1/任务8 的输入画像）。"""
from __future__ import annotations

import argparse
import csv
import io
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=r"E:\ETL_Amazon\data\staging\reviews\product_review_counts.csv")
    args = ap.parse_args(argv)
    counts = []
    with io.open(args.src, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            counts.append(int(r["review_count"]))
    counts.sort()
    n = len(counts)
    total = sum(counts)

    def pct(p):
        return counts[min(n - 1, int(n * p))]

    print("products          = %d" % n)
    print("reviews_total     = %d" % total)
    print("mean              = %.2f" % (total / max(1, n)))
    print("min/max           = %d / %d" % (counts[0], counts[-1]))
    for p in (0.25, 0.5, 0.75, 0.9, 0.95, 0.99):
        print("p%-4s            = %d" % (int(p * 100), pct(p)))
    for t in (1, 5, 10, 50, 100, 500):
        c = sum(1 for x in counts if x >= t)
        print(">=%-4d reviews    = %d 个产品 (%.1f%%)" % (t, c, 100.0 * c / max(1, n)))
    # 累计覆盖：前多少产品覆盖 50%/80%/90% 评论
    acc = 0
    marks = {}
    for i, x in enumerate(reversed(counts), start=1):
        acc += x
        for q in (0.5, 0.8, 0.9):
            if q not in marks and acc >= total * q:
                marks[q] = i
    for q in (0.5, 0.8, 0.9):
        print("top %-6d products cover %d%% of reviews" % (marks.get(q, n), int(q * 100)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
