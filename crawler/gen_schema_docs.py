# -*- coding: utf-8 -*-
"""【KEY-79】从目标库自动生成 ER 图（Mermaid）与字段字典。

数据源：data/target/target.db 的 sqlite_master + PRAGMA（权威结构，与实际库一致）。
产物：
  design/ER图.md         Mermaid erDiagram（GitHub/VSCode 可直接渲染）
  design/字段字典.md     表/字段/类型/可空/主键/外键/说明

用法：
  python crawler/gen_schema_docs.py
"""
from __future__ import annotations

import io
import os
import sqlite3
import sys

ROOT = r"E:\ETL_Amazon"
DB = os.path.join(ROOT, "data", "target", "target.db")
OUT_ER = os.path.join(ROOT, "design", "ER图.md")
OUT_DICT = os.path.join(ROOT, "design", "字段字典.md")

# 对外展示的实体关系（父 -> 子，基数）
RELATIONS = [
    ("movie", "product_edition", "||--o{", "movie_id"),
    ("movie", "product_movie", "||--o{", "movie_id"),
    ("product_edition", "product_movie", "||--o{", "product_id"),
    ("product_edition", "review", "||--o{", "product_id"),
    ("reviewer", "review", "||--o{", "reviewer_id"),
    ("review", "review_mapping", "||--o|", "review_id"),
    ("movie", "movie_person", "||--o{", "movie_id"),
    ("person", "movie_person", "||--o{", "person_id"),
    ("person", "person_alias", "||--o{", "person_id"),
    ("product_edition", "product_classification", "||--o|", "product_id"),
    ("source_record", "product_edition", "||--o|", "product_id"),
    ("movie", "release_date", "||--o|", "movie_id"),
    ("movie", "field_selection", "||--o{", "entity_id"),
    ("movie", "merge_decision", "||--o{", "asin_a"),
    ("source_record", "field_assertion", "||--o{", "source_record_id"),
    ("etl_run", "quality_issue", "||--o{", "run_id"),
    ("product_edition", "boxset_member_candidate", "||--o{", "product_id"),
]

COLUMN_DOC = {
    ("etl_run", "run_id"): "批次号，贯穿 quality_issue / field_assertion 的 run_id",
    ("etl_run", "tool"): "执行工具（python / pdi）",
    ("etl_run", "rule_version"): "规则版本号",
    ("etl_run", "status"): "running / ok / failed",
    ("source_record", "source_record_id"): "源记录标识（当前用 ASIN）",
    ("source_record", "asin"): "商品 ASIN",
    ("source_record", "snapshot_path"): "快照文件路径（分桶目录）",
    ("source_record", "content_sha256"): "解压后页面内容的 SHA-256",
    ("source_record", "fetched_at"): "抓取时间（UTC）",
    ("source_record", "status"): "success / not_found / parse_failed / validation_page",
    ("source_file", "file_name"): "评论分片文件名（part-XXXXX.csv.gz）",
    ("source_file", "sha256"): "分片文件 SHA-256",
    ("movie", "movie_id"): "作品标识（M-合并 / S-剧集 / E-单商品）",
    ("movie", "canonical_title"): "规范标题（保留原始写法）",
    ("movie", "norm_title"): "标准化标题（去版本标记，用于候选生成）",
    ("movie", "work_type"): "film / series / concert / anime / boxset",
    ("movie", "release_year"): "上映年份；缺则空，不推断",
    ("movie", "edition_count"): "该作品下的版本数",
    ("product_edition", "product_id"): "商品 ASIN（版本级主键）",
    ("product_edition", "movie_id"): "所属作品",
    ("product_edition", "media_format"): "载体/格式（DVD、Blu-ray、VHS 等）",
    ("product_edition", "product_release_date"): "商品发售日（不是上映日）",
    ("product_movie", "relation_type"): "single / same_work_edition / series_volume / boxset_member",
    ("merge_decision", "decision"): "auto / review / reject",
    ("merge_decision", "rule_id"): "判定规则编号（A-* 自动、M-* 复核、R-* 不合并）",
    ("merge_decision", "confidence"): "置信度（经验标度）",
    ("review", "review_id"): "评论主键（md5(asin|record_index)）",
    ("review", "product_id"): "所属商品 ASIN",
    ("review", "reviewer_id"): "评论者 ID",
    ("review", "helpful_num"): "有用票分子（必须 ≤ 分母）",
    ("review", "helpful_den"): "有用票分母",
    ("review", "review_time_epoch"): "评论时间（Unix 秒）",
    ("review", "record_index"): "在分片中的记录序号（血缘定位）",
    ("review", "part_file"): "来源分片文件名",
    ("review_mapping", "logical_review_id"): "归并后的逻辑评论 ID",
    ("review_mapping", "reason"): "D1 完全重复 / D2 文本近似 / D3 跨商品候选",
    ("person", "person_id"): "人物标识",
    ("person", "canonical_name"): "规范姓名",
    ("movie_person", "role"): "director / actor",
    ("field_assertion", "assertion_id"): "断言主键",
    ("field_assertion", "entity_type"): "movie / product / review",
    ("field_assertion", "locator"): "快照哈希#JSON 路径，或 分片#记录号",
    ("field_assertion", "rule_id"): "抽取规则版本（parse_product_page/v1 等）",
    ("field_selection", "selected_assertion_id"): "最终采用的断言",
    ("field_selection", "status"): "selected / inferred_only / missing / conflict",
    ("release_date", "status"): "verified / inferred / missing",
    ("release_date", "precision"): "day / year",
    ("quality_issue", "issue_type"): "page_not_found / template_unparsed / review_field_invalid",
    ("product_classification", "classification"): "movie / movie_weak / series / concert / music / boxset / anime / sport / pending / no_page",
}


