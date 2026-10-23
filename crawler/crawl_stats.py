#!/usr/bin/env python3
"""采集看板：状态分布、快照体积、进度与 ETA（只读本地数据）。"""
from __future__ import annotations

import collections
import csv
import io
import json
import os
import shutil
import sqlite3

LIST = r"E:\ETL_Amazon\data\crawler\product_list.csv"
DB = r"E:\ETL_Amazon\data\crawler\products.sqlite"
PAGES = r"E:\ETL_Amazon\data\raw\pages"
ATTEMPTS_PER_TARGET = 1.14   # 实测


def main() -> int:
    conn = sqlite3.connect(DB)
    rows = list(conn.execute("SELECT asin,status,attempts,snapshot_path FROM product_page"))
    total = 0
    with io.open(LIST, "r", encoding="utf-8", newline="") as fh:
        total = sum(1 for _ in fh) - 1

    by = collections.Counter(r[1] for r in rows)
    processed = len(rows)
    pending = total - sum(v for k, v in by.items() if k in ("success", "not_found"))

    files = 0
    size = 0
    for root, _dirs, fs in os.walk(PAGES):
        for f in fs:
            if f.endswith(".html.gz"):
                files += 1
                try:
                    size += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass

    free_gb = shutil.disk_usage(PAGES).free / (1024 ** 3)
    done = by.get("success", 0) + by.get("not_found", 0)

    print("================ 采集看板 ================")
    print("产品总数        : %d" % total)
    print("已处理          : %d (%.3f%%)" % (processed, 100.0 * processed / max(total, 1)))
    print("  终态(成功+不存在): %d" % done)
    print("待采集          : %d" % pending)
    print("状态分布        : %s" % dict(by))
    print("快照文件        : %d 个, %.1f MB" % (files, size / 1048576))
    print("磁盘可用        : %.1f GB" % free_gb)
    for delay in (3.0, 2.0, 1.0):
        sec = pending * ATTEMPTS_PER_TARGET * delay
        print("ETA @delay=%.0fs : %.1f 小时 (%.1f 天)" % (delay, sec / 3600, sec / 86400))
    print("==========================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
