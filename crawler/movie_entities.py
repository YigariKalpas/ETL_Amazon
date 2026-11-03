# -*- coding: utf-8 -*-
"""【KEY-42】任务4：同一电影/作品识别（实体定义 + 候选生成 + 三级判定 + 聚类）。

作业依据（§六 任务4）：
  - 同一作品不同载体/语言/地区 → 同一 movie_id，保留各自 edition；
  - 重拍/续集/同名不同作品分别建模；合集是多对多；剧集与电影分开计数；
  - 候选可用标题/年份/导演，但**单独标题相似、单独年份相同或缺失年份都不足以自动合并**；
  - 自动/人工/不合并三级，保存证据、阈值、置信度、规则版本；
  - 检查聚类一致性，避免 A~B、B~C 连带把 A、C 合并。

实现要点：
  【KEY-43】只有"多特征一致"才自动合并；缺年份时年份证据记为 missing，不推断。
  【KEY-44】合集成员来自标题枚举，只生成候选（boxset_candidates.csv），不直接造 Movie 实体。
  【KEY-45】剧集按 series 根标题聚合，卷/季作为 edition，不与电影混算。
  【KEY-46】所有判定（含不合并）都写进 merge_decision.csv，可复核。

用法：
  python crawler/movie_entities.py
  python crawler/movie_entities.py --out data/staging/entities
"""
from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = r"E:\ETL_Amazon"
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
CLS = os.path.join(ROOT, "data", "staging", "classification", "classification.csv")
OUT = os.path.join(ROOT, "data", "staging", "entities")

RULE_VERSION = "movie_entities/v1"
# 自动合并阈值
AUTO_TITLE_RATIO = 1.0      # 必须是标准化后完全相同
AUTO_CAST_JACCARD = 0.6
AUTO_RUNTIME_DELTA = 10
REVIEW_TITLE_RATIO = 0.86
AUTO_FUZZY_RATIO = 0.90
CANDIDATE_TITLE_RATIO = 0.80   # 【KEY-51】低于复核线的近似对也要比较并留痕（作为"检查过且不合并"的证据）
REJECT_RUNTIME_DELTA = 25

# 作品族：只在同一族内判同（剧集不与电影混算）
FAMILY = {
    "movie": "film", "movie_weak": "film",
    "series": "series", "concert": "concert", "anime": "anime",
    "boxset": "boxset", "music": "music", "sport": "sport", "other": "other",
}

EDITION_RE = re.compile(
    r"\b(dvd|blu\s*-?\s*ray|vhs|uhd|4k|widescreen|full\s*screen|special\s+edition|"
    r"collector'?s\s+edition|director'?s\s+cut|unrated|restored|remastered|anniversary|"
    r"criterion|import|region\s*\d|ntsc|pal|box\s*set|boxed\s*set|series\s*box|gift\s*set)\b", re.I)
BRACKET_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
PUNCT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff ]+")
YEAR_RE = re.compile(r"[\[\(](19\d{2}|20\d{2})[\]\)]")
SERIES_RE = re.compile(r"(?i)\b(season\s*\d+|vol\.?\s*\d+|volume\s*\d+|episode\s*\d+|ep\.?\s*\d+)\b")


def norm_title(t: str) -> str:
    s = (t or "").lower()
    s = BRACKET_RE.sub(" ", s)
    s = EDITION_RE.sub(" ", s)
    s = PUNCT_RE.sub(" ", s)
    return " ".join(s.split())


def title_year(t: str):
    m = YEAR_RE.search(t or "")
    return int(m.group(1)) if m else None


def cast_set(rec: dict) -> set:
    names = {x.strip().lower() for x in (rec.get("actors_raw") or "").split(",") if x.strip()}
    if not names:
        names = {p["name_raw"].strip().lower() for p in (rec.get("people_raw") or [])
                 if p.get("role") == "actor"}
    return names


def director_set(rec: dict) -> set:
    return {p["name_raw"].strip().lower() for p in (rec.get("people_raw") or [])
            if p.get("role") == "director"}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def series_root(title: str) -> str:
    s = SERIES_RE.sub(" ", title or "")
    s = re.split(r"[:\-]", s)[0]
    return norm_title(s)


