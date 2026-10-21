#!/usr/bin/env python3
"""全量校验 ASIN 清单并生成产品 URL 列表。

输入: data/crawler/product_list.csv  (asin, source_url, first_line)
输出:
  data/crawler/product_urls.txt      每行一个 https://www.amazon.com/dp/<ASIN>
  data/crawler/product_urls.meta.json 校验统计 + sha256

校验项：
  1) ASIN 形态 ^[A-Z0-9]{10}$
  2) URL 与 ASIN 一致
  3) 无重复 ASIN
  4) 统计重复/非法记录
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

SRC = r"E:\ETL_Amazon\data\crawler\product_list.csv"
OUT_URLS = r"E:\ETL_Amazon\data\crawler\product_urls.txt"
OUT_META = r"E:\ETL_Amazon\data\crawler\product_urls.meta.json"

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
URL_TMPL = "https://www.amazon.com/dp/"


def main() -> int:
    if not os.path.isfile(SRC):
        sys.stderr.write("缺少清单: %s\n" % SRC)
        return 2

    rows = 0
    ok = 0
    bad_asin = 0
    bad_url = 0
    dup = 0
    seen = set()
    urls: list[str] = []

    with io.open(SRC, "r", encoding="utf-8", newline="") as fh:
        # 手工解析，避免 csv 模块对大小写/空白的意外
        header = fh.readline()
        for line in fh:
            line = line.rstrip("\r\n")
            if not line:
                continue
            rows += 1
            parts = line.split(",")
            if len(parts) < 2:
                bad_asin += 1
                continue
            asin, url = parts[0].strip(), parts[1].strip()
            if not ASIN_RE.match(asin):
                bad_asin += 1
                continue
            if asin in seen:
                dup += 1
                continue
            if url != URL_TMPL + asin:
                bad_url += 1
                # 仍以 ASIN 重建，保证输出可用
            seen.add(asin)
            urls.append(URL_TMPL + asin)
            ok += 1

    os.makedirs(os.path.dirname(OUT_URLS), exist_ok=True)
    with io.open(OUT_URLS, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(urls) + "\n")

    blob = ("\n".join(urls) + "\n").encode("utf-8")
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_csv": SRC,
        "output": OUT_URLS,
        "rows_read": rows,
        "valid_unique": ok,
        "invalid_asin": bad_asin,
        "url_mismatch": bad_url,
        "duplicate_asin": dup,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "bytes": len(blob),
        "url_template": URL_TMPL + "<ASIN>",
    }
    with io.open(OUT_META, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("rows_read      = %d" % rows)
    print("valid_unique   = %d" % ok)
    print("invalid_asin   = %d" % bad_asin)
    print("url_mismatch   = %d" % bad_url)
    print("duplicate_asin = %d" % dup)
    print("out_bytes      = %d" % len(blob))
    print("out_sha256     = %s" % meta["sha256"])
    print("out            = %s" % OUT_URLS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
