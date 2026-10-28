# -*- coding: utf-8 -*-
"""【KEY-75】进程与任务健康度汇总：谁在跑、在跑什么、是否还在出数据。

数据来源（都不需要 OS 级权限，规避沙箱 tasklist Access denied）：
  1) data/crawler/runtime/*.json —— 各守护进程自报的 PID/角色/参数；
  2) logs 最近修改时间 —— 判断是否仍在活动；
  3) 采集状态库 —— 最近一条 fetched_at，判断是否真的在抓；
  4) PostgreSQL —— 端口可用性与表数。

用法：
  python crawler/process_status.py
"""
from __future__ import annotations

import glob
import io
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

ROOT = r"E:\ETL_Amazon"
RUNTIME = os.path.join(ROOT, "data", "crawler", "runtime")
LOGS = os.path.join(ROOT, "logs")
DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
PSQL = os.path.join(ROOT, "tools", "pgsql", "bin", "psql.exe")


def age_sec(ts: float) -> float:
    return max(0.0, datetime.now().timestamp() - ts)


def main() -> int:
    print("=" * 70)
    print("运行标记（进程自报）")
    print("=" * 70)
    markers = sorted(glob.glob(os.path.join(RUNTIME, "*.json")))
    if not markers:
        print("  无标记文件（旧版本进程运行中，或全部已退出）")
    for p in markers:
        try:
            with io.open(p, encoding="utf-8") as fh:
                m = json.load(fh)
        except (OSError, ValueError):
            continue
        print("  %-8s pid=%-7s start=%-20s %s" % (
            m.get("role"), m.get("pid"), m.get("started_at"),
            {k: v for k, v in m.items() if k not in ("role", "pid", "started_at")}))

    print()
    print("=" * 70)
    print("日志活跃度（最近修改）")
    print("=" * 70)
    for pat, label in [(os.path.join(LOGS, "forever_loop.log"), "forever_loop"),
                       (os.path.join(LOGS, "wave_loop.log"), "wave_loop"),
                       (os.path.join(LOGS, "refresh.log"), "refresh(pipeline)"),
                       (os.path.join(LOGS, "purge.log"), "purge"),
                       (os.path.join(LOGS, "pipeline.log"), "pipeline")]:
        if os.path.isfile(pat):
            a = age_sec(os.path.getmtime(pat))
            print("  %-18s 最后写于 %6.0f 秒前" % (label, a))
    waves = sorted(glob.glob(os.path.join(LOGS, "wave_0*.log")), key=os.path.getmtime)
    if waves:
        last = waves[-1]
        print("  %-18s %s（%.0f 秒前）" % ("最新波次", os.path.basename(last), age_sec(os.path.getmtime(last))))

    print()
    print("=" * 70)
    print("采集是否真的在出数据")
    print("=" * 70)
    if os.path.isfile(DB):
        conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        last = conn.execute("SELECT MAX(fetched_at) FROM product_page").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM product_page").fetchone()[0]
        ok = conn.execute("SELECT COUNT(*) FROM product_page WHERE status='success'").fetchone()[0]
        conn.close()
        print("  状态行 = %d，success = %d" % (n, ok))
        print("  最近抓取时间 = %s" % last)
        if last:
            t = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            lag = (datetime.now(timezone.utc) - t).total_seconds()
            print("  距今 %.0f 秒（%s）" % (lag, "活跃" if lag < 300 else "可能已停止或正在冷却"))

    print()
    print("=" * 70)
    print("PostgreSQL")
    print("=" * 70)
    env = dict(os.environ)
    env.update({"PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": "etl",
                "PGDATABASE": "etl_amazon", "PGOPTIONS": "-c statement_timeout=10000"})
    try:
        r = subprocess.run([PSQL, "-t", "-A", "-c",
                            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'"],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        out = r.stdout.decode("utf-8", "replace").strip()
        print("  可用：表数 = %s" % out if r.returncode == 0 else "  不可用：%s" % out[:120])
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("  不可用：%s" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
