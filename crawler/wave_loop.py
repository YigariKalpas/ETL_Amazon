#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【KEY-27】分波采集守护：波内自适应退避，波间自适应冷却。

为什么需要它：
  实测该出口按"请求预算"限制：一波大约放出 130~200 次请求，之后几乎全是验证页。
  单靠波内暂停无法恢复（限制持续数十分钟到数小时），必须波间冷却。
  本脚本用「本波新增成功数」判断波次质量，动态决定休息多久。

退出条件（任一）：
  1) 存在 data/crawler/STOP
  2) 达到 --target-success（累计成功数）
  3) 超过 --max-hours
  4) 连续 --max-blocked-waves 波几乎没有新增成功

合规：只降速/等待/停止，不绕过验证，不更换出口。
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
CRAWLER = os.path.join(ROOT, "crawler", "crawl_product_pages.py")
PURGE = os.path.join(ROOT, "crawler", "purge_invalid_snapshots.py")
PIPELINE = os.path.join(ROOT, "crawler", "run_pipeline.py")
RUNTIME = os.path.join(ROOT, "data", "crawler", "runtime")


def write_marker(role: str, extra: dict) -> None:
    """【KEY-74】进程自报身份，供 process_status.py 汇总。"""
    try:
        os.makedirs(RUNTIME, exist_ok=True)
        payload = {"role": role, "pid": os.getpid(),
                   "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        payload.update(extra)
        with open(os.path.join(RUNTIME, role + ".json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    except OSError:
        pass


def clear_marker(role: str) -> None:
    try:
        os.remove(os.path.join(RUNTIME, role + ".json"))
    except OSError:
        pass
DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
STOP = os.path.join(ROOT, "data", "crawler", "STOP")
LOGDIR = os.path.join(ROOT, "logs")


def log(msg: str) -> None:
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    os.makedirs(LOGDIR, exist_ok=True)
    with open(os.path.join(LOGDIR, "wave_loop.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def next_wave_index() -> int:
    """【KEY-30】按已有日志续编号，避免重启后覆盖上一次的波次日志（那是证据）。"""
    os.makedirs(LOGDIR, exist_ok=True)
    mx = 0
    for name in os.listdir(LOGDIR):
        if name.startswith("wave_") and name.endswith(".log"):
            try:
                mx = max(mx, int(name[5:8]))
            except ValueError:
                pass
    return mx + 1


def counts() -> dict:
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute("SELECT status, COUNT(*) FROM product_page GROUP BY status").fetchall()
    finally:
        conn.close()
    return {r[0]: r[1] for r in rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="分波采集守护")
    ap.add_argument("--delay", type=float, default=10.0)
    ap.add_argument("--jitter", type=float, default=2.0)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--sample-per-wave", type=int, default=120)
    ap.add_argument("--target-success", type=int, default=1000)
    ap.add_argument("--max-hours", type=float, default=8.0)
    ap.add_argument("--cooldown-ok", type=int, default=300, help="波次健康时休息秒数")
    ap.add_argument("--cooldown-blocked", type=int, default=1800, help="被限流时首次冷却秒数")
    ap.add_argument("--cooldown-cap", type=int, default=7200, help="冷却上限秒数")
    ap.add_argument("--blocked-if-gain-lt", type=int, default=40, help="新增成功少于该值即视为仍被限流")
    ap.add_argument("--max-blocked-waves", type=int, default=5)
    ap.add_argument("--delay-max", type=float, default=30.0,
                    help="被限流时逐波放大间隔，上限秒数（降低单位时间请求量）")
    ap.add_argument("--abort-after-blocks", type=int, default=2,
                    help="本波连续几轮窗口恶化就中止（越小越省预算）")
    ap.add_argument("--refresh-every-waves", type=int, default=5,
                    help="每 N 个波次刷新一次目标层（0=不刷新）")
    # 【KEY-93】自适应波次大小：上一波成功率低就少采，避免把预算耗在验证页上
    ap.add_argument("--adaptive", type=int, default=1, help="1=按上波成功率调整每波目标数")
    ap.add_argument("--sample-min", type=int, default=50)
    ap.add_argument("--sample-max", type=int, default=100)
    args = ap.parse_args(argv)

    t0 = time.monotonic()   # 【KEY-29】用单调时钟：机器休眠/改系统时间不应让 max-hours 提前结束
    wave = next_wave_index() - 1
    blocked_streak = 0
    cooldown = args.cooldown_blocked
    cur_delay = args.delay
    cur_sample = max(args.sample_min, min(args.sample_max, args.sample_per_wave))

    base = counts()
    log("start: success=%d target=%d delay=%.0fs sample/wave=%d maxHours=%.1f"
        % (base.get("success", 0), args.target_success, args.delay,
           args.sample_per_wave, args.max_hours))

    while True:
        if os.path.isfile(STOP):
            log("STOP found -> exit")
            break
        if (time.monotonic() - t0) / 3600.0 >= args.max_hours:
            log("max hours reached -> exit")
            break
        cur = counts()
        if cur.get("success", 0) >= args.target_success:
            log("target reached: success=%d -> exit" % cur["success"])
            break
        # 【KEY-94】开波前先探测窗口：被限流就跳过整波，不把预算耗在验证页上
        probe = subprocess.run([PY, CRAWLER, "--probe"], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if probe.returncode == 2:
            blocked_streak += 1
            cooldown = min(args.cooldown_blocked * (2 ** max(0, blocked_streak - 1)),
                           args.cooldown_cap)
            log("probe: window closed (streak=%d) -> cooldown %ds"
                % (blocked_streak, cooldown))
            end = time.monotonic() + cooldown
            while time.monotonic() < end and not os.path.isfile(STOP):
                time.sleep(min(30, max(1, end - time.monotonic())))
            continue

        if blocked_streak >= args.max_blocked_waves:
            log("blocked %d waves in a row -> exit" % blocked_streak)
            break

        wave += 1
        before = counts().get("success", 0)
        # 无效件（验证页）回填为待采集，避免它们占着状态位
        subprocess.run([PY, PURGE, "--apply"], capture_output=True)

        wave_log = os.path.join(LOGDIR, "wave_%03d.log" % wave)
        write_marker("wave", {"wave": wave, "delay": cur_delay, "target": args.target_success})
        log("wave %d start: success=%d pending_purged, delay=%.0fs" % (wave, before, cur_delay))
        with open(wave_log, "w", encoding="utf-8") as fh:
            subprocess.run(
                [PY, "-u", CRAWLER,
                 "--sample", str(cur_sample),
                 "--delay", str(cur_delay),
                 "--jitter", str(args.jitter),
                 "--concurrency", str(args.concurrency),
                 "--backoff-window", "10", "--backoff-bad", "4",
                 "--backoff-base", "600", "--backoff-factor", "2", "--backoff-cap", "1800",
                 "--abort-after-blocks", str(args.abort_after_blocks)],
                stdout=fh, stderr=subprocess.STDOUT)

        after = counts().get("success", 0)
        gain = after - before
        log("wave %d end: gain=%d success_total=%d (sample=%d)"
            % (wave, gain, after, cur_sample))

        if args.adaptive:
            rate = float(gain) / max(1, cur_sample)
            if rate < 0.40:
                new_sample = max(args.sample_min, int(cur_sample * 0.6))
            elif rate > 0.70:
                new_sample = min(args.sample_max, cur_sample + 20)
            else:
                new_sample = cur_sample
            if new_sample != cur_sample:
                log("sample %d -> %d (last wave success rate %.0f%%)"
                    % (cur_sample, new_sample, 100.0 * rate))
            cur_sample = new_sample

        if args.refresh_every_waves and wave % args.refresh_every_waves == 0:
            log("refresh target layer ...")
            with open(os.path.join(LOGDIR, "refresh.log"), "a", encoding="utf-8") as rf:
                rc = subprocess.run([PY, "-u", PIPELINE], cwd=ROOT, stdout=rf,
                                    stderr=subprocess.STDOUT).returncode
            log("refresh exit=%d" % rc)

        if gain >= args.blocked_if_gain_lt:
            blocked_streak = 0
            cooldown = args.cooldown_ok
            new_delay = max(args.delay, cur_delay * 0.8)
        else:
            blocked_streak += 1
            cooldown = min(args.cooldown_blocked * (2 ** (blocked_streak - 1)), args.cooldown_cap)
            # 【KEY-28】仍被限流：放大间隔（少发请求），下一波用更慢的节奏
            new_delay = min(cur_delay * 1.5, args.delay_max)
        if abs(new_delay - cur_delay) > 0.5:
            log("delay %.0fs -> %.0fs" % (cur_delay, new_delay))
        cur_delay = new_delay
        log("cooldown %d s (blocked_streak=%d)" % (cooldown, blocked_streak))

        end = time.monotonic() + cooldown
        while time.monotonic() < end:
            if os.path.isfile(STOP):
                break
            time.sleep(min(10, max(1, end - time.monotonic())))
        if os.path.isfile(STOP):
            log("STOP found during cooldown -> exit")
            break

    clear_marker("wave")
    log("end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
