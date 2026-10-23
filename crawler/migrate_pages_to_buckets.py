#!/usr/bin/env python3
"""把扁平存放的页面快照迁移到分桶目录，并更新状态库路径。

目录：data/raw/pages/<sha1(ASIN)[:2]>/<ASIN>.html.gz
幂等：已迁移的会跳过。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys

PAGES = r"E:\ETL_Amazon\data\raw\pages"
DB = r"E:\ETL_Amazon\data\crawler\products.sqlite"


def bucket(asin: str) -> str:
    return hashlib.sha1(asin.encode("utf-8")).hexdigest()[:2]


def main() -> int:
    conn = sqlite3.connect(DB)
    rows = list(conn.execute("SELECT asin, snapshot_path FROM product_page"))
    moved = already = missing = 0
    updates = []

    for asin, old in rows:
        target = os.path.join(PAGES, bucket(asin), asin + ".html.gz")
        if os.path.isfile(target):
            already += 1
            if old != os.path.abspath(target):
                updates.append((os.path.abspath(target), asin))
            continue
        if old and os.path.isfile(old) and os.path.abspath(old) != os.path.abspath(target):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(old, target)
            moved += 1
            updates.append((os.path.abspath(target), asin))
        else:
            missing += 1

    for path, asin in updates:
        conn.execute("UPDATE product_page SET snapshot_path=? WHERE asin=?", (path, asin))
    conn.commit()

    # 扫描仍留在根目录的孤立文件
    stray = [f for f in os.listdir(PAGES)
             if os.path.isfile(os.path.join(PAGES, f)) and f.endswith(".html.gz")]
    for f in stray:
        asin = f[:-len(".html.gz")]
        tgt = os.path.join(PAGES, bucket(asin), f)
        os.makedirs(os.path.dirname(tgt), exist_ok=True)
        shutil.move(os.path.join(PAGES, f), tgt)
        moved += 1

    buckets = [d for d in os.listdir(PAGES) if os.path.isdir(os.path.join(PAGES, d))]
    print("rows_in_db   =", len(rows))
    print("moved        =", moved)
    print("already_ok   =", already)
    print("missing_file =", missing)
    print("stray_moved  =", len(stray))
    print("buckets_used =", len(buckets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
