# -*- coding: utf-8 -*-
"""【KEY-66】并行采集自检：分片互不重叠、目标都已入库、无重复处理。

只读分片 CSV 与状态库，不发请求。

用法：
  python crawler/verify_parallel.py
"""
from __future__ import annotations

import csv
import glob
import io
import os
import sqlite3
import sys

ROOT = r"E:\ETL_Amazon"
SHARDS = os.path.join(ROOT, "data", "crawler", "shards")
DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")


def main() -> int:
    paths = sorted(glob.glob(os.path.join(SHARDS, "shard_*.csv")))
    if not paths:
        print("没有分片文件")
        return 2
    seen = {}
    total = 0
    for p in paths:
        n = 0
        with io.open(p, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                a = r["asin"]
                n += 1
                seen.setdefault(a, []).append(os.path.basename(p))
        total += n
        print("%-14s %4d 个目标" % (os.path.basename(p), n))
    dup = {a: v for a, v in seen.items() if len(v) > 1}
    print("shards         = %d" % len(paths))
    print("targets_total  = %d" % total)
    print("distinct_asin  = %d" % len(seen))
    print("overlap_asin   = %d %s" % (len(dup), list(dup)[:5] if dup else ""))

    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    rows = conn.execute(
        "SELECT asin, status, attempts FROM product_page WHERE asin IN (%s)"
        % ",".join("?" * len(seen)), list(seen)).fetchall()
    conn.close()
    in_db = {r[0] for r in rows}
    missing = [a for a in seen if a not in in_db]
    print("in_db          = %d" % len(in_db))
    print("missing_in_db  = %d %s" % (len(missing), missing[:5]))
    stat = {}
    for r in rows:
        stat[r[1]] = stat.get(r[1], 0) + 1
    print("status_dist    = %s" % stat)
    ok = not dup and not missing
    print("result         = %s" % ("OK" if ok else "CHECK"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
