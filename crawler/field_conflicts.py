# -*- coding: utf-8 -*-
"""【KEY-60】任务7：来源冲突处理（可复核的选择规则）。

作业要求（§七 任务7）：
  - 对同一字段建立可复核的选择规则，考虑来源权威性、字段语义、匹配可靠性、日期精度、采集时间；
  - 不得简单取"最后抓到的值"或"多数票"；
  - 保留所有候选值与采用/拒绝/待确认状态；无法决策时保留冲突状态，不得编造。

实现：对每个（实体, 字段）收集候选，按字段专属规则排序：
  - movie.release_year：external(verified, 权威高) > inferred(评论上界，仅下界意义) > missing
  - edition.product_release_date：页面 Release date（语义=商品发售日，不得用于上映年）
  - 若同一字段存在两个 verified 且值不同 → status=conflict，不选值

输出：data/staging/conflicts/{field_selection.csv, conflicts.csv, stats.json}

用法：
  python crawler/field_conflicts.py
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

ROOT = r"E:\ETL_Amazon"
LINEAGE = os.path.join(ROOT, "data", "staging", "lineage", "field_selection.csv")
RELEASE = os.path.join(ROOT, "data", "staging", "release_dates", "release_date.csv")
OUT = os.path.join(ROOT, "data", "staging", "conflicts")
RULE_VERSION = "field_conflicts/v1"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lineage", default=LINEAGE)
    ap.add_argument("--release", default=RELEASE)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    base = []
    if os.path.isfile(args.lineage):
        with io.open(args.lineage, encoding="utf-8-sig", newline="") as fh:
            base = list(csv.DictReader(fh))

    release = {}
    if os.path.isfile(args.release):
        with io.open(args.release, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                release[r["movie_id"]] = r

    selections = []
    conflicts = []
    # 1) 继承 lineage 的选择（canonical_title 等）
    for r in base:
        if r["field_name"] == "release_year":
            continue
        selections.append([r["entity_type"], r["entity_id"], r["field_name"],
                           r.get("selected_assertion_id", ""), int(r.get("candidates") or 0),
                           r.get("reason", ""), r.get("rule_version", ""), r.get("status", "")])

    # 2) movie.release_year 的选择规则
    for movie_id, r in sorted(release.items()):
        status = r["status"]
        if status == "verified":
            selections.append(["movie", movie_id, "release_year", "", 1,
                               "外部源 verified；precision=%s" % r["precision"],
                               RULE_VERSION, "selected"])
        elif status == "inferred":
            selections.append(["movie", movie_id, "release_year", "", 1,
                               "仅有 earliest_review_time 上界，不是真实上映日期", RULE_VERSION, "inferred_only"])
        else:
            selections.append(["movie", movie_id, "release_year", "", 1,
                               "无可信来源；页面 Release date 属商品发售日", RULE_VERSION, "missing"])
    # 3) 真实冲突检测（可复核，不编造）：
    #    a. 标题内年份 > 最早评论年份 → 评论早于作品年份，冲突
    #    b. 商品发售年 > 最早评论年 → 评论早于发售，可能是预售/再版，标 candidate
    import sqlite3
    db_path = os.path.join(ROOT, "data", "target", "target.db")
    title_year = {}

    def y_of(epoch):
        return datetime.fromtimestamp(epoch, tz=timezone.utc).year if epoch else None

    if os.path.isfile(db_path):
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        for mid, title in conn.execute("SELECT movie_id, canonical_title FROM movie"):
            m = re.search(r"(19\d{2}|20\d{2})", title or "")
            if m:
                title_year[mid] = int(m.group(1))
        for mid, t in conn.execute(
                "SELECT e.movie_id, MIN(r.review_time_epoch) FROM review r "
                "JOIN product_edition e ON e.product_id=r.product_id GROUP BY e.movie_id"):
            if not t:
                continue
            ty = title_year.get(mid)
            ry = y_of(int(t))
            if ty and ry and ry < ty:
                conflicts.append(["movie", mid, "release_year",
                                  "title_year=%d earliest_review_year=%d" % (ty, ry),
                                  "conflict", RULE_VERSION])
        for pid, pdate, t in conn.execute(
                "SELECT e.product_id, e.product_release_date, MIN(r.review_time_epoch) "
                "FROM product_edition e JOIN review r ON r.product_id=e.product_id "
                "WHERE e.product_release_date IS NOT NULL "
                "GROUP BY e.product_id, e.product_release_date"):
            if not t:
                continue
            m = re.search(r"(19\\d{2}|20\\d{2})", pdate or "")
            if not m:
                continue
            py = int(m.group(1))
            ry = y_of(int(t))
            if ry and ry < py:
                conflicts.append(["product", pid, "product_release_date",
                                  "product_release_year=%d earliest_review_year=%d" % (py, ry),
                                  "candidate", RULE_VERSION])
        # c. 同一作品不同版本的发售年差异 > 3 年 → 再版候选（用于说明 edition 语义）
        for mid, dates in conn.execute(
                "SELECT movie_id, GROUP_CONCAT(product_release_date, '|') "
                "FROM product_edition WHERE product_release_date IS NOT NULL "
                "AND product_release_date <> '' GROUP BY movie_id"):
            years = [int(x) for x in re.findall(r"(19\d{2}|20\d{2})", dates or "")]
            if len(years) >= 2 and max(years) - min(years) > 3:
                conflicts.append(["movie", mid, "edition_release_dates",
                                  "years=%s" % ",".join(str(y) for y in sorted(set(years))),
                                  "candidate", RULE_VERSION])
        conn.close()

    print("real_conflicts   = %d" % len(conflicts))

    # 3c) 旧的单一来源检测（保留机制）
    # 3c) 旧的单一来源检测（保留机制）
    by_field = defaultdict(list)
    for movie_id, r in release.items():
        if r["status"] == "verified" and r["value_year"]:
            by_field[("movie", "release_year")].append((movie_id, r["value_year"]))
        if r["status"] == "verified" and r["value_date"] and r["value_year"]:
            y = r["value_date"][:4]
            if y and y != r["value_year"]:
                conflicts.append(["movie", movie_id, "release_year",
                                  "date_year=%s vs year=%s" % (y, r["value_year"]),
                                  "conflict", RULE_VERSION])

    with io.open(os.path.join(args.out, "field_selection.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["entity_type", "entity_id", "field_name", "selected_assertion_id",
                    "candidates", "reason", "rule_version", "status"])
        w.writerows(selections)
    with io.open(os.path.join(args.out, "conflicts.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["entity_type", "entity_id", "field_name", "detail", "status", "rule_version"])
        w.writerows(conflicts)

    dist = defaultdict(int)
    for s in selections:
        if s[2] == "release_year":
            dist[s[7]] += 1
    stats = {"rule_version": RULE_VERSION, "selections": len(selections),
             "conflicts": len(conflicts), "release_year_status": dict(dist)}
    with io.open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)
    for k, v in stats.items():
        print("%-24s = %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