def main() -> int:
    if not os.path.isfile(DB):
        print("目标库不存在: %s" % DB)
        return 2
    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    # ---------- ER 图 ----------
    lines = ["# 目标层 ER 图（自动生成）", "",
             "> 由 crawler/gen_schema_docs.py 依据 data/target/target.db 生成；",
             "> Mermaid 语法，GitHub / VSCode 可直接渲染。", "",
             "```mermaid", "erDiagram"]
    names = {t: t for t in tables}
    for parent, child, card, fk in RELATIONS:
        if parent in names and child in names:
            lines.append('    %s %s %s : "%s"' % (parent, card, child, fk))
    for t in tables:
        cols = conn.execute("PRAGMA table_info(%s)" % t).fetchall()
        if not cols:
            continue
        lines.append("    %s {" % t)
        for _cid, name, typ, notnull, dflt, pk in cols:
            flag = " PK" if pk else ""
            eff = (typ or "TEXT").split("(")[0].upper()
            lines.append("        %s %s%s" % (eff, name, flag))
        lines.append("    }")
    lines += ["```", ""]
    with io.open(OUT_ER, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    # ---------- 字段字典 ----------
    out = ["# 目标层字段字典（自动生成）", "",
           "> 由 crawler/gen_schema_docs.py 依据 data/target/target.db 生成；",
           "> 说明列只覆盖关键字段，未覆盖的字段按名称自解释。", ""]
    total_cols = 0
    for t in tables:
        cols = conn.execute("PRAGMA table_info(%s)" % t).fetchall()
        if not cols:
            continue
        fks = conn.execute("PRAGMA foreign_key_list(%s)" % t).fetchall()
        fk_map = {(r[3]): (r[2], r[4]) for r in fks}
        out.append("## %s" % t)
        out.append("")
        out.append("| 字段 | 类型 | 可空 | 主键 | 外键 | 说明 |")
        out.append("|---|---|---|---|---|---|")
        for _cid, name, typ, notnull, dflt, pk in cols:
            total_cols += 1
            fk = fk_map.get(name)
            fk_s = ("%s.%s" % fk) if fk else ""
            out.append("| %s | %s | %s | %s | %s | %s |" % (
                name, typ or "", "否" if notnull else "是", "是" if pk else "",
                fk_s, COLUMN_DOC.get((t, name), "")))
        out.append("")
    with io.open(OUT_DICT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    conn.close()

    print("tables      = %d" % len(tables))
    print("columns     = %d" % total_cols)
    print("ER 图       = %s" % OUT_ER)
    print("字段字典    = %s" % OUT_DICT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
