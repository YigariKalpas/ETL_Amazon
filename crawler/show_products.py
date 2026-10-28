# -*- coding: utf-8 -*-
"""【KEY-50】按 ASIN 打印暂存解析结果的要点，供人工比对（不联网）。

用法：
  python crawler/show_products.py B0006FFRD4 B0006FFRDE
"""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = r"E:\ETL_Amazon"
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")


def show(asin: str) -> None:
    path = os.path.join(STAGING, asin + ".json")
    if not os.path.isfile(path):
        print("%s  -> 无暂存文件" % asin)
        return
    with io.open(path, encoding="utf-8") as fh:
        r = json.load(fh)
    detail = r.get("detail") or {}
    print("=" * 70)
    print("asin        : %s" % asin)
    print("title       : %s" % r.get("title_raw"))
    print("breadcrumb  : %s" % " > ".join(r.get("breadcrumb_categories") or [])[:90])
    print("format      : %s" % (r.get("media_format") or ""))
    print("studio      : %s" % (r.get("studio") or ""))
    print("runtime     : %s" % (r.get("runtime_minutes") or ""))
    print("language    : %s" % (r.get("language") or ""))
    print("mpaa        : %s" % (r.get("mpaa_rating") or ""))
    print("actors      : %s" % (r.get("actors_raw") or "")[:100])
    print("director    : %s" % (detail.get("director") or ""))
    print("release(prod): %s" % (r.get("product_release_date_raw") or ""))
    print("snapshot    : %s" % (r.get("snapshot_path") or ""))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python crawler/show_products.py ASIN [ASIN ...]")
        sys.exit(2)
    for a in sys.argv[1:]:
        show(a)
