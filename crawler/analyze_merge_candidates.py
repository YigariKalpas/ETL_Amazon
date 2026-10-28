# -*- coding: utf-8 -*-
"""【KEY-40】任务4 前置分析：现有页面里"同一电影多版本"到底长什么样。

只读暂存 JSON + 分类 CSV，不联网。回答三个问题：
  1) 标题标准化后，有多少组出现多个 ASIN（多版本候选）？
  2) 这些候选组内，导演/主演/片长/厂牌/载体的吻合度如何？
  3) 仅靠标题会发生哪些误合并（同名不同作品）？

产出：
  data/staging/merge_candidates.csv   组内明细（供人工看 5 组跨版本、5 组易混）
  标准输出：统计摘要
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = r"E:\ETL_Amazon"
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
CLS = os.path.join(ROOT, "data", "staging", "classification", "classification.csv")
OUT = os.path.join(ROOT, "data", "staging", "merge_candidates.csv")

WORKS = ("movie", "movie_weak", "series", "concert", "boxset", "anime")

# 版本/载体标记：标准化时剥离，但原始标题必须保留
EDITION_RE = re.compile(
    r"\b(dvd|blu\s*-?\s*ray|vhs|uhd|4k|widescreen|full\s*screen|special\s+edition|"
    r"collector'?s\s+edition|director'?s\s+cut|unrated|restored|remastered|anniversary|"
    r"criterion|import|region\s*\d|ntsc|pal)\b", re.I)
BRACKET_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
PUNCT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff ]+")


def norm_title(t: str) -> str:
    s = (t or "").lower()
    s = BRACKET_RE.sub(" ", s)
    s = EDITION_RE.sub(" ", s)
    s = PUNCT_RE.sub(" ", s)
    return " ".join(s.split())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--cls", default=CLS)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)

    cls = {}
    with io.open(args.cls, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["classification"] in WORKS:
                cls[r["asin"]] = r

    recs = {}
    for name in os.listdir(args.staging):
        if not name.endswith(".json"):
            continue
        with io.open(os.path.join(args.staging, name), encoding="utf-8") as fh:
            r = json.load(fh)
        a = r.get("asin")
        if a in cls:
            recs[a] = r

    groups = defaultdict(list)
    for a, r in recs.items():
        groups[norm_title(r.get("title_raw"))].append(a)

    multi = {k: v for k, v in groups.items() if len(v) > 1}
    print("works_loaded      = %d" % len(recs))
    print("title_groups      = %d" % len(groups))
    print("multi_version_groups = %d" % len(multi))
    print("products_in_multi = %d" % sum(len(v) for v in multi.values()))
    sizes = Counter(len(v) for v in groups.values())
    print("group_size_dist   = %s" % dict(sorted(sizes.items())))

    def actors(r):
        return {x.strip().lower() for x in (r.get("actors_raw") or "").split(",") if x.strip()}

    rows = []
    same_dir = diff_dir = 0
    runtime_close = runtime_far = 0
    for key, asins in sorted(multi.items(), key=lambda kv: -len(kv[1])):
        asins = sorted(asins)
        dirs = {(recs[a].get("people_raw") and
                 tuple(sorted(p["name_raw"] for p in recs[a]["people_raw"] if p.get("role") == "director"))
                 or json.dumps([])) for a in asins}
        dirs = {d for d in dirs}
        if len(dirs) == 1 and dirs != {json.dumps([])} and list(dirs)[0]:
            same_dir += 1
        else:
            diff_dir += 1
        rts = [recs[a].get("runtime_minutes") for a in asins if recs[a].get("runtime_minutes")]
        if len(rts) >= 2:
            if max(rts) - min(rts) <= 10:
                runtime_close += 1
            else:
                runtime_far += 1
        for a in asins:
            r = recs[a]
            rows.append([key, a, r.get("title_raw", ""), cls[a]["classification"],
                         r.get("media_format") or "", r.get("studio") or "",
                         r.get("runtime_minutes") or "",
                         len(actors(r)), (r.get("snapshot_path") or ""),
                         ""])

    print("同导演组          = %d" % same_dir)
    print("导演不一致组      = %d" % diff_dir)
    print("片长接近(<=10min) = %d" % runtime_close)
    print("片长差异大        = %d" % runtime_far)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with io.open(args.out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["norm_title", "asin", "title_raw", "classification", "media_format",
                    "studio", "runtime_minutes", "actors_n", "snapshot_path", "人工结论(待填)"])
        w.writerows(rows)
    print("candidates_csv    = %s (%d 行)" % (args.out, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
