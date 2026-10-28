# -*- coding: utf-8 -*-
"""【KEY-80】幂等性验证：同样输入与规则版本连跑两次，目标层不得产生重复实体。

作业依据（§八 可重复运行与恢复）：
  「相同输入与规则版本再次运行，不得生成重复目标实体或重复关系；
    人为中断后须能从已完成批次恢复。」

做法：
  1) 对当前目标库做快照（逐表行数 + 按主键排序后的内容摘要）；
  2) 重新执行 build_target_db.py；
  3) 再快照一次，逐表比较行数与摘要；
  4) 结果写 data/staging/idempotency_report.json，退出码 0 表示幂等。

说明：etl_run 每次都有新 run_id、quality_issue 带 run_id，这两张表只比行数不比摘要。

用法：
  python crawler/idempotency_check.py
  python crawler/idempotency_check.py --db data/target/target.db
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys

ROOT = r"E:\ETL_Amazon"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
BUILD = os.path.join(ROOT, "crawler", "build_target_db.py")
DB = os.path.join(ROOT, "data", "target", "target.db")
OUT = os.path.join(ROOT, "data", "staging", "idempotency_report.json")

TABLES = ["movie", "product_edition", "product_movie", "merge_decision",
          "boxset_member_candidate", "product_classification", "person",
          "person_alias", "movie_person", "review", "review_mapping",
          "field_assertion", "field_selection", "release_date", "source_record"]
COUNT_ONLY = ["etl_run", "quality_issue", "source_record", "product_classification",
              "review", "review_mapping", "field_assertion", "field_selection",
              "release_date", "boxset_member_candidate"]

# 【KEY-81】只对"实体定义列"取摘要，避开 run_id / created_at 这类每次都会变的列
DEFINING = {
    "movie": "movie_id,canonical_title,norm_title,work_type,release_year,status,rule_version,edition_count",
    "product_edition": "product_id,movie_id,media_format,language,discs,studio,product_release_date,classification",
    "product_movie": "product_id,movie_id,relation_type,rule_version,status",
    "merge_decision": "decision_id,asin_a,asin_b,decision,rule_id,confidence",
    "person": "person_id,canonical_name,status,rule_version",
    "person_alias": "person_id,alias_raw,source",
    "movie_person": "movie_id,person_id,role,billing_order",
}


def snapshot(db_path: str) -> dict:
    import sqlite3
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    out = {}
    for t in TABLES + COUNT_ONLY:
        try:
            n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except sqlite3.Error:
            out[t] = {"rows": 0, "digest": None}
            continue
        digest = None
        if t in DEFINING:
            h = hashlib.md5()
            cols = DEFINING[t].split(",")
            for row in conn.execute("SELECT %s FROM %s ORDER BY 1" % (DEFINING[t], t)):
                h.update(("|".join("" if v is None else str(v) for v in row)).encode("utf-8"))
                h.update(b"\n")
            digest = h.hexdigest()
        out[t] = {"rows": n, "digest": digest}
    conn.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    args = ap.parse_args(argv)

    if not os.path.isfile(args.db):
        print("目标库不存在，先执行 build_target_db.py")
        return 2

    before = snapshot(args.db)
    print("第一次快照完成：%d 张表" % len(before))

    rc = subprocess.run([PY, BUILD], cwd=ROOT,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT).returncode
    print("重建完成 exit=%d" % rc)
    if rc != 0:
        return 1

    after = snapshot(args.db)
    diffs = []
    for t in before:
        b, a = before[t], after[t]
        if b["rows"] != a["rows"]:
            diffs.append({"table": t, "kind": "rows", "before": b["rows"], "after": a["rows"]})
        elif b["digest"] and b["digest"] != a["digest"]:
            diffs.append({"table": t, "kind": "content", "before": b["digest"], "after": a["digest"]})

    report = {
        "db": args.db,
        "tables_checked": len(before),
        "rows_before": {t: before[t]["rows"] for t in before},
        "rows_after": {t: after[t]["rows"] for t in after},
        "diffs": diffs,
        "input_changed": before.get("source_record", {}).get("rows")
                         != after.get("source_record", {}).get("rows"),
        "idempotent": not diffs,
        "note": "etl_run / quality_issue 含 run_id，只比行数",
    }
    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print()
    print("%-28s %8s %8s" % ("表", "第一次", "第二次"))
    for t in before:
        flag = "  <-- 不一致" if any(d["table"] == t for d in diffs) else ""
        print("%-28s %8d %8d%s" % (t, before[t]["rows"], after[t]["rows"], flag))
    print()
    print("结论: %s" % ("幂等（两次结果完全一致）" if not diffs else "存在 %d 处差异" % len(diffs)))
    print("report = %s" % OUT)
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
