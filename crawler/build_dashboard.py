# -*- coding: utf-8 -*-
"""生成单文件 HTML 控制台：采集/ETL 全链路观察 + 人工验收入口。

特点：
  - 单文件、无外部依赖、无服务端，双击即可用浏览器打开；
  - 数据来自本地库与日志，每次运行重新生成；
  - 只读，不修改任何业务数据。

用法：
  python crawler/build_dashboard.py
  python crawler/build_dashboard.py --out dashboard/index.html --refresh 30
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

ROOT = r"E:\\ETL_Amazon"
CRAWL_DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
TARGET_DB = os.path.join(ROOT, "data", "target", "target.db")
PAGES = os.path.join(ROOT, "data", "raw", "pages")
STAGING = os.path.join(ROOT, "data", "staging", "product_pages")
LOGS = os.path.join(ROOT, "logs")
AUDIT = os.path.join(ROOT, "data", "staging", "audit_sample.csv")
QUEUE = os.path.join(ROOT, "data", "staging", "entities", "review_queue.csv")
HISTORY = os.path.join(ROOT, "data", "staging", "schedule_history.csv")
PSQL = os.path.join(ROOT, "tools", "pgsql", "bin", "psql.exe")
OUT = os.path.join(ROOT, "dashboard", "index.html")
TOTAL_PRODUCTS = 253059


def q(db, sql):
    if not os.path.isfile(db):
        return None
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        return conn.execute(sql).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def counts(db, tables):
    out = {}
    if not os.path.isfile(db):
        return out
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    for t in tables:
        try:
            out[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except sqlite3.Error:
            out[t] = 0
    conn.close()
    return out


def csv_meta(path, human_col_hint):
    if not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    filled = 0
    for r in rows:
        for k, v in r.items():
            if human_col_hint in (k or "") and (v or "").strip():
                filled += 1
                break
    return {"rows": len(rows), "filled": filled}


def pg_counts():
    env = dict(os.environ)
    env.update({"PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": "etl",
                "PGDATABASE": "etl_amazon",
                "PGOPTIONS": "-c statement_timeout=15000 -c max_parallel_workers_per_gather=0"})
    q2 = ("SELECT 'source_record', COUNT(*) FROM source_record UNION ALL "
          "SELECT 'movie', COUNT(*) FROM movie UNION ALL "
          "SELECT 'review', COUNT(*) FROM review")
    try:
        r = subprocess.run([PSQL, "-t", "-A", "-F", "|", "-c", q2], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        if r.returncode != 0:
            return {}
        out = {}
        for line in r.stdout.decode("utf-8", "replace").strip().splitlines():
            if "|" in line:
                k, v = line.split("|", 1)
                out[k] = int(v)
        return out
    except (OSError, subprocess.TimeoutExpired):
        return {}


def tail(path, n):
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        return [x.rstrip() for x in fh.readlines()[-n:]]


def card(label, value, sub=""):
    return ('<div class="card"><div class="k">%s</div><div class="v">%s</div>'
            '<div class="s">%s</div></div>' % (label, value, sub))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--refresh", type=int, default=0, help="秒；0 表示不自动刷新")
    args = ap.parse_args(argv)

    st = {}
    if os.path.isfile(CRAWL_DB):
        conn = sqlite3.connect("file:%s?mode=ro" % CRAWL_DB, uri=True)
        for k, v in conn.execute("SELECT status, COUNT(*) FROM product_page GROUP BY status"):
            st[k] = v
        conn.close()
    rows = sum(st.values())
    success = st.get("success", 0)
    last_fetch = q(CRAWL_DB, "SELECT MAX(fetched_at) FROM product_page")
    last_fetch = last_fetch[0] if last_fetch else "-"

    files = glob.glob(os.path.join(PAGES, "*", "*.html.gz"))
    size_mb = sum(os.path.getsize(f) for f in files) / 1024.0 / 1024.0 if files else 0
    staged = sum(1 for n in os.listdir(STAGING) if n.endswith(".json")) if os.path.isdir(STAGING) else 0

    t = counts(TARGET_DB, ["source_record", "movie", "product_edition", "review",
                           "review_mapping", "person", "movie_person",
                           "field_assertion", "field_selection", "release_date",
                           "merge_decision", "quality_issue"])
    pg = pg_counts()

    audit = csv_meta(AUDIT, "人工结论")
    queue = csv_meta(QUEUE, "human_decision")
    hist = tail(HISTORY, 6)
    sch = tail(os.path.join(LOGS, "scheduler.log"), 8)
    fore = tail(os.path.join(LOGS, "forever_loop.log"), 5)
    waves = sorted(glob.glob(os.path.join(LOGS, "wave_0*.log")), key=os.path.getmtime)
    wave_info = os.path.basename(waves[-1]) if waves else "-"

    gap_parse = success - staged
    gap_target = max(0, rows - t.get("source_record", 0))
    coverage = 100.0 * t.get("movie", 0) / TOTAL_PRODUCTS

    def bars():
        out = []
        for name, val, color in [
            ("success", success, "#4ade80"), ("not_found", st.get("not_found", 0), "#94a3b8"),
            ("parse_failed", st.get("parse_failed", 0), "#f59e0b"),
            ("validation", st.get("validation_page", 0), "#ef4444")]:
            pct = 100.0 * val / max(1, rows)
            out.append('<div class="bar"><span style="width:%.1f%%;background:%s"></span>'
                       '<em>%s %d（%.1f%%）</em></div>' % (pct, color, name, val, pct))
        return "".join(out)

    def stage(name, value, detail, ok=True):
        cls = "ok" if ok else "warn"
        return ('<div class="stage %s"><div class="sn">%s</div><div class="sv">%s</div>'
                '<div class="sd">%s</div></div>' % (cls, name, value, detail))

    html = []
    html.append("<!DOCTYPE html><html lang=zh-CN><head><meta charset=utf-8>")
    html.append("<title>ETL 全链路控制台</title>")
    if args.refresh:
        html.append('<meta http-equiv=refresh content="%d">' % args.refresh)
    html.append("""<style>
