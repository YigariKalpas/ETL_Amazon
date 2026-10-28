# -*- coding: utf-8 -*-
"""【KEY-72】外层常驻守护：wave_loop 退出后自动续下一轮，实现不停机连跑。

分工：
  forever_loop.py（本文件） —— 只管"什么时候重启、什么时候彻底停"
  wave_loop.py               —— 管"一波采多少、被限流了冷却多久"

停止条件（任一）：
  1) 出现 data/crawler/STOP（会先终止子进程再退出）
  2) 累计成功数达到 --target-success
  3) 运行超过 --max-days（0 = 不设上限）
  4) 连续 N 轮子进程异常退出（默认 3 轮，避免无限重启崩溃的脚本）

用法：
  python crawler/forever_loop.py --child-max-hours 8 --target-success 5000
  python crawler/forever_loop.py --max-days 3 --pause-between 120
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

ROOT = r"E:\ETL_Amazon"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
WAVE = os.path.join(ROOT, "crawler", "wave_loop.py")
DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
STOP = os.path.join(ROOT, "data", "crawler", "STOP")
LOGDIR = os.path.join(ROOT, "logs")


def log(msg: str) -> None:
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    os.makedirs(LOGDIR, exist_ok=True)
    with open(os.path.join(LOGDIR, "forever_loop.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


RUNTIME = os.path.join(ROOT, "data", "crawler", "runtime")


def write_marker(role: str, extra: dict) -> None:
    """【KEY-74】进程自报身份：PID + 角色 + 启动时间，供 process_status.py 汇总。"""
    os.makedirs(RUNTIME, exist_ok=True)
    payload = {"role": role, "pid": os.getpid(),
               "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    payload.update(extra)
    with open(os.path.join(RUNTIME, role + ".json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def clear_marker(role: str) -> None:
    try:
        os.remove(os.path.join(RUNTIME, role + ".json"))
    except OSError:
        pass


def success_count() -> int:
    if not os.path.isfile(DB):
        return 0
    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM product_page WHERE status='success'").fetchone()[0]
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="常驻采集守护")
    ap.add_argument("--child-max-hours", type=float, default=8.0,
                    help="每一轮 wave_loop 的运行上限（小时）")
    ap.add_argument("--pause-between", type=float, default=60.0,
                    help="两轮之间的间隔秒数")
    ap.add_argument("--target-success", type=int, default=5000)
    ap.add_argument("--max-days", type=float, default=0.0, help="0 = 不设上限")
    ap.add_argument("--max-failures", type=int, default=3)
    ap.add_argument("--delay", type=float, default=14.0)
    ap.add_argument("--sample-per-wave", type=int, default=100)
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    rounds = 0
    failures = 0
    write_marker("forever", {"target": args.target_success,
                             "child_max_hours": args.child_max_hours})
    log("forever start: target=%d childMax=%.1fh pause=%.0fs maxDays=%s"
        % (args.target_success, args.child_max_hours, args.pause_between,
           args.max_days or "inf"))

    while True:
        if os.path.isfile(STOP):
            log("STOP found -> exit")
            break
        if success_count() >= args.target_success:
            log("target reached: success=%d -> exit" % success_count())
            break
        if args.max_days and (time.monotonic() - t0) / 86400.0 >= args.max_days:
            log("max days reached -> exit")
            break
        if failures >= args.max_failures:
            log("child failed %d times in a row -> exit" % failures)
            break

        rounds += 1
        before = success_count()
        cmd = [PY, "-u", WAVE,
               "--delay", str(args.delay),
               "--sample-per-wave", str(args.sample_per_wave),
               "--target-success", str(args.target_success),
               "--max-hours", str(args.child_max_hours),
               "--cooldown-blocked", "3600", "--cooldown-cap", "14400",
               "--blocked-if-gain-lt", "30", "--max-blocked-waves", "6",
               "--abort-after-blocks", "2", "--refresh-every-waves", "5"]
        log("round %d start (success=%d)" % (rounds, before))
        with open(os.path.join(LOGDIR, "wave_loop_console.log"), "a", encoding="utf-8") as fh:
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
            while proc.poll() is None:
                if os.path.isfile(STOP):
                    log("STOP during round %d -> terminating child" % rounds)
                    proc.terminate()
                    try:
                        proc.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
                time.sleep(5)
        rc = proc.returncode
        after = success_count()
        gain = after - before
        log("round %d end: rc=%s gain=%d success=%d" % (rounds, rc, gain, after))

        if os.path.isfile(STOP):
            break
        if after >= args.target_success:
            continue
        # 子进程异常退出且毫无产出 → 记失败；有产出就重置
        if rc not in (0, None) and gain <= 0:
            failures += 1
        else:
            failures = 0
        if args.pause_between > 0:
            end = time.time() + args.pause_between
            while time.time() < end:
                if os.path.isfile(STOP):
                    break
                time.sleep(min(10, max(1, end - time.time())))

    clear_marker("forever")
    log("forever end: rounds=%d success=%d" % (rounds, success_count()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
