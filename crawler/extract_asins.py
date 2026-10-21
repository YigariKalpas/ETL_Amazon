#!/usr/bin/env python3
"""从 Amazon 电影评论原始文件流式提取「去重后的 ASIN」（作业任务2 的前置步骤）。

【KEY-1】只用流式逐行扫描，绝不把 9.3 GB 原文读进内存或上下文。
【KEY-2】只认字段前缀 product/productId:，每行一个；ASIN 形态校验 ^[A-Z0-9]{10}$。
【KEY-3】输出 CSV（asin, source_url, first_line），source_url 即 amazon.com/dp/<ASIN>。
【KEY-4】--max-lines 用于有界抽样（冒烟测试/小样本），不作全量结论。

用法:
    python extract_asins.py                        # 全量
    python extract_asins.py --max-lines 200000     # 有界抽样
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time

DEFAULT_SRC = r"E:\ETL_Amazon\movies.txt\movies.txt"
DEFAULT_OUT = r"E:\ETL_Amazon\data\crawler\product_list.csv"

# 【KEY-2】ASIN 校验：Amazon ASIN 为 10 位大写字母/数字
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
# 【KEY-2】字段前缀（注意与 review/productId 区分——本数据集用 product/productId）
PID_PREFIX = "product/productId:"


def build_url(asin: str) -> str:
    """【KEY-3】由 ASIN 组成产品页 URL；保留原始 URL 以便追溯。"""
    return "https://www.amazon.com/dp/" + asin


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="流式提取去重 ASIN")
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--max-lines", type=int, default=0, help="0 表示全量；>0 时仅扫描前 N 行")
    ap.add_argument("--encoding", default="utf-8", help="源文件编码（异常字符用 errors=replace）")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.src):
        sys.stderr.write("源文件不存在: %s\n" % args.src)
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    seen: dict[str, int] = {}          # asin -> 首次出现的行号
    lines_read = 0
    product_lines = 0
    invalid = 0
    t0 = time.time()

    # 【KEY-1】流式读取：缓冲区逐行，内存恒定
    with open(args.src, "r", encoding=args.encoding, errors="replace", buffering=1 << 20) as fh:
        for lineno, line in enumerate(fh, start=1):
            lines_read += 1
            if args.max_lines and lines_read > args.max_lines:
                break
            if not line.startswith(PID_PREFIX):
                continue
            product_lines += 1
            asin = line[len(PID_PREFIX):].strip()
            if not ASIN_RE.match(asin):
                invalid += 1
                continue
            if asin not in seen:
                seen[asin] = lineno

    # 【KEY-3】落盘为采集清单的输入（按首次出现顺序）
    with open(args.out, "w", encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        w.writerow(["asin", "source_url", "first_line"])
        for asin, ln in seen.items():
            w.writerow([asin, build_url(asin), ln])

    dt = time.time() - t0
    print("lines_read      = %d" % lines_read)
    print("productId_lines = %d" % product_lines)
    print("distinct_asin   = %d" % len(seen))
    print("invalid_asin    = %d" % invalid)
    print("elapsed         = %.1f s" % dt)
    print("output          = %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
