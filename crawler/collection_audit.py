# -*- coding: utf-8 -*-
"""【KEY-33】采集现状审计：一次性回答"这批数据到底成色如何"。

只读本地产物（状态库 / 快照 / 暂存 JSON / 分类 CSV），不发任何网络请求。

输出四块：
  A 规模与体积：各状态条数、快照体积统计；
  B 自洽性检查：success 里有没有"其实不是产品页"的（404 页、Prime Video 模板、空字段）；
  C 字段覆盖率：分两个分母（已识别作品 / 全部成功页），并给出全部产品的识别率；
  D 人工审核抽样：分层抽样写 CSV，供逐条肉眼核对。

用法：
  python crawler/collection_audit.py
  python crawler/collection_audit.py --sample 30 --out data/staging/audit_sample.csv
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

ROOT = r"E:\ETL_Amazon"
DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
CLS = os.path.join(ROOT, "data", "staging", "classification", "classification.csv")
TOTAL_PRODUCTS = 253059

WORKS = ("movie", "movie_weak", "series", "concert", "boxset", "anime")


def field_present(rec: dict, key: str) -> bool:
    v = rec.get(key)
    if key == "people_raw":
        return bool(v)
    if key == "genre_labels_raw":
        return any(x for x in (v or []) if x)
    if key == "breadcrumb_categories":
        return bool(v)
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return False
        # “Actors: Various” 这类无信息值不算覆盖
        if v.lower() in ("various", "artist not provided", "n/a", "unknown"):
            return False
        return True
    return v not in (None, "", [], {})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="采集现状审计")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--cls", default=CLS)
    ap.add_argument("--sample", type=int, default=0, help="分层抽样导出多少条")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "staging", "audit_sample.csv"))
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT asin, status, snapshot_path, content_length, fetched_at, note "
        "FROM product_page ORDER BY asin").fetchall()

    # ---------- A 规模与体积 ----------
    by_status = Counter(r[1] for r in rows)
    sizes = defaultdict(list)
    gz_sizes = defaultdict(list)
    for asin, status, path, length, _f, _n in rows:
        if length:
            sizes[status].append(length)
        if path and os.path.isfile(path):
            gz_sizes[status].append(os.path.getsize(path))

    print("=" * 62)
    print("A. 规模与体积")
    print("=" * 62)
    print("状态行数        : %d" % len(rows))
    for st, n in by_status.most_common():
        s = sizes.get(st, [])
        g = gz_sizes.get(st, [])
        print("  %-15s : %4d 条  原始均值 %7.1f KB  合计 %7.2f MB | gzip 合计 %7.2f MB"
              % (st, n, (sum(s) / len(s) / 1024.0) if s else 0,
                 sum(s) / 1024.0 / 1024.0, sum(g) / 1024.0 / 1024.0))

    # ---------- 读暂存与分类 ----------
    staged = {}
    for name in os.listdir(args.staging):
        if name.endswith(".json"):
            with io.open(os.path.join(args.staging, name), encoding="utf-8") as fh:
                rec = json.load(fh)
            staged[rec.get("asin") or name[:-5]] = rec

    cls = {}
    if os.path.isfile(args.cls):
        with io.open(args.cls, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                cls[r["asin"]] = r

    # ---------- B 自洽性 ----------
    print()
    print("=" * 62)
    print("B. success 页面的自洽性")
    print("=" * 62)
    n404 = nprime = nempty = nnotitle = nvarious = 0
    bc_top = Counter()
    for asin, r in staged.items():
        title = (r.get("title_raw") or "").strip()
        if not title:
            nnotitle += 1
        if "page not found" in title.lower():
            n404 += 1
        if title.lower() == "prime video":
            nprime += 1
        detail = r.get("detail") or {}
        if not detail and not r.get("actors_raw") and not r.get("studio"):
            nempty += 1
        if "various" in (r.get("actors_raw") or "").lower():
            nvarious += 1
        bcs = r.get("breadcrumb_categories") or []
        bc_top[bcs[0] if bcs else "(空)"] += 1
    print("暂存 JSON        : %d 个（应等于 success 数 %d）" % (len(staged), by_status.get("success", 0)))
    print("标题为空         : %d" % nnotitle)
    print("标题是 404 页    : %d   <- 若 >0 说明状态判定有误" % n404)
    print("Prime Video 模板 : %d" % nprime)
    print("无任何商品字段   : %d" % nempty)
    print("Actors: Various  : %d   <- 按作业 §五 视为不可识别，不生成 Person" % nvarious)
    print("breadcrumb 首层分布（前 8）:")
    for k, v in bc_top.most_common(8):
        print("  %-28s %d" % (k, v))

    # ---------- C 覆盖率（双分母） ----------
    print()
    print("=" * 62)
    print("C. 字段覆盖率（双分母）")
    print("=" * 62)
    fields = ["title_raw", "breadcrumb_categories", "media_format", "studio",
              "runtime_minutes", "actors_raw", "product_release_date_raw", "language",
              "discs", "mpaa_rating", "people_raw", "genre_labels_raw"]
    works = [a for a, r in cls.items() if r.get("classification") in WORKS]
    denom_works = [a for a in works if a in staged]
    denom_success = list(staged.keys())
    print("分母① 已识别作品 : %d" % len(denom_works))
    print("分母② 成功页面   : %d" % len(denom_success))
    print("识别率（全部产品）: %d / %d = %.2f%%" % (len(works), TOTAL_PRODUCTS,
                                                   100.0 * len(works) / TOTAL_PRODUCTS))
    print()
    print("%-26s %12s %12s" % ("字段", "已识别作品", "成功页面"))
    for f in fields:
        a = sum(1 for x in denom_works if field_present(staged[x], f))
        b = sum(1 for x in denom_success if field_present(staged[x], f))
        print("%-26s %5d/%3d %4.0f%% %5d/%3d %4.0f%%" % (
            f, a, len(denom_works), 100.0 * a / max(1, len(denom_works)),
            b, len(denom_success), 100.0 * b / max(1, len(denom_success))))

    # ---------- D 分层抽样 ----------
    if args.sample > 0:
        buckets = defaultdict(list)
        for a, r in cls.items():
            if a in staged:
                buckets[r.get("classification") or "(未知)"].append(a)
        picked = []
        per = max(1, args.sample // max(1, len(buckets)))
        for label in sorted(buckets, key=lambda k: -len(buckets[k])):
            picked.extend(sorted(buckets[label])[:per])
        picked = picked[:args.sample]
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with io.open(args.out, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["asin", "分类", "标题", "breadcrumb首层", "媒体格式",
                        "厂牌", "时长(分)", "演员数", "快照路径", "人工结论(待填)"])
            for a in picked:
                r = staged[a]
                c = cls.get(a, {})
                bcs = r.get("breadcrumb_categories") or []
                actors = (r.get("actors_raw") or "").split(",") if r.get("actors_raw") else []
                w.writerow([a, c.get("classification", ""), r.get("title_raw", ""),
                            bcs[0] if bcs else "", r.get("media_format") or "",
                            r.get("studio") or "", r.get("runtime_minutes") or "",
                            len([x for x in actors if x.strip()]), r.get("snapshot_path", ""), ""])
        print()
        print("D. 抽样清单已写入 %s（%d 条，按分类分层）" % (args.out, len(picked)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
