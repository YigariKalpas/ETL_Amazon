#!/usr/bin/env python3
"""用修正后的判定逻辑重扫已保存快照，更正历史状态（纯本地，不联网）。

用途：ROBOT_MARKERS 曾漏判 "validateCaptcha"，导致验证码页被记为 parse_failed。
本脚本重读快照、重算状态并更新状态库，快照本身不改动。
"""
from __future__ import annotations

import gzip
import os
import sqlite3
import sys

sys.path.insert(0, r"E:\ETL_Amazon\crawler")
import crawl_product_pages as C  # noqa: E402

DB = r"E:\ETL_Amazon\data\crawler\products.sqlite"


def main() -> int:
    conn = sqlite3.connect(DB)
    rows = list(conn.execute(
        "SELECT asin,status,http_status,COALESCE(note,''),snapshot_path FROM product_page"))
    changed = 0
    skipped = 0
    detail = []

    for asin, status, http, note, path in rows:
        if not path or not os.path.isfile(path):
            skipped += 1
            continue
        with gzip.open(path, "rb") as fh:
            body = fh.read()
        text = body.decode("utf-8", "replace")
        new_status, new_note = C.classify(http if http is not None else 200, text, asin)
        if new_status != status or new_note != note:
            conn.execute(
                "UPDATE product_page SET status=?, note=? WHERE asin=?",
                (new_status, new_note, asin))
            changed += 1
            if len(detail) < 12:
                detail.append((asin, status, new_status, new_note[:60]))

    conn.commit()
    print("rows      =", len(rows))
    print("changed   =", changed)
    print("skipped   =", skipped, "(无快照)")
    print("\n前 12 条变更：")
    for a, old, new, n in detail:
        print("  %-11s %-14s -> %-16s %s" % (a, old, new, n))
    print("\n更正后状态分布：")
    for r in conn.execute("SELECT status, COUNT(*) FROM product_page GROUP BY status"):
        print("  %-16s %d" % r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
