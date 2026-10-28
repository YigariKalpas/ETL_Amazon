# -*- coding: utf-8 -*-
"""覆盖率报告（作业 §十：分字段报告覆盖率，电影信息以已识别作品为分母，另报告全部产品的识别覆盖率）。

三个分母严格区分：
  D1 已识别作品 —— 实体层的 movie（有作品归属的商品聚合后）
  D2 成功页面   —— 解析通过的 product（版本级）
  D3 全部产品   —— 253,059（只用于识别覆盖率这一分母）

产物：design/覆盖率报告.md、data/staging/coverage/summary.json

用法：python crawler/coverage_report.py
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = r"E:\\ETL_Amazon"
CLS = os.path.join(ROOT, "data", "staging", "classification", "classification.csv")
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
ENT = os.path.join(ROOT, "data", "staging", "entities")
OUT_MD = os.path.join(ROOT, "design", "覆盖率报告.md")
OUT_JSON = os.path.join(ROOT, "data", "staging", "coverage", "summary.json")
TOTAL_PRODUCTS = 253059

FIELDS = [
    ("title_raw", "作品标题"),
    ("breadcrumb_categories", "类目面包屑"),
    ("media_format", "载体/格式"),
    ("studio", "厂牌/发行商"),
    ("runtime_minutes", "片长"),
    ("actors_raw", "演员"),
    ("product_release_date_raw", "商品发售日"),
    ("language", "语言"),
    ("discs", "碟数"),
    ("mpaa_rating", "分级"),
    ("people_raw", "结构化人物"),
    ("genre_labels_raw", "类型标签"),
    ("review_count_raw", "评论数"),
    ("rating_raw", "评分"),
]


def present(rec, key):
    v = rec.get(key)
    if key == "people_raw":
        return bool(v)
    if key == "genre_labels_raw":
        return any(x for x in (v or []) if x)
    if key == "breadcrumb_categories":
        return bool(v)
    if isinstance(v, str):
        v = v.strip()
        return bool(v) and v.lower() not in ("various", "artist not provided", "n/a")
    return v not in (None, "", [], {})


def read_csv(path):
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-md", default=OUT_MD)
    ap.add_argument("--out-json", default=OUT_JSON)
    args = ap.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)

    cls = {r["asin"]: r for r in read_csv(CLS)}
    pm = {r["product_id"]: r["movie_id"] for r in read_csv(os.path.join(ENT, "product_movie.csv"))}
    movies = {r["movie_id"]: r for r in read_csv(os.path.join(ENT, "movie.csv"))}

    pages = {}
    for name in os.listdir(STAGING):
        if name.endswith(".json"):
            with io.open(os.path.join(STAGING, name), encoding="utf-8") as fh:
                rec = json.load(fh)
            pages[rec.get("asin") or name[:-5]] = rec

    works = {m for m, r in movies.items() if r.get("work_type") in
             ("film", "series", "concert", "anime", "boxset")}
    movie_pages = defaultdict(list)
    for asin, mid in pm.items():
        if mid in works and asin in pages:
            movie_pages[mid].append(asin)

    page_hits = Counter()
    movie_hits = Counter()
    for asin, rec in pages.items():
        for key, _label in FIELDS:
            if present(rec, key):
                page_hits[key] += 1
    for mid, asins in movie_pages.items():
        for key, _label in FIELDS:
            if any(present(pages[a], key) for a in asins):
                movie_hits[key] += 1

    n_pages = len(pages)
    n_movies = len(movie_pages)

    lines = []
    lines.append("# 覆盖率报告（自动生成）")
    lines.append("")
    lines.append("> 生成：crawler/coverage_report.py；数据源为本地暂存与目标层，不联网。")
    lines.append("> 作业 §十 要求分字段报告覆盖率：电影信息以已识别作品为分母，另报告全部产品的识别覆盖率。")
    lines.append("")
    lines.append("## 一、分母与样本")
    lines.append("")
    lines.append("| 分母 | 数量 | 说明 |")
    lines.append("|---|---|---|")
    lines.append("| 全部产品 | %d | 采集清单总量 |" % TOTAL_PRODUCTS)
    lines.append("| 成功页面（版本级） | %d | 采样率 %.3f%% |" % (n_pages, 100.0 * n_pages / TOTAL_PRODUCTS))
    lines.append("| 已识别作品（作品级） | %d | 有作品归属且能归类 |" % n_movies)
    lines.append("| 作品实体总数 | %d | 目标层 movie 行数 |" % len(movies))
    lines.append("")
    lines.append("## 二、识别覆盖率")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append("| 已识别作品 / 全部产品 | %d / %d = **%.2f%%** |" % (n_movies, TOTAL_PRODUCTS, 100.0 * n_movies / TOTAL_PRODUCTS))
    lines.append("| 采集成功页 / 全部产品 | %d / %d = %.2f%% |" % (n_pages, TOTAL_PRODUCTS, 100.0 * n_pages / TOTAL_PRODUCTS))
    lines.append("")
    lines.append("## 三、分字段覆盖率（双分母）")
    lines.append("")
    lines.append("| 字段 | 含义 | 已识别作品 | 成功页面 |")
    lines.append("|---|---|---|---|")
    for key, label in FIELDS:
        a, b = movie_hits[key], page_hits[key]
        lines.append("| %s | %s | %d/%d（%.0f%%） | %d/%d（%.0f%%） |" % (
            key, label, a, n_movies, 100.0 * a / max(1, n_movies),
            b, n_pages, 100.0 * b / max(1, n_pages)))
    lines.append("")
    lines.append("## 四、分类分布（版本级）")
    lines.append("")
    dist = Counter(r["classification"] for r in cls.values())
    lines.append("| 分类 | 条数 | 占比 |")
    lines.append("|---|---|---|")
    for k, v in dist.most_common():
        lines.append("| %s | %d | %.1f%% |" % (k, v, 100.0 * v / max(1, sum(dist.values()))))
    lines.append("")
    lines.append("## 五、评论覆盖（与目标层一致）")
    lines.append("")
    db = os.path.join(ROOT, "data", "target", "target.db")
    review_rows = review_products = reviews_on_works = 0
    if os.path.isfile(db):
        import sqlite3
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        review_rows = conn.execute("SELECT COUNT(*) FROM review").fetchone()[0]
        review_products = conn.execute("SELECT COUNT(DISTINCT product_id) FROM review").fetchone()[0]
        reviews_on_works = conn.execute(
            "SELECT COUNT(*) FROM review r JOIN product_edition e ON e.product_id=r.product_id "
            "JOIN movie m ON m.movie_id=e.movie_id WHERE m.work_type IN "
            "('film','series','concert','anime','boxset')").fetchone()[0]
        conn.close()
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append("| 已入库评论 | %d |" % review_rows)
    lines.append("| 覆盖商品数 | %d |" % review_products)
    lines.append("| 落在已识别作品上的评论 | %d（%.1f%%） |" % (reviews_on_works, 100.0 * reviews_on_works / max(1, review_rows)))
    lines.append("")
    lines.append("## 六、口径说明（写报告时要带上）")
    lines.append("")
    lines.append("1. **分母不同不可混用**：作品级覆盖率用 D1，字段可得性用 D2，识别率用 D3；")
    lines.append("2. **genre 偏低是客观缺失**：亚马逊详情表本身少给类型，靠面包屑与标题补；")
    lines.append("3. **people 覆盖率低于 actors**：Actors: Various 等无依据值按作业 §五 主动放弃；")
    lines.append("4. **页面 Release date 是商品发售日**，不是上映年，不参与上映年份覆盖；")
    lines.append("5. 样本为随机抽样（种子 20261023），覆盖率结论只代表样本，不能外推全量。")
    lines.append("")
    with io.open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    summary = {
        "total_products": TOTAL_PRODUCTS,
        "success_pages": n_pages,
        "movie_entities": len(movies),
        "identified_works": n_movies,
        "identification_rate": round(100.0 * n_movies / TOTAL_PRODUCTS, 3),
        "sample_rate": round(100.0 * n_pages / TOTAL_PRODUCTS, 3),
        "field_coverage_movie": {k: movie_hits[k] for k, _l in FIELDS},
        "field_coverage_page": {k: page_hits[k] for k, _l in FIELDS},
        "classification_dist": dict(dist),
        "reviews": {"rows": review_rows, "products": review_products, "on_works": reviews_on_works},
    }
    with io.open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print("pages            = %d" % n_pages)
    print("identified_works = %d (%.3f%% of all products)" % (n_movies, 100.0 * n_movies / TOTAL_PRODUCTS))
    for key, label in FIELDS[:6]:
        print("  %-24s movie %d/%d  page %d/%d" % (key, movie_hits[key], n_movies, page_hits[key], n_pages))
    print("md   = %s" % args.out_md)
    print("json = %s" % args.out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())