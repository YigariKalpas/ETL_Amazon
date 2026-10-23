#!/usr/bin/env python3
"""采集守护进程（Python 版，避免依赖 PowerShell 语言模式）。

退出条件（任一）：
  1) 存在 data/crawler/STOP
  2) 磁盘可用低于 --min-free-gb
  3) 待采集为 0
  4) 运行超过 --max-hours

子进程输出写入 logs/crawl_forever.log（文件重定向，不使用管道）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

ROOT = r"E:\ETL_Amazon"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
CRAWLER = os.path.join(ROOT, "crawler", "crawl_product_pages.py")
PENDING = os.path.join(ROOT, "crawler", "pending_count.py")
STOP = os.path.join(ROOT, "data", "crawler", "STOP")
LOG = os.path.join(ROOT, "logs", "crawl_forever.log")


def log(msg: str) -> None:
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_pending():
    r = subprocess.run([PY, PENDING], capture_output=False, stdout=subprocess.PIPE)
    # 注：若环境禁止管道，这里会失败；由调用方兜底
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--min-free-gb", type=float, default=20.0)
    ap.add_argument("--max-hours", type=float, default=6.0)
    ap.add_argument("--wait", type=int, default=15)
    args = ap.parse_args(argv)

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    log("supervisor start: delay=%.0f concurrency=%d batch=%d minFree=%.0fGB maxHours=%.1f"
        % (args.delay, args.concurrency, args.batch, args.min_free_gb, args.max_hours))
    t0 = time.time()
    rnd = 0

    while True:
        if os.path.isfile(STOP):
            log("STOP file found -> exit")
            break
        if (time.time() - t0) / 3600.0 >= args.max_hours:
            log("max hours reached -> exit")
            break

        try:
            info = read_pending()
        except Exception as e:  # noqa: BLE001
            log("cannot read pending (%s) -> wait" % e)
            time.sleep(args.wait)
            continue

        free = info.get("free_gb")
        if free is not None and free < args.min_free_gb:
            log("low disk %.1fGB -> exit" % free)
            break
        if int(info.get("pending", 0)) == 0:
            log("no pending targets -> exit")
            break

        rnd += 1
        log("round %d: pending=%d free=%sGB" % (rnd, info["pending"], free))
        with open(LOG, "a", encoding="utf-8") as logfh:
            # 文件重定向而非管道，规避受限环境下管道不可用
            proc = subprocess.run(
                [PY, CRAWLER,
                 "--limit", str(args.batch),
                 "--delay", str(args.delay),
                 "--concurrency", str(args.concurrency),
                 "--min-free-gb", str(args.min_free_gb)],
                stdout=logfh, stderr=subprocess.STDOUT,
            )
        log("crawler exit=%d" % proc.returncode)
        time.sleep(args.wait)

    log("supervisor end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
