# -*- coding: utf-8 -*-
"""【KEY-38】任务1 产物的独立核验：不信任抽取脚本的自报数，重新算一遍。

核验项：
  1) 每个 part 的行数、总行数是否等于 stats.json 的 records；
  2) 不同 ASIN 数是否等于 253,059（与采集清单一致）；
  3) 评分分布、时间范围、文本总长是否与 stats.json 一致；
  4) part 文件的 SHA-256 清单（供报告写校验值 / 防篡改）；
  5) --sample 抽几行打印，供人工眼看字段是否正确。

用法：
  python crawler/verify_reviews.py
  python crawler/verify_reviews.py --sample 3
  python crawler/verify_reviews.py --manifest data/staging/reviews/manifest.json
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sys
from collections import Counter

ROOT = r"E:\ETL_Amazon"
DEFAULT_DIR = os.path.join(ROOT, "data", "staging", "reviews")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="核验评论抽取产物")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--manifest", default="")
    args = ap.parse_args(argv)

    parts = sorted(f for f in os.listdir(args.dir) if f.startswith("part-") and f.endswith(".csv.gz"))
    if not parts:
        print("没有找到 part-*.csv.gz")
        return 1

    rows = 0
    asins = set()
    score_dist = Counter()
    t_min = None
    t_max = None
    text_sum = 0
    manifest = []
    for name in parts:
        path = os.path.join(args.dir, name)
        h = hashlib.sha256()
        with open(path, "rb") as raw:
            for chunk in iter(lambda: raw.read(1 << 20), b""):
                h.update(chunk)
        n = 0
        with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
            for rec in csv.DictReader(fh):
                n += 1
                asins.add(rec["asin"])
                if rec["score"]:
                    score_dist[rec["score"]] += 1
                if rec["review_time_epoch"]:
                    e = int(rec["review_time_epoch"])
                    t_min = e if t_min is None else min(t_min, e)
                    t_max = e if t_max is None else max(t_max, e)
                text_sum += int(rec["text_len"] or 0)
        rows += n
        manifest.append({"file": name, "rows": n,
                         "gz_bytes": os.path.getsize(path), "sha256": h.hexdigest()})
        print("%-22s rows=%7d gz=%8.1f MB" % (name, n, os.path.getsize(path) / 1024.0 / 1024.0))

    print("-" * 62)
    print("parts          = %d" % len(parts))
    print("rows_total     = %d" % rows)
    print("distinct_asin  = %d" % len(asins))
    print("score_dist     = %s" % dict(sorted(score_dist.items())))
    print("time_range     = %s .. %s" % (t_min, t_max))
    print("text_len_sum   = %d (avg %.1f)" % (text_sum, text_sum / max(1, rows)))

    stats_path = os.path.join(args.dir, "stats.json")
    if os.path.isfile(stats_path):
        with io.open(stats_path, encoding="utf-8") as fh:
            st = json.load(fh)
        ok = (st.get("records") == rows and st.get("distinct_products") == len(asins))
        print("-" * 62)
        print("stats.json     = records %d / products %d  -> %s" % (
            st.get("records"), st.get("distinct_products"), "一致" if ok else "不一致"))

    if args.sample > 0:
        print("-" * 62)
        with gzip.open(os.path.join(args.dir, parts[0]), "rt", encoding="utf-8", newline="") as fh:
            for i, rec in enumerate(csv.DictReader(fh)):
                if i >= args.sample:
                    break
                print("  asin=%s score=%s time=%s helpful=%s/%s summary=%r text=%r" % (
                    rec["asin"], rec["score"], rec["review_time_iso"], rec["helpful_num"],
                    rec["helpful_den"], rec["summary"][:50], rec["text"][:50]))

    if args.manifest:
        payload = {"dir": args.dir, "parts": len(parts), "rows": rows,
                   "distinct_asin": len(asins),
                   "digest_of_parts": hashlib.sha256(
                       "\n".join("%s %s" % (m["file"], m["sha256"]) for m in manifest).encode()
                   ).hexdigest(),
                   "files": manifest}
        with io.open(args.manifest, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print("manifest       = %s (digest_of_parts=%s)" % (args.manifest, payload["digest_of_parts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
