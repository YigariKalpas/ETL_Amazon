#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【KEY-35】任务1：从 8.69 GB 原始评论文件流式抽取完整字段。

原始格式（已由 probe_review_format.py 实测确认，8 个字段 + 空行分隔）：
    product/productId: B000...
    review/userId: A...
    review/profileName: ...
    review/helpfulness: 2/3
    review/score: 4.0
    review/time: 1234567890
    review/summary: ...
    review/text: ...

产出：
  data/staging/reviews/part-00001.csv.gz ...   （每 chunk 一个 gzip CSV，带表头）
  data/staging/reviews/product_review_counts.csv（每个产品的评论条数）
  data/staging/reviews/stats.json              （总量/异常/分布，供报告引用）

设计要点：
  【KEY-1】流式逐行，内存恒定；绝不整文件读入。
  【KEY-2】块解析：遇到空行才提交一条记录，字段缺失只计数、不抛异常。
  【KEY-3】保留 review_time 的原始 epoch 与 ISO8601 两列，便于下游按时间分区。
  【KEY-4】--max-records 用于有界冒烟，不作全量结论。

用法：
  python crawler/extract_reviews.py --max-records 5000      # 冒烟
  python crawler/extract_reviews.py                          # 全量
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone

DEFAULT_SRC = r"E:\ETL_Amazon\movies.txt\movies.txt"
DEFAULT_OUT = r"E:\ETL_Amazon\data\staging\reviews"

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
HELP_RE = re.compile(r"^(\d+)\s*/\s*(\d+)$")

FIELDS = ["product_id", "user_id", "profile_name", "helpful_num", "helpful_den",
          "score", "review_time_epoch", "review_time_iso", "summary", "text",
          "summary_len", "text_len", "record_index"]
HEADER = ["asin", "user_id", "profile_name", "helpful_num", "helpful_den",
          "score", "review_time_epoch", "review_time_iso", "summary", "text",
          "summary_len", "text_len", "record_index"]