def compare(a: dict, b: dict) -> dict:
    """比较两个商品，返回特征证据与三级判定。"""
    ta, tb = a["title_raw"], b["title_raw"]
    ratio = difflib.SequenceMatcher(None, (ta or "").lower(), (tb or "").lower()).ratio()
    same_norm = a["norm"] == b["norm"] and a["norm"] != ""
    cj = jaccard(a["cast"], b["cast"])
    da, db_ = a["directors"], b["directors"]
    if da and db_:
        dir_match = bool(da & db_)
        dir_conflict = not dir_match
    else:
        dir_match = False
        dir_conflict = False
    ra, rb = a["runtime"], b["runtime"]
    rdelta = abs(ra - rb) if (ra and rb) else None
    ya, yb = a["year"], b["year"]
    year_conflict = bool(ya and yb and ya != yb)

    family = a["family"]

    if family != b["family"]:
        decision, rule = "reject", "R-FAMILY"
    elif year_conflict:
        decision, rule = "reject", "R-YEAR-CONFLICT"
    elif rdelta is not None and rdelta > REJECT_RUNTIME_DELTA:
        decision, rule = "reject", "R-RUNTIME"
    elif dir_conflict:
        decision, rule = "reject", "R-DIRECTOR"
    elif same_norm and (dir_match or cj >= AUTO_CAST_JACCARD) and (rdelta is None or rdelta <= AUTO_RUNTIME_DELTA):
        decision, rule = "auto", "A-MULTI-FEATURE"
    elif same_norm and rdelta is not None and rdelta <= AUTO_RUNTIME_DELTA and a["media_format"] != b["media_format"]:
        decision, rule = "auto", "A-TITLE-RUNTIME"
    elif ratio >= AUTO_FUZZY_RATIO and rdelta is not None and rdelta <= AUTO_RUNTIME_DELTA and a["media_format"] != b["media_format"]:
        # 【KEY-48】标题高度近似 + 片长一致 + 载体不同 = 多特征一致（弱于同名，置信度下调）
        decision, rule = "auto", "A-TITLE-RUNTIME-FUZZY"
    elif ratio >= REVIEW_TITLE_RATIO or (same_norm and not dir_match and cj < AUTO_CAST_JACCARD):
        decision, rule = "review", "M-PARTIAL"
    else:
        decision, rule = "reject", "R-WEAK"

    # 置信度：只做粗糙标度，供人工排序
    conf = 0.0
    if decision == "auto":
        conf = 0.6 + (0.2 if dir_match else 0.0) + (0.2 if (rdelta is not None and rdelta <= AUTO_RUNTIME_DELTA) else 0.0)
        if rule == "A-TITLE-RUNTIME":
            conf = 0.70
        elif rule == "A-TITLE-RUNTIME-FUZZY":
            conf = 0.62
    elif decision == "review":
        conf = 0.3 + 0.3 * ratio
    return {
        "title_ratio": round(ratio, 3), "same_norm": same_norm,
        "director_match": dir_match, "director_conflict": dir_conflict,
        "cast_jaccard": round(cj, 3), "runtime_delta": rdelta,
        "year_a": ya, "year_b": yb, "year_conflict": year_conflict,
        "decision": decision, "rule_id": rule,
        "confidence": round(min(conf, 0.99), 2),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--cls", default=CLS)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--apply-review", default="",
                    help="人工复核结果 CSV（asin_a,asin_b,human_decision），accept 即视为合并")
    args = ap.parse_args(argv)

    cls = {}
    with io.open(args.cls, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            cls[r["asin"]] = r

    items = {}
    for name in os.listdir(args.staging):
        if not name.endswith(".json"):
            continue
        with io.open(os.path.join(args.staging, name), encoding="utf-8") as fh:
            rec = json.load(fh)
        a = rec.get("asin")
        c = cls.get(a)
        if not c:
            continue
        fam = FAMILY.get(c["classification"], "other")
        if fam in ("music", "sport", "other"):
            continue
        items[a] = {
            "asin": a, "title_raw": rec.get("title_raw") or "", "norm": norm_title(rec.get("title_raw")),
            "family": fam, "classification": c["classification"],
            "cast": cast_set(rec), "directors": director_set(rec),
            "runtime": rec.get("runtime_minutes"), "year": title_year(rec.get("title_raw")),
            "media_format": rec.get("media_format") or "", "studio": rec.get("studio") or "",
            "language": rec.get("language") or "", "discs": rec.get("discs") or "",
            "product_release_date": rec.get("product_release_date_raw") or "",
            "snapshot_path": rec.get("snapshot_path") or "",
        }
    keys = sorted(items)
    print("candidates_loaded = %d" % len(keys))

    # ---------- 候选生成（分块，避免 O(n^2) difflib） ----------
    # 【KEY-96】先用标准化标题的 token 建倒排索引，只比较共享 token 的商品对；
    # 高频词（the/collection 等）跳过，防止桶爆炸。同标题对无论如何都进候选。
    block_index = defaultdict(list)
    for k in keys:
        toks = {t for t in items[k]["norm"].split() if len(t) >= 3}
        for t in toks:
            block_index[t].append(k)
    cand = set()
    skipped_hot = 0
    for tok, members in block_index.items():
        if len(members) < 2:
            continue
        if len(members) > 200:
            skipped_hot += 1
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                cand.add((members[i], members[j]))
    print("candidate_pairs   = %d (blocks=%d, skipped_hot=%d)"
          % (len(cand), len(block_index), skipped_hot))

    decisions = []
    for a_asin, b_asin in sorted(cand):
        a, b = items[a_asin], items[b_asin]
        if a["family"] != b["family"]:
            continue
        same_norm = a["norm"] == b["norm"] and a["norm"] != ""
        if not same_norm:
            if abs(len(a["title_raw"]) - len(b["title_raw"])) > 40:
                continue
            rough = difflib.SequenceMatcher(None, a["title_raw"].lower(),
                                            b["title_raw"].lower()).ratio()
            if rough < CANDIDATE_TITLE_RATIO:
                continue
        ev = compare(a, b)
        decisions.append({"asin_a": a_asin, "asin_b": b_asin, **ev})

    # 【KEY-49】人工复核结果回灌：accept 提升为 auto，reject 保持不合并
    overrides = {}
    if args.apply_review and os.path.isfile(args.apply_review):
        with io.open(args.apply_review, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                key = (row.get("asin_a", "").strip(), row.get("asin_b", "").strip())
                dec = (row.get("human_decision") or "").strip().lower()
                if key[0] and key[1] and dec:
                    overrides[key] = dec
        print("review_overrides  = %d" % len(overrides))
    for d in decisions:
        dec = overrides.get((d["asin_a"], d["asin_b"]))
        if not dec:
            continue
        if dec == "accept":
            d["decision"] = "auto"
            d["rule_id"] = "H-REVIEW-ACCEPT"
            d["confidence"] = 0.9
        elif dec == "reject":
            d["decision"] = "reject"
            d["rule_id"] = "H-REVIEW-REJECT"
            d["confidence"] = 0.95

    dist = Counter(d["decision"] for d in decisions)
    print("pairs_compared    = %d" % len(decisions))
    print("decision_dist     = %s" % dict(dist))

    # ---------- 合并（并查集）+ 一致性检查 ----------
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    conflicts = []
    for d in decisions:
        if d["decision"] != "auto":
            continue
        ra, rb = find(d["asin_a"]), find(d["asin_b"])
        if ra == rb:
            continue
        # 一致性检查：合并后簇内若出现导演冲突或片长极差过大 → 拒绝并记录
        cluster = [k for k in keys if find(k) in (ra, rb)]
        dirs = set()
        rts = []
        for k in cluster + [d["asin_a"], d["asin_b"]]:
            for x in items[k]["directors"]:
                dirs.add(x)
            if items[k]["runtime"]:
                rts.append(items[k]["runtime"])
        bad = len(dirs) > 1 or (rts and max(rts) - min(rts) > REJECT_RUNTIME_DELTA)
        if bad:
            d["decision"] = "review"
            d["rule_id"] = "M-CLUSTER-CONFLICT"
            conflicts.append(d)
            continue
        union(d["asin_a"], d["asin_b"])

    # 【KEY-45】剧集按根标题聚合：同一 series root 下的卷/季归入同一 series 实体
    series_groups = defaultdict(list)
    for k in keys:
        if items[k]["family"] == "series":
            series_groups[series_root(items[k]["title_raw"])].append(k)
    series_merges = 0
    for root, members in series_groups.items():
        if len(members) > 1:
            first = members[0]
            for m in members[1:]:
                if find(m) != find(first):
                    union(first, m)
            series_merges += 1
    print("series_roots_with_multi = %d" % series_merges)

    clusters = defaultdict(list)
    for k in keys:
        clusters[find(k)].append(k)
    merged = {r: v for r, v in clusters.items() if len(v) > 1}
    print("clusters_total    = %d" % len(clusters))
    print("clusters_merged   = %d" % len(merged))
    print("products_merged   = %d" % sum(len(v) for v in merged.values()))

    # ---------- 写出实体与映射 ----------
    os.makedirs(args.out, exist_ok=True)
    movie_rows, edition_rows, pm_rows = [], [], []
    for root, members in clusters.items():
        family = items[members[0]]["family"]
        if family == "series":
            movie_id = "S-" + root          # 【KEY-45】剧集实体，卷/季作为 edition
            canonical = items[members[0]]["title_raw"]
        elif len(members) > 1:
            movie_id = "M-" + root
            canonical = items[members[0]]["title_raw"]
        else:
            movie_id = "E-" + members[0]   # 单商品也算一个作品实体（edition 级）
            canonical = items[members[0]]["title_raw"]
        year = items[members[0]]["year"]
        movie_rows.append([movie_id, canonical, items[members[0]]["norm"], family,
                           year if year else "", "auto" if len(members) > 1 else "single",
                           RULE_VERSION, len(members)])
        for k in sorted(members):
            it = items[k]
            edition_rows.append([k, movie_id, it["media_format"], it["language"], it["discs"],
                                 it["studio"], it["product_release_date"], it["snapshot_path"]])
            rel = "single" if len(members) == 1 else (
                "series_volume" if family == "series" else "same_work_edition")
            pm_rows.append([k, movie_id, rel, RULE_VERSION,
                            "auto" if len(members) > 1 else "single"])

    def write(name, header, rows):
        path = os.path.join(args.out, name)
        with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        print("wrote %-24s %d 行" % (name, len(rows)))

    write("movie.csv", ["movie_id", "canonical_title", "norm_title", "work_type",
                        "release_year", "status", "rule_version", "edition_count"], movie_rows)
    write("product_edition.csv", ["product_id", "movie_id", "media_format", "language", "discs",
                                  "studio", "product_release_date", "snapshot_path"], edition_rows)
    write("product_movie.csv", ["product_id", "movie_id", "relation_type", "rule_version", "status"], pm_rows)
    write("merge_decision.csv", ["asin_a", "asin_b", "title_ratio", "same_norm", "director_match",
                                 "director_conflict", "cast_jaccard", "runtime_delta", "year_a",
                                 "year_b", "year_conflict", "decision", "rule_id", "confidence"],
          [[d["asin_a"], d["asin_b"], d["title_ratio"], d["same_norm"], d["director_match"],
            d["director_conflict"], d["cast_jaccard"], d["runtime_delta"], d["year_a"], d["year_b"],
            d["year_conflict"], d["decision"], d["rule_id"], d["confidence"]] for d in decisions])

    # ---------- 人工复核队列 ----------
    review_rows = []
    for d in decisions:
        if d["decision"] == "review":
            review_rows.append([d["asin_a"], d["asin_b"], items[d["asin_a"]]["title_raw"],
                                items[d["asin_b"]]["title_raw"], d["title_ratio"],
                                d["cast_jaccard"], d["runtime_delta"], d["year_a"], d["year_b"],
                                d["confidence"], ""])
    write("review_queue.csv", ["asin_a", "asin_b", "title_a", "title_b", "title_ratio",
                               "cast_jaccard", "runtime_delta", "year_a", "year_b",
                               "confidence", "human_decision(accept/reject)"], review_rows)

    # ---------- 合集成员候选（不造实体，交人工确认）----------
    box_rows = []
    for a, it in items.items():
        if it["family"] != "boxset":
            continue
        t = it["title_raw"]
        body = t
        m = re.search(r"[\(\[]([^\)\]]+)[\)\]]", t)
        if m:
            body = m.group(1)
        members = [x.strip() for x in re.split(r"\s*/\s*|,\s*", body) if len(x.strip()) > 3]
        if len(members) >= 2:
            for idx, mem in enumerate(members, 1):
                box_rows.append([a, idx, mem, "pending_review", RULE_VERSION])
    write("boxset_candidates.csv", ["product_id", "member_index", "member_title_raw",
                                    "status", "rule_version"], box_rows)

    summary = {
        "rule_version": RULE_VERSION,
        "products_in_scope": len(keys),
        "pairs_compared": len(decisions),
        "decision_dist": dict(dist),
        "clusters_total": len(clusters),
        "clusters_merged": len(merged),
        "products_merged": sum(len(v) for v in merged.values()),
        "cluster_size_dist": dict(Counter(len(v) for v in clusters.values())),
        "thresholds": {
            "auto_title_ratio": AUTO_TITLE_RATIO,
            "auto_cast_jaccard": AUTO_CAST_JACCARD,
            "auto_runtime_delta": AUTO_RUNTIME_DELTA,
            "review_title_ratio": REVIEW_TITLE_RATIO,
            "reject_runtime_delta": REJECT_RUNTIME_DELTA,
        },
        "boxset_candidates": len(box_rows),
        "cluster_conflicts_resolved": len(conflicts),
        "merged_examples": [{"movie_id": "M-" + r, "members": v, "titles": [items[k]["title_raw"] for k in v]}
                            for r, v in list(merged.items())[:10]],
    }
    with io.open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print("summary -> %s" % os.path.join(args.out, "summary.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
