# -*- coding: utf-8 -*-
"""【KEY-34】探明评论原始文件的字段结构，只输出聚合统计，不把原文带进上下文。

做法：流式扫前 N 行，按空行切记录，统计「出现过的字段名 / 次数 / 值长度分布」，
并检查记录块是否规整（字段数是否恒定、有无缺 productId 的块）。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

SRC = r"E:\ETL_Amazon\movies.txt\movies.txt"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--max-lines", type=int, default=200000)
    ap.add_argument("--max-records", type=int, default=300)
    args = ap.parse_args(argv)

    keys = Counter()
    val_len = defaultdict(list)
    blocks = 0
    cur = {}
    lines = 0
    bad_key = 0

    with open(args.src, "r", encoding="utf-8", errors="replace", buffering=1 << 20) as fh:
        for line in fh:
            lines += 1
            if lines > args.max_lines:
                break
            s = line.rstrip("\n\r")
            if not s.strip():
                if cur:
                    blocks += 1
                    if blocks >= args.max_records:
                        break
                    cur = {}
                continue
            if ":" not in s:
                bad_key += 1
                continue
            k, v = s.split(":", 1)
            k = k.strip()
            v = v.strip()
            keys[k] += 1
            if len(val_len[k]) < 200000:
                val_len[k].append(len(v))
            cur[k] = v

    print("lines_scanned = %d" % lines)
    print("records       = %d" % blocks)
    print("lines_without_colon = %d" % bad_key)
    print()
    print("%-28s %8s %8s %8s %8s %8s" % ("field", "count", "min_len", "avg_len", "max_len", "empty"))
    for k, c in keys.most_common():
        lens = val_len[k]
        empt = sum(1 for x in lens if x == 0)
        print("%-28s %8d %8d %8.1f %8d %8d" % (
            k, c, min(lens), sum(lens) / len(lens), max(lens), empt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
