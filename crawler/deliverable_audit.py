# -*- coding: utf-8 -*-
"""【KEY-78】交付物盘点：对照作业要求，检查"现在有什么、还缺什么"。

只读文件与目标库，不发请求。每项给出：要求 / 当前证据 / 状态(有/部分/缺)。

用法：
  python crawler/deliverable_audit.py
"""
from __future__ import annotations

import glob
import io
import os
import sqlite3
import sys

ROOT = r"E:\ETL_Amazon"
TARGET = os.path.join(ROOT, "data", "target", "target.db")
CLS_SUM = os.path.join(ROOT, "data", "staging", "classification", "summary.json")
AUDIT_CSV = os.path.join(ROOT, "data", "staging", "audit_sample.csv")
CONFLICT = os.path.join(ROOT, "data", "staging", "conflicts", "conflicts.csv")
REVIEW_QUEUE = os.path.join(ROOT, "data", "staging", "entities", "review_queue.csv")


def exists(pat: str) -> bool:
    return bool(glob.glob(os.path.join(ROOT, pat)))


def csv_rows(path: str) -> int:
    if not os.path.isfile(path):
        return 0
    with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def main() -> int:
    db_rows = {}
    review_assertions = conflict_rows = reviewed = 0
    if os.path.isfile(TARGET):
        conn = sqlite3.connect("file:%s?mode=ro" % TARGET, uri=True)
        for t in ("movie", "product_edition", "review", "review_mapping", "person",
                  "movie_person", "field_assertion", "field_selection", "quality_issue"):
            try:
                db_rows[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except sqlite3.Error:
                db_rows[t] = 0
        try:
            review_assertions = conn.execute(
                "SELECT COUNT(*) FROM field_assertion WHERE entity_type='review'").fetchone()[0]
        except sqlite3.Error:
            pass
        try:
            reviewed = conn.execute(
                "SELECT COUNT(*) FROM movie WHERE edition_count>1").fetchone()[0]
        except sqlite3.Error:
            pass
        conn.close()

    conflict_rows = csv_rows(CONFLICT)

    checks = [
        ("建表/建模脚本", "sql/target_schema.sqlite.sql + pg.sql",
         "有" if (os.path.isfile(os.path.join(ROOT, "sql", "target_schema.sqlite.sql"))
                  and os.path.isfile(os.path.join(ROOT, "sql", "target_schema.pg.sql"))) else "缺"),
        ("ER 图", "需要实体关系图", "有" if exists("design/ER*.md") or exists("design/*ER*") else "缺"),
        ("字段字典", "每个字段的含义/类型/约束", "有" if exists("design/*字段字典*") else "缺"),
        ("PDI 作业定义", "*.kjb / *.ktr（作业 §八 要求提交作业定义与依赖）",
         "有" if (exists("etl/**/*.kjb") or exists("etl/**/*.ktr") and glob.glob(os.path.join(ROOT, "etl", "jobs", "*.kjb"))) else "缺"),
        ("分类覆盖率报告", "双分母（已识别作品 / 全部产品）",
         "有" if os.path.isfile(CLS_SUM) else "缺"),
        ("覆盖率报告", "design/覆盖率报告.md（双分母）",
         "有" if os.path.isfile(os.path.join(ROOT, "design", "覆盖率报告.md")) else "缺"),
        ("冲突案例", "任务7 要求保留冲突状态与候选值",
         "有(%d)" % conflict_rows if conflict_rows else "缺(0 条)"),
        ("评论级字段血缘", "任务9 要求原始评论可定位到分片+记录号",
         "有(%d)" % review_assertions if review_assertions else "缺(0 条)"),
        ("人工抽样审核", "audit_sample.csv 需填 human 结论",
         "有(%d 行待填)" % csv_rows(AUDIT_CSV) if os.path.isfile(AUDIT_CSV) else "缺"),
        ("合并人工复核", "review_queue.csv 的 accept/reject",
         "有(%d 行待填)" % csv_rows(REVIEW_QUEUE) if os.path.isfile(REVIEW_QUEUE) else "缺"),
        ("幂等性验证", "同输入两次运行不产生重复实体",
         "有" if exists("data/staging/idempotency*.json") else "缺"),
        ("目标层实体", "movie/edition/review/person",
         "有(movie %d / edition %d / review %d / person %d)" % (
             db_rows.get("movie", 0), db_rows.get("product_edition", 0),
             db_rows.get("review", 0), db_rows.get("person", 0))),
        ("多版本合并实例", "任务4 要求 ≥5 组合并",
         "有(%d 组多版本 movie)" % reviewed if reviewed else "缺"),
        ("质量问题记录", "quality_issue",
         "有(%d 条)" % db_rows.get("quality_issue", 0) if db_rows.get("quality_issue") else "缺"),
    ]

    print("=" * 86)
    print("%-18s %-34s %s" % ("交付项", "证据", "状态"))
    print("=" * 86)
    missing = []
    for name, evidence, status in checks:
        print("%-18s %-34s %s" % (name, evidence[:34], status))
        if status.startswith("缺"):
            missing.append(name)

    print()
    print("缺失/待办：%d 项 → %s" % (len(missing), "、".join(missing) if missing else "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
