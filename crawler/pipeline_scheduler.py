# -*- coding: utf-8 -*-
"""ETL 定时调度：按固定间隔调用 PDI 作业 j_etl_pipeline.kjb。

设计：
  - 每次触发 = 一次完整 ETL（parse -> ... -> verify），失败不影响下一轮；
  - 单实例锁：data/crawler/scheduler.lock 记录 pid + 心跳，避免重复启动；
  - 每轮记录到 data/staging/schedule_history.csv（开始/结束/耗时/退出码）；
  - 支持 STOP 文件随时停止；--once 只跑一次（用于验收）。

用法：
  python crawler/pipeline_scheduler.py --interval-hours 4
  python crawler/pipeline_scheduler.py --once
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = r"E:\ETL_Amazon"
PDI = os.path.join(ROOT, "scripts", "pdi.cmd")
JOB = os.path.join(ROOT, "etl", "jobs", "j_etl_pipeline.kjb")
STOP = os.path.join(ROOT, "data", "crawler", "STOP")
LOCK = os.path.join(ROOT, "data", "crawler", "scheduler.lock")
HISTORY = os.path.join(ROOT, "data", "staging", "schedule_history.csv")
LOGDIR = os.path.join(ROOT, "logs")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    line = "[%s] %s" % (now(), msg)
    print(line, flush=True)
    os.makedirs(LOGDIR, exist_ok=True)
    with io.open(os.path.join(LOGDIR, "scheduler.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def acquire() -> bool:
    if os.path.isfile(LOCK):
        try:
            with io.open(LOCK, encoding="utf-8") as fh:
                info = json.load(fh)
            beat = float(info.get("heartbeat", 0))
            if time.time() - beat < 600:
                return False
        except (OSError, ValueError):
            pass
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    with io.open(LOCK, "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "started": now(), "heartbeat": time.time()}, fh)
    return True


def heartbeat() -> None:
    try:
        with io.open(LOCK, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "heartbeat": time.time()}, fh)
    except OSError:
        pass


def run_once(seq: int) -> tuple:
    started = time.time()
    ts = now()
    log("run %d start (PDI: %s)" % (seq, os.path.basename(JOB)))
    out = os.path.join(LOGDIR, "scheduler_pdi_%03d.log" % seq)
    with io.open(out, "w", encoding="utf-8") as fh:
        rc = subprocess.call(
            [PDI, "kitchen", "/file:" + JOB],
            cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    dur = time.time() - started
    log("run %d end rc=%d %.1fs log=%s" % (seq, rc, dur, os.path.basename(out)))
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    new = not os.path.isfile(HISTORY)
    with io.open(HISTORY, "a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["seq", "started_at", "finished_at", "duration_s", "exit_code", "log"])
        w.writerow([seq, ts, now(), round(dur, 1), rc, os.path.basename(out)])
    return rc, dur


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ETL 定时调度")
    ap.add_argument("--interval-hours", type=float, default=4.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--max-runs", type=int, default=0, help="0=不限")
    args = ap.parse_args(argv)

    if not acquire():
        log("another scheduler instance is alive -> exit")
        return 1
    log("scheduler start: interval=%.1fh once=%s" % (args.interval_hours, args.once))
    seq = 0
    try:
        while True:
            if os.path.isfile(STOP):
                log("STOP found -> exit")
                break
            seq += 1
            run_once(seq)
            if args.once or (args.max_runs and seq >= args.max_runs):
                break
            end = time.time() + args.interval_hours * 3600
            log("next run in %.1f h" % args.interval_hours)
            while time.time() < end:
                heartbeat()
                if os.path.isfile(STOP):
                    break
                time.sleep(min(30, max(1, end - time.time())))
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass
        log("scheduler end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
