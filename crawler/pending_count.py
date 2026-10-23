#!/usr/bin/env python3
"""输出待采集数量与磁盘可用空间（JSON），供守护/看板脚本解析。"""
from __future__ import annotations

import csv, io, json, shutil, sqlite3

LIST = r"E:\ETL_Amazon\data\crawler\product_list.csv"
DB = r"E:\ETL_Amazon\data\crawler\products.sqlite"
PAGES = r"E:\ETL_Amazon\data\raw\pages"
TERMINAL = ("success", "not_found")
MAX_ATTEMPTS = 3


def main() -> int:
    conn = sqlite3.connect(DB)
    state = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT asin, status, attempts, COALESCE(note,'') FROM product_page")}
    pending = 0
    total = 0
    with io.open(LIST, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            a = (row.get("asin") or "").strip()
            if not a:
                continue
            total += 1
            if a not in state:
                pending += 1
                continue
            status, attempts, note = state[a]
            if status in TERMINAL or note == "robots_disallow":
                continue
            if attempts < MAX_ATTEMPTS:
                pending += 1
    try:
        free_gb = round(shutil.disk_usage(PAGES).free / (1024 ** 3), 1)
    except Exception:
        free_gb = None
    print(json.dumps({"pending": pending, "total": total, "free_gb": free_gb,
                      "processed": len(state)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
