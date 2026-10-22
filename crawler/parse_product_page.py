#!/usr/bin/env python3
"""从已保存的 Amazon 产品页快照中抽取结构化字段（暂存层 / staging）。

设计原则（对应作业 §五 与 §九）：
  1) 只读原始层快照，产出独立 JSON，绝不改写原始证据；
  2) 每个字段同时保留 raw_value 与 extraction_rule，便于字段级血缘；
  3) 严格区分「商品发售日期」与「作品上映日期」——产品页给的是前者；
  4) 抽不到的字段留 null，不猜、不补。

用法:
    python parse_product_page.py            # 解析所有 success 快照
    python parse_product_page.py --all      # 连非 success 有快照的也解析
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

DB = r"E:\ETL_Amazon\data\crawler\products.sqlite"
OUT_DIR = r"E:\ETL_Amazon\data\staging\product_pages"

RE_TITLE = re.compile(r'<span[^>]*id="productTitle"[^>]*>(.*?)</span>', re.S | re.I)
RE_BYLINE = re.compile(r'id="bylineInfo"[^>]*>(.*?)</div>', re.S | re.I)
RE_CRUMB_BLOCK = re.compile(r'id="wayfinding-breadcrumbs_feature_div"(.*?)</ul>', re.S | re.I)
RE_CRUMB_A = re.compile(r"<a[^>]*>(.*?)</a>", re.S | re.I)
RE_TR = re.compile(r"<tr[^>]*>\s*<t[hd][^>]*>(.*?)</t[hd]>\s*<td[^>]*>(.*?)</td>", re.S | re.I)
RE_LI_BLOCK = re.compile(r'id="detailBullets_feature_div"(.*?)</div>\s*</div>', re.S | re.I)
RE_LI = re.compile(r"<li[^>]*>(.*?)</li>", re.S | re.I)
RE_REVIEWS = re.compile(r'id="acrCustomerReviewText"[^>]*>(.*?)</span>', re.S | re.I)
# 【补充】byline_info 里含 "Name (Actor) / Name (Director)"；多为截断显示（& N more）
RE_PERSON = re.compile(
    r"([A-Z][^,()]{1,60}?)\s*\((Actor|Actress|Director|Producer|Writer|Host|Narrator)\)", re.I
)
RE_MORE = re.compile(r"&\s*(\d+)\s*more", re.I)
RE_RATING = re.compile(r'<span[^>]*class="a-icon-alt"[^>]*>(.*?)</span>', re.S | re.I)

# 标题里的版本/载体后缀：必须剥离才能做匹配，但原始标题必须保留
RE_TITLE_SUFFIX = re.compile(
    r"\s*[\(\[]\s*(VHS|DVD|Blu-?ray|Import|All Regions|Region\s*\d|Widescreen|Full Screen|"
    r"Collector'?s Edition|Special Edition|Remastered|NTSC|PAL)[^\)\]]*[\)\]]\s*$",
    re.I,
)

# 只保留有意义的详情键
KEEP_KEYS = {
    "release date", "studio", "actors", "genre", "format", "media format",
    "language", "runtime", "run time", "number of discs", "mpaa rating",
    "package dimensions", "contributor", "director", "producer",
}


def clean(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    for a, b in (("&amp;", "&"), ("&#39;", "'"), ("&quot;", '"'), ("&nbsp;", " "),
                 ("&mdash;", "-"), ("&rlm;", ""), ("&lrm;", ""), ("&rsquo;", "'")):
        s = s.replace(a, b)
    s = s.replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"\s+", " ", s).strip()


def norm_title(t: str) -> str:
    t2 = RE_TITLE_SUFFIX.sub("", t).strip()
    t2 = re.sub(r"\s*[-:]+\s*$", "", t2)
    return re.sub(r"\s+", " ", t2)


def parse_runtime(text: str):
    if not text:
        return None
    h = re.search(r"(\d+)\s*hour", text, re.I)
    m = re.search(r"(\d+)\s*minute", text, re.I)
    total = 0
    if h:
        total += int(h.group(1)) * 60
    if m:
        total += int(m.group(1))
    return total or None


def parse_detail(txt: str) -> dict:
    out: dict[str, str] = {}

    for k, v in RE_TR.findall(txt):
        kk, vv = clean(k), clean(v)
        if kk.lower() in KEEP_KEYS and vv and vv != "-":
            out.setdefault(kk.lower(), vv)

    m = RE_LI_BLOCK.search(txt)
    if m:
        for it in RE_LI.findall(m.group(1)):
            t = clean(it)
            if ":" not in t:
                continue
            k, v = t.split(":", 1)
            k, v = k.strip().lower(), v.strip()
            if k in KEEP_KEYS and v:
                out.setdefault(k, v)
    return out


def parse_people(byline: str):
    """从 byline_info 抽取演职员。注意：页面多为截断显示，需记录隐藏人数。"""
    people = []
    for name, role in RE_PERSON.findall(byline or ""):
        people.append({"name_raw": name.strip(), "role": role.lower(), "source": "bylineInfo"})
    m = RE_MORE.search(byline or "")
    return people, (int(m.group(1)) if m else 0)


def parse_snapshot(path: str, asin: str) -> dict:
    with gzip.open(path, "rb") as fh:
        raw = fh.read()
    txt = raw.decode("utf-8", "replace")

    title_raw = clean(RE_TITLE.search(txt).group(1)) if RE_TITLE.search(txt) else None
    byline = clean(RE_BYLINE.search(txt).group(1)) if RE_BYLINE.search(txt) else None

    crumbs: list[str] = []
    m = RE_CRUMB_BLOCK.search(txt)
    if m:
        crumbs = [c for c in (clean(x) for x in RE_CRUMB_A.findall(m.group(1))) if c]

    detail = parse_detail(txt)
    reviews = RE_REVIEWS.search(txt)
    rating = RE_RATING.search(txt)

    rec = {
        "asin": asin,
        "source_url": "https://www.amazon.com/dp/" + asin,
        "snapshot_path": path,
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title_raw": title_raw,
        "title_normalized": norm_title(title_raw) if title_raw else None,
        "byline_info": byline,
        "breadcrumb_categories": crumbs,
        "detail": detail,
        # 作品 vs 版本：产品页只给「商品发售日」，绝不可当上映日
        "product_release_date_raw": detail.get("release date"),
        "studio": detail.get("studio"),
        "actors_raw": detail.get("actors"),
        "people_raw": parse_people(byline)[0],
        "byline_truncated_more": parse_people(byline)[1],
        "genre_labels_raw": [detail.get("genre")] if detail.get("genre") else [],
        "media_format": detail.get("media format") or detail.get("format"),
        "language": detail.get("language"),
        "runtime_minutes": parse_runtime(detail.get("runtime") or detail.get("run time")),
        "discs": detail.get("number of discs"),
        "mpaa_rating": detail.get("mpaa rating"),
        "review_count_raw": clean(reviews.group(1)) if reviews else None,
        "rating_raw": clean(rating.group(1)) if rating else None,
        # 抽取规则版本，供字段级血缘引用
        "rule_version": "parse_product_page/v1",
    }
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    conn = sqlite3.connect(args.db)
    where = "" if args.all else "WHERE status='success'"
    rows = list(conn.execute("SELECT asin, snapshot_path FROM product_page %s" % where))

    n = 0
    for asin, path in rows:
        if not path or not os.path.isfile(path):
            continue
        rec = parse_snapshot(path, asin)
        dst = os.path.join(args.out, asin + ".json")
        with io.open(dst, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=2)
        n += 1
        print("parsed", asin, "->", os.path.basename(dst))
    print("total_parsed =", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