:root{--bg:#0b1220;--panel:#121a2b;--line:#1f2b45;--fg:#e2e8f0;--sub:#8aa0bf;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}
header{padding:20px 28px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
h1{font-size:20px;margin:0}.sub{color:var(--sub);font-size:12px}
main{padding:20px 28px;max-width:1280px;margin:0 auto}
section{margin-bottom:26px}h2{font-size:15px;color:#9fb3d1;margin:0 0 12px;font-weight:600;letter-spacing:.5px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .k{color:var(--sub);font-size:12px}.card .v{font-size:22px;font-weight:700;margin:4px 0}
.card .s{color:var(--sub);font-size:11px}
.bar{position:relative;background:#0f172a;border:1px solid var(--line);border-radius:6px;height:26px;margin:6px 0;overflow:hidden}
.bar span{position:absolute;left:0;top:0;bottom:0;opacity:.55}.bar em{position:relative;font-style:normal;font-size:12px;padding-left:8px;line-height:26px}
.stages{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.stage{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--ok);border-radius:8px;padding:10px 12px}
.stage.warn{border-left-color:var(--warn)}.stage .sn{color:var(--sub);font-size:12px}
.stage .sv{font-size:18px;font-weight:700}.stage .sd{color:var(--sub);font-size:11px}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);font-size:13px}
th{color:var(--sub);font-weight:600;background:#0f172a}
pre{background:#0f172a;border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;font-size:12px;color:#c7d2e0}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;border:1px solid var(--line)}
.pill.ok{color:var(--ok);border-color:#14532d}.pill.warn{color:var(--warn);border-color:#78350f}.pill.bad{color:var(--bad);border-color:#7f1d1d}
a{color:#7dd3fc;text-decoration:none}a:hover{text-decoration:underline}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:900px){.two{grid-template-columns:1fr}}
</style></head><body>""")
    html.append("<header><div><h1>Amazon 电影 ETL · 全链路控制台</h1>"
                '<div class="sub">生成于 %s ｜ 数据只读 ｜ 刷新页面即可更新</div></div>'
                '<div class="sub">%s</div></header>' % (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    ('自动刷新 %d 秒' % args.refresh) if args.refresh else '手动刷新'))
    html.append("<main>")

    html.append("<section><h2>关键指标</h2><div class=cards>")
    html.append(card("成功页面", success, "占全部产品 %.2f%%" % (100.0 * success / TOTAL_PRODUCTS)))
    html.append(card("状态行", rows, "not_found %d / parse_failed %d" % (st.get("not_found", 0), st.get("parse_failed", 0))))
    html.append(card("快照", len(files), "%.1f MB" % size_mb))
    html.append(card("作品实体", t.get("movie", 0), "识别率 %.2f%%" % coverage))
    html.append(card("评论入库", t.get("review", 0), "去重映射 %d" % t.get("review_mapping", 0)))
    html.append(card("字段断言", t.get("field_assertion", 0), "字段选择 %d" % t.get("field_selection", 0)))
    html.append("</div></section>")

    html.append("<section><h2>采集状态分布</h2>%s</section>" % bars())

    html.append("<section><h2>全链路阶段（从采集到目标层）</h2><div class=stages>")
    html.append(stage("① 采集", rows, "success %d · 最近 %s" % (success, last_fetch)))
    html.append(stage("② 快照", len(files), "%.1f MB / 256 桶" % size_mb, True))
    html.append(stage("③ 解析暂存", staged, "缺口 %d" % gap_parse, gap_parse <= 0))
    html.append(stage("④ 作品实体", t.get("movie", 0), "版本 %d" % t.get("product_edition", 0)))
    html.append(stage("⑤ 人物", t.get("person", 0), "关系 %d" % t.get("movie_person", 0)))
    html.append(stage("⑥ 血缘断言", t.get("field_assertion", 0), "选择 %d" % t.get("field_selection", 0)))
    html.append(stage("⑦ 评论去重", t.get("review_mapping", 0), "comments %d" % t.get("review", 0)))
    html.append(stage("⑧ 目标层", t.get("source_record", 0), "滞后 %d" % gap_target, gap_target == 0))
    html.append(stage("⑨ PostgreSQL", pg.get("source_record", "-"), "与 SQLite %s" % ("一致" if pg.get("source_record") == t.get("source_record") else "待同步/未启动"), bool(pg)))
    html.append("</div></section>")

    html.append("<section><h2>数据质量与一致性</h2><table><tr><th>检查</th><th>结果</th><th>说明</th></tr>")
    html.append("<tr><td>快照缺失 / 孤儿</td><td>见 landing_compare</td><td>crawler/landing_compare.py</td></tr>")
    html.append("<tr><td>success 与暂存差</td><td>%d</td><td>刷新滞后，跑 run_pipeline.py 收敛</td></tr>" % gap_parse)
    html.append("<tr><td>目标层滞后</td><td>%d</td><td>状态行与 source_record 之差</td></tr>" % gap_target)
    html.append("<tr><td>质量问题</td><td>%d</td><td>404 / 模板页 / 评论字段 / 冲突候选</td></tr>" % t.get("quality_issue", 0))
    html.append("<tr><td>合并判定</td><td>%d</td><td>auto / review / reject</td></tr>" % t.get("merge_decision", 0))
    html.append("</table></section>")

    html.append("<section><h2>人工验收入口</h2><div class=two>")
    if audit:
        html.append('<div class="card"><div class="k">抽样分类复核（audit_sample.csv）</div>'
                    '<div class="v">%d / %d</div><div class="s">已填 / 总数 ｜ 填 正确·分类错·字段错·无法判断</div>'
                    '<div class="s">文件：data/staging/audit_sample.csv</div></div>' % (audit["filled"], audit["rows"]))
    if queue:
        html.append('<div class="card"><div class="k">合并候选复核（review_queue.csv）</div>'
                    '<div class="v">%d / %d</div><div class="s">已填 / 总数 ｜ 填 accept 或 reject</div>'
                    '<div class="s">文件：data/staging/entities/review_queue.csv</div></div>' % (queue["filled"], queue["rows"]))
    html.append("<pre># 提交后回灌合并结果")
    html.append(".venv\\Scripts\\python.exe crawler\\movie_entities.py --apply-review data\\staging\\entities\\review_queue.csv")
    html.append("")
    html.append("# 自动复算（验收必跑）")
    html.append(".venv\\Scripts\\python.exe crawler\\landing_compare.py")
    html.append(".venv\\Scripts\\python.exe crawler\\verify_snapshots.py --manifest data\\crawler\\collect_manifest.json")
    html.append(".venv\\Scripts\\python.exe crawler\\trace_evidence.py")
    html.append(".venv\\Scripts\\python.exe crawler\\verify_target_db.py")
    html.append(".venv\\Scripts\\python.exe crawler\\deliverable_audit.py</pre>")

    hist_txt = chr(10).join(hist) if hist else "（暂无记录）"
    html.append("<section><h2>ETL 调度历史</h2><pre>%s</pre></section>" % hist_txt)
    sch_txt = chr(10).join(sch) or "-"
    fore_txt = chr(10).join(fore) or "-"
    html.append("<section><h2>运行日志（尾部）</h2><div class=two><div><h2>scheduler.log</h2><pre>%s</pre></div>"
                "<div><h2>forever_loop.log</h2><pre>%s</pre></div></div>"
                "<div class=sub>最新波次：%s</div></section>" % (sch_txt, fore_txt, wave_info))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with io.open(args.out, "w", encoding="utf-8") as fh:
        fh.write(chr(10).join(html))
    print("dashboard = %s" % args.out)
    print("rows=%d success=%d movie=%d review=%d assertion=%d pg=%s" % (
        rows, success, t.get("movie", 0), t.get("review", 0), t.get("field_assertion", 0),
        "up" if pg else "down"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
