# -*- coding: utf-8 -*-
"""数据库中内容的概览：逐表行数 + 关键分布，输出 Markdown。

数据源：data/target/target.db（目标层）与 data/crawler/products.sqlite（采集）。
用法：python crawler/db_inventory.py
"""
from __future__ import annotations

import collections
import io
import os
import sqlite3
import sys

ROOT = r"E:\\ETL_Amazon"
TARGET = os.path.join(ROOT, "data", "target", "target.db")
CRAWL = os.path.join(ROOT, "data", "crawler", "products.sqlite")
OUT = os.path.join(ROOT, "design", "数据库内容概览.md")


def rows_of(conn, sql):
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []


def main():
    lines = ["# 数据库内容概览（自动生成）", "",
             "> 生成：crawler/db_inventory.py；只读 target.db 与 products.sqlite。", ""]
    if not os.path.isfile(TARGET):
        print("目标库不存在")
        return 2
    conn = sqlite3.connect("file:%s?mode=ro" % TARGET, uri=True)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    counts = {}
    for t in tables:
        counts[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]

    lines += ["## 一、表与行数", "", "| 表 | 行数 |", "|---|---|"]
    total_rows = 0
    for t in tables:
        lines.append("| %s | %d |" % (t, counts[t]))
        total_rows += counts[t]
    lines.append("| **合计** | **%d** |" % total_rows)
    lines.append("")

    def dist(title, sql, limit=12):
        rows = rows_of(conn, sql)
        if not rows:
            return
        lines.extend(["", "### " + title, "", "| 值 | 行数 |", "|---|---|"])
        for r in rows[:limit]:
            lines.append("| %s | %d |" % (r[0], r[1]))
        lines.append("")

    lines += ["## 二、关键分布", ""]
    dist("作品类型（movie.work_type）",
         "SELECT work_type, COUNT(*) FROM movie GROUP BY work_type ORDER BY 2 DESC")
    dist("分类结果（product_classification）",
         "SELECT classification, COUNT(*) FROM product_classification GROUP BY classification ORDER BY 2 DESC")
    dist("合并判定（merge_decision）",
         "SELECT decision || ' / ' || rule_id, COUNT(*) FROM merge_decision GROUP BY decision, rule_id ORDER BY 2 DESC")
    dist("上映信息状态（release_date）",
         "SELECT status, COUNT(*) FROM release_date GROUP BY status ORDER BY 2 DESC")
    dist("血缘断言（field_assertion.entity_type）",
         "SELECT entity_type, COUNT(*) FROM field_assertion GROUP BY entity_type ORDER BY 2 DESC")
    dist("血缘字段 Top10（field_assertion.field_name）",
         "SELECT field_name, COUNT(*) FROM field_assertion GROUP BY field_name ORDER BY 2 DESC LIMIT 10")
    dist("质量问题类型（quality_issue.issue_type）",
         "SELECT issue_type, COUNT(*) FROM quality_issue GROUP BY issue_type ORDER BY 2 DESC")
    dist("人物角色（movie_person.role）",
         "SELECT role, COUNT(*) FROM movie_person GROUP BY role ORDER BY 2 DESC")
    dist("评论评分分布（review.score）",
         "SELECT score, COUNT(*) FROM review GROUP BY score ORDER BY 1")
    dist("去重原因（review_mapping.reason）",
         "SELECT reason, COUNT(*) FROM review_mapping GROUP BY reason ORDER BY 2 DESC")
    dist("字段选择状态（field_selection.status）",
         "SELECT status, COUNT(*) FROM field_selection GROUP BY status ORDER BY 2 DESC")

    rng = rows_of(conn, "SELECT MIN(review_time_iso), MAX(review_time_iso) FROM review")
    if rng:
        lines += ["", "### 评论时间范围", "", "| 最早 | 最晚 |", "|---|---|"]
        lines.append("| %s | %s |" % (rng[0][0], rng[0][1]))
        lines.append("")
    top = rows_of(conn, "SELECT product_id, COUNT(*) FROM review GROUP BY product_id ORDER BY 2 DESC LIMIT 5")
    if top:
        lines += ["### 评论最多的商品 Top5", "", "| 商品 | 评论数 |", "|---|---|"]
        for r in top:
            lines.append("| %s | %d |" % (r[0], r[1]))
        lines.append("")
    conn.close()

    # 采集库
    lines += ["## 三、采集库（products.sqlite）", "", "| 状态 | 行数 |", "|---|---|"]
    if os.path.isfile(CRAWL):
        c = sqlite3.connect("file:%s?mode=ro" % CRAWL, uri=True)
        for k, v in c.execute("SELECT status, COUNT(*) FROM product_page GROUP BY status ORDER BY 2 DESC"):
            lines.append("| %s | %d |" % (k, v))
        c.close()
    lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fh:
        fh.write(chr(10).join(lines))
    print("tables=%d rows_total=%d" % (len(tables), total_rows))
    print("md = %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
