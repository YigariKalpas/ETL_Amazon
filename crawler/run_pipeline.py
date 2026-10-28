# -*- coding: utf-8 -*-
"""【KEY-61】一键刷新流水线：解析 → 分类 → 实体 → 人物 → 血缘 → 上映/冲突 → 落盘 → 自检。

顺序（run_id 为每次执行的批次号）：
  1 parse_product_page.py        快照 -> 暂存 JSON
  2 classify_products.py         任务3 分类
  3 movie_entities.py            任务4 实体与合并判定
  4 person_entities.py           任务5 人物
  5 build_field_assertions.py    任务9 血缘断言
  6 release_dates.py             任务6 上映（含 inferred 上界）
  7 field_conflicts.py           任务7 字段选择/冲突
  8 build_target_db.py           落盘 SQLite（先建好 review 表）
  9 dedup_reviews.py             任务8 去重（读 review 表）
 10 build_target_db.py           再落盘一次，带上 review_mapping
 11 load_target_pg.py            同步 PostgreSQL
 12 verify_target_db.py          目标层自检

用法：
  python crawler/run_pipeline.py
  python crawler/run_pipeline.py --skip-pg      # 不同步 PG
  python crawler/run_pipeline.py --from 3       # 从第 3 步开始
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

ROOT = r"E:\ETL_Amazon"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
CRAWLER = os.path.join(ROOT, "crawler")
LOGDIR = os.path.join(ROOT, "logs")

STEPS = [
    ("parse", ["parse_product_page.py"]),
    ("classify", ["classify_products.py", "--rules", os.path.join(ROOT, "design", "产品分类规则_v0.4.json")]),
    ("entities", ["movie_entities.py"]),
    ("persons", ["person_entities.py"]),
    ("lineage", ["build_field_assertions.py"]),
    ("target1", ["build_target_db.py"]),
    ("release", ["release_dates.py"]),
    ("conflicts", ["field_conflicts.py"]),
    ("dedup", ["dedup_reviews.py"]),
    ("target2", ["build_target_db.py"]),
    ("pg", ["load_target_pg.py"]),
    ("verify", ["verify_target_db.py"]),
]


def main(argv=None) -> int:
    # 【KEY-68】子进程输出含中文/替换字符，Windows 控制台默认 GBK 会 UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=1, help="从第几步开始（1 起）")
    ap.add_argument("--skip-pg", action="store_true")
    args = ap.parse_args(argv)
    os.makedirs(LOGDIR, exist_ok=True)
    log_path = os.path.join(LOGDIR, "pipeline.log")
    t0 = time.time()
    failed = []

    for idx, (name, cmd) in enumerate(STEPS, start=1):
        if idx < args.start:
            continue
        if args.skip_pg and name == "pg":
            print("[%02d] %-10s SKIP (--skip-pg)" % (idx, name))
            continue
        started = time.time()
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("[%s] step %02d %s start\n" % (datetime.now().strftime("%H:%M:%S"), idx, name))
        r = subprocess.run([PY] + [os.path.join(CRAWLER, cmd[0])] + cmd[1:],
                           cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = r.stdout.decode("utf-8", "replace")
        tail = "\n".join(out.strip().splitlines()[-4:])
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print("[%02d] %-10s exit=%d  %.1fs" % (idx, name, r.returncode, time.time() - started))
        if tail:
            for line in tail.splitlines():
                print("      " + line)
        if r.returncode != 0:
            failed.append(name)
            break

    print("\nelapsed = %.1f s" % (time.time() - t0))
    if failed:
        print("FAILED at: %s（日志 %s）" % (", ".join(failed), log_path))
        return 1
    print("pipeline ok（日志 %s）" % log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
