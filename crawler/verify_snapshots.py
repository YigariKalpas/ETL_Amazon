# -*- coding: utf-8 -*-
"""【KEY-31】快照完整性核验 + 采集清单生成。

核验（不联网，只读本地文件）：
  1) 状态库里每条记录的快照文件是否真的存在；
  2) gzip 是否可解（损坏检测）；
  3) 解压后内容的 SHA-256 是否等于入库时记录的 content_sha256；
  4) 解压后字节数是否等于 content_length；
  5) 路径是否符合分桶规则 sha1(ASIN)[:2]；
  6) 磁盘上是否有状态库没登记的多余文件（孤儿）。

同时可导出采集清单 JSON（作业 §十一"完整数据较大时提交校验值、保存位置与恢复方法"）。

用法：
  python crawler/verify_snapshots.py                       # 只核验，打印汇总
  python crawler/verify_snapshots.py --sample 5            # 附带抽样打印 ASIN/title
  python crawler/verify_snapshots.py --manifest data/crawler/collect_manifest.json
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = r"E:\ETL_Amazon"
DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
PAGES = os.path.join(ROOT, "data", "raw", "pages")


def bucket_of(asin: str) -> str:
    return hashlib.sha1(asin.encode("utf-8")).hexdigest()[:2]


def verify_one(db: str, pages_dir: str, asin: str) -> int:
    """单条核验：把库里的登记值与磁盘上的实际值并排打印，便于人工比对。"""
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT asin, status, snapshot_path, content_sha256, content_length, fetched_at "
        "FROM product_page WHERE asin=?", (asin,)).fetchone()
    if not row:
        print("状态库里没有 %s" % asin)
        return 1
    _asin, status, snap, sha, length, fetched = row
    print("asin            = %s" % asin)
    print("status          = %s" % status)
    print("fetched_at      = %s" % fetched)
    print("db_content_sha256 = %s" % sha)
    print("db_content_length = %s" % length)
    print("snapshot_path   = %s" % snap)
    expect = os.path.join(pages_dir, bucket_of(asin) + os.sep + asin + ".html.gz")
    print("expected_path   = %s  (一致=%s)" % (expect, os.path.abspath(expect) == os.path.abspath(snap or "")))
    if not snap or not os.path.isfile(snap):
        print("文件不存在")
        return 1
    gz_bytes = os.path.getsize(snap)
    with gzip.open(snap, "rb") as fh:
        body = fh.read()
    got = hashlib.sha256(body).hexdigest()
    print("disk_gz_bytes   = %d" % gz_bytes)
    print("disk_raw_bytes  = %d  (与库一致=%s)" % (len(body), length is None or len(body) == length))
    print("disk_sha256     = %s  (与库一致=%s)" % (got, got == sha))
    i = body.find(b"<title")
    if i >= 0:
        j = body.find(b"</title>", i)
        print("title           = %s" % " ".join(body[i:j + 8].decode("utf-8", "replace").split())[:160])
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="快照完整性核验")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--pages-dir", default=PAGES)
    ap.add_argument("--sample", type=int, default=0, help="抽样打印 N 条（人工眼看）")
    ap.add_argument("--manifest", default="", help="导出清单 JSON 的路径")
    ap.add_argument("--limit-problems", type=int, default=20)
    ap.add_argument("--asin", default="", help="只核验这一个 ASIN，打印全部细节")
    args = ap.parse_args(argv)

    if args.asin:
        return verify_one(args.db, args.pages_dir, args.asin)

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT asin, status, snapshot_path, content_sha256, content_length "
        "FROM product_page ORDER BY asin").fetchall()

    problems: list[tuple[str, str]] = []
    entries = []
    gz_total = raw_total = 0
    ok = 0
    expected_no_snap = 0
    for asin, status, snap, sha, length in rows:
        if not snap:
            # 【KEY-100】timeout 属于"没有响应体"，本来就不会有快照；不算完整性问题
            if status in ("timeout", "not_collected"):
                expected_no_snap += 1
            else:
                problems.append((asin, "no_snapshot_path"))
            continue
        if not os.path.isfile(snap):
            problems.append((asin, "file_missing"))
            continue
        expect = os.path.join(args.pages_dir, bucket_of(asin), asin + ".html.gz")
        if os.path.abspath(snap) != os.path.abspath(expect):
            problems.append((asin, "wrong_bucket"))
        gz_bytes = os.path.getsize(snap)
        gz_total += gz_bytes
        try:
            with gzip.open(snap, "rb") as fh:
                body = fh.read()
        except OSError as exc:
            problems.append((asin, "gzip_broken:%s" % exc))
            continue
        raw_total += len(body)
        got = hashlib.sha256(body).hexdigest()
        if sha and got != sha:
            problems.append((asin, "sha256_mismatch"))
            continue
        if length is not None and len(body) != length:
            problems.append((asin, "length_mismatch:%d!=%d" % (len(body), length)))
            continue
        ok += 1
        entries.append({"asin": asin, "status": status, "path": os.path.relpath(snap, ROOT),
                        "gz_bytes": gz_bytes, "raw_bytes": len(body), "sha256": got})

    # 孤儿文件：磁盘上有、状态库没登记
    known = {e["path"].replace(os.sep, "/") for e in entries}
    orphans = []
    for dirpath, _dirnames, filenames in os.walk(args.pages_dir):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            if rel not in known:
                orphans.append(rel)

    print("db_rows        = %d" % len(rows))
    print("verified_ok    = %d" % ok)
    print("problems       = %d" % len(problems))
    if expected_no_snap:
        print("expected_no_snap = %d（timeout/未采集，无响应体属正常）" % expected_no_snap)
    for asin, why in problems[:args.limit_problems]:
        print("   %s  %s" % (asin, why))
    print("orphan_files   = %d" % len(orphans))
    for rel in orphans[:args.limit_problems]:
        print("   %s" % rel)
    print("gz_total       = %d bytes (%.2f MB)" % (gz_total, gz_total / 1024.0 / 1024.0))
    print("raw_total      = %d bytes (%.2f MB)" % (raw_total, raw_total / 1024.0 / 1024.0))
    if raw_total:
        print("compress_ratio = %.2f" % (float(gz_total) / raw_total))

    if args.sample > 0:
        print("\n[抽样] asin / 状态 / 页面标题片段")
        step = max(1, len(entries) // args.sample)
        for e in entries[::step][:args.sample]:
            title = ""
            try:
                with gzip.open(os.path.join(ROOT, e["path"]), "rb") as fh:
                    text = fh.read(200000).decode("utf-8", "replace")
                i = text.find("<title")
                if i >= 0:
                    j = text.find("</title>", i)
                    title = " ".join(text[i:j + 8].split())[:110]
            except OSError:
                title = "(解压失败)"
            print("   %s / %-14s / %s" % (e["asin"], e["status"], title))

    if args.manifest:
        manifest = {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "db": os.path.relpath(args.db, ROOT),
            "pages_dir": os.path.relpath(args.pages_dir, ROOT),
            "file_count": len(entries),
            "gz_bytes": gz_total,
            "raw_bytes": raw_total,
            "digest_of_entries": hashlib.sha256(
                "\n".join("%s %s" % (e["asin"], e["sha256"]) for e in entries).encode("utf-8")
            ).hexdigest(),
            "problems": [{"asin": a, "why": w} for a, w in problems],
            "orphans": orphans,
            "entries": entries,
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.manifest)), exist_ok=True)
        with open(args.manifest, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
        print("manifest       = %s (digest_of_entries=%s)" % (
            args.manifest, manifest["digest_of_entries"]))
    return 0 if not problems and not orphans else 1


if __name__ == "__main__":
    sys.exit(main())