KEYMAP = {
    "product/productId": "product_id",
    "review/userId": "user_id",
    "review/profileName": "profile_name",
    "review/helpfulness": "helpfulness",
    "review/score": "score",
    "review/time": "time",
    "review/summary": "summary",
    "review/text": "text",
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="流式抽取完整评论字段")
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--chunk-records", type=int, default=200000)
    ap.add_argument("--max-records", type=int, default=0, help="0=全量")
    ap.add_argument("--encoding", default="utf-8")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.src):
        sys.stderr.write("源文件不存在: %s\n" % args.src)
        return 2
    os.makedirs(args.out, exist_ok=True)

    stats = {
        "source": args.src,
        "encoding": args.encoding,
        "chunk_records": args.chunk_records,
        "records": 0,
        "blocks_rejected": 0,
        "missing_field": Counter(),
        "invalid_asin": 0,
        "invalid_score": 0,
        "invalid_time": 0,
        "invalid_helpfulness": 0,
        "score_dist": Counter(),
        "empty_summary": 0,
        "empty_text": 0,
        "text_len_sum": 0,
        "text_len_max": 0,
        "time_min": None,
        "time_max": None,
        "parts": 0,
        "per_product": Counter(),
    }

    rejects_path = os.path.join(args.out, "rejects.csv")
    rejects_fh = io.open(rejects_path, "w", encoding="utf-8", newline="")
    rejects_writer = csv.writer(rejects_fh)
    rejects_writer.writerow(["record_index", "reasons", "product_id", "user_id", "score_raw",
                             "time_raw", "helpfulness_raw", "summary_len", "text_len",
                             "text_head"])

    t0 = time.time()
    cur: dict[str, str] = {}
    part_no = 0
    writer = None
    fh_out = None
    txt_out = None
    in_part = 0

    def open_part():
        nonlocal part_no, writer, fh_out, txt_out, in_part
        part_no += 1
        path = os.path.join(args.out, "part-%05d.csv.gz" % part_no)
        fh_out = gzip.open(path, "wb", compresslevel=6)
        txt_out = io.TextIOWrapper(fh_out, encoding="utf-8", newline="")
        writer = csv.writer(txt_out)
        writer.writerow(HEADER)
        in_part = 0
        stats["parts"] += 1

    def close_part():
        nonlocal writer, fh_out, txt_out, in_part
        if txt_out is not None:
            txt_out.flush()
            txt_out.close()   # 关闭 TextIOWrapper 会一并关闭 gzip 句柄
            writer = None
            fh_out = None
            txt_out = None
        in_part = 0            # 【KEY-36】关闭后计数归零，下一条记录才会重新开新分片

    def reject(idx: int, rec: dict[str, str], reasons: list[str]) -> None:
        """【KEY-37】异常记录不静默丢弃：落 rejects.csv，便于人工复核与血缘追溯。"""
        rejects_writer.writerow([
            idx, ";".join(reasons),
            rec.get("product_id", ""), rec.get("user_id", ""),
            rec.get("score", ""), rec.get("time", ""), rec.get("helpfulness", ""),
            len(rec.get("summary", "") or ""), len(rec.get("text", "") or ""),
            (rec.get("text", "") or "")[:200].replace("\n", " ")])

    def commit(rec: dict[str, str], idx: int) -> bool:
        nonlocal in_part
        pid = (rec.get("product_id") or "").strip()
        if not ASIN_RE.match(pid):
            stats["invalid_asin"] += 1
            reject(idx, rec, ["invalid_asin"])
            return False
        if in_part == 0:
            open_part()

        score_raw = (rec.get("score") or "").strip()
        try:
            score = float(score_raw)
            if not (1.0 <= score <= 5.0):
                raise ValueError
        except ValueError:
            stats["invalid_score"] += 1
            score = None

        t_raw = (rec.get("time") or "").strip()
        iso = None
        epoch = None
        try:
            epoch = int(t_raw)
            iso = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OSError, OverflowError):
            stats["invalid_time"] += 1
            epoch = None

        h_raw = (rec.get("helpfulness") or "").strip()
        hn = hd = None
        m = HELP_RE.match(h_raw)
        if m:
            hn, hd = int(m.group(1)), int(m.group(2))
        else:
            stats["invalid_helpfulness"] += 1

        summary = rec.get("summary") or ""
        text = rec.get("text") or ""
        if not summary:
            stats["empty_summary"] += 1
        if not text:
            stats["empty_text"] += 1
        stats["text_len_sum"] += len(text)
        stats["text_len_max"] = max(stats["text_len_max"], len(text))

        if score is not None:
            stats["score_dist"][str(score)] += 1
        if epoch is not None:
            stats["time_min"] = epoch if stats["time_min"] is None else min(stats["time_min"], epoch)
            stats["time_max"] = epoch if stats["time_max"] is None else max(stats["time_max"], epoch)

        reasons = []
        if score is None:
            reasons.append("invalid_score")
        if epoch is None:
            reasons.append("invalid_time")
        if hn is None:
            reasons.append("invalid_helpfulness")
        if not summary:
            reasons.append("empty_summary")
        if not text:
            reasons.append("empty_text")
        if reasons:
            reject(idx, rec, reasons)

        writer.writerow([pid, rec.get("user_id", ""), rec.get("profile_name", ""),
                         "" if hn is None else hn, "" if hd is None else hd,
                         "" if score is None else score, "" if epoch is None else epoch,
                         iso or "", summary, text, len(summary), len(text), idx])
        in_part += 1
        stats["records"] += 1
        stats["per_product"][pid] += 1

        if in_part >= args.chunk_records:
            close_part()
        return True

    idx = 0
    lines_read = 0
    with open(args.src, "r", encoding=args.encoding, errors="replace", buffering=1 << 20) as fh:
        for line in fh:
            lines_read += 1
            s = line.rstrip("\n\r")
            if not s.strip():
                if cur:
                    idx += 1
                    if not commit(cur, idx):
                        stats["blocks_rejected"] += 1
                    cur = {}
                    if args.max_records and idx >= args.max_records:
                        break
                continue
            if ":" not in s:
                continue
            k, v = s.split(":", 1)
            field = KEYMAP.get(k.strip())
            if field:
                cur[field] = v.strip()
                if field in ("summary", "text"):
                    cur[field] = v.lstrip()   # 文本保留原样，只去掉键后的一个空格
    if cur:
        idx += 1
        if not commit(cur, idx):
            stats["blocks_rejected"] += 1
    close_part()

    rejects_fh.close()

    # 每个产品的评论数
    counts_path = os.path.join(args.out, "product_review_counts.csv")
    with io.open(counts_path, "w", encoding="utf-8", newline="") as cf:
        w = csv.writer(cf)
        w.writerow(["asin", "review_count"])
        for pid, n in stats["per_product"].most_common():
            w.writerow([pid, n])

    elapsed = time.time() - t0
    out = {
        "source": stats["source"],
        "elapsed_s": round(elapsed, 1),
        "lines_read": lines_read,
        "records": stats["records"],
        "parts": stats["parts"],
        "blocks_rejected": stats["blocks_rejected"],
        "invalid_asin": stats["invalid_asin"],
        "invalid_score": stats["invalid_score"],
        "invalid_time": stats["invalid_time"],
        "invalid_helpfulness": stats["invalid_helpfulness"],
        "empty_summary": stats["empty_summary"],
        "empty_text": stats["empty_text"],
        "avg_text_len": round(stats["text_len_sum"] / max(1, stats["records"]), 1),
        "max_text_len": stats["text_len_max"],
        "score_dist": dict(sorted(stats["score_dist"].items())),
        "time_min": stats["time_min"],
        "time_max": stats["time_max"],
        "time_min_iso": datetime.fromtimestamp(stats["time_min"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if stats["time_min"] else None,
        "time_max_iso": datetime.fromtimestamp(stats["time_max"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if stats["time_max"] else None,
        "distinct_products": len(stats["per_product"]),
        "product_counts_csv": counts_path,
        "rejects_csv": rejects_path,
        "top_products": stats["per_product"].most_common(10),
    }
    with io.open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    for k in ("records", "parts", "invalid_asin", "invalid_score", "invalid_time",
              "invalid_helpfulness", "empty_summary", "empty_text", "distinct_products"):
        print("%-20s = %s" % (k, out[k]))
    print("%-20s = %.1f s" % ("elapsed", elapsed))
    print("%-20s = %s" % ("output", args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
