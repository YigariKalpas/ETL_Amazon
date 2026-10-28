# -*- coding: utf-8 -*-
"""【KEY-65】多进程并行采集：N 个 worker 分片抓取，共享一个全局限速与一个退避信号。

为什么要分片 + 全局限速：
  - 站点按"请求预算"限流，总量不因并发而增加，所以并发只用来重叠网络等待与落盘；
  - 因此每个 worker 的间隔 = 全局间隔 × worker 数，聚合速率恒定（默认 1 请求 / 3 秒）；
  - 任一 worker 撞到验证页，会把暂停时间写进共享 pause 文件，其他 worker 一起等待（不会各撞各的）。

实现：
  1) 从 data/crawler/product_urls.txt 取 ASIN，剔除状态库已有记录的，按种子随机抽样；
  2) 均分成 N 个分片 CSV（asin, source_url），worker 之间不重叠；
  3) 每个 worker 是独立进程，共用 SQLite（WAL 模式）与快照目录（按 ASIN 分桶，不会冲突）；
  4) 汇总进度、STOP、磁盘水位、运行时长统一由本脚本监控。

用法：
  python crawler/parallel_crawl.py --workers 3 --per-worker 100 --global-interval 3
  python crawler/parallel_crawl.py --workers 4 --per-worker 50 --global-interval 2 --max-hours 2
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import random
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
URLS = os.path.join(ROOT, "data", "crawler", "product_urls.txt")
DB = os.path.join(ROOT, "data", "crawler", "products.sqlite")
SHARDS = os.path.join(ROOT, "data", "crawler", "shards")
LOGS = os.path.join(ROOT, "logs")
STOP = os.path.join(ROOT, "data", "crawler", "STOP")
PAUSE = os.path.join(ROOT, "data", "crawler", "PAUSE")


def load_pending(limit: int, seed: int) -> list:
    done = set()
    if os.path.isfile(DB):
        conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        done = {r[0] for r in conn.execute("SELECT asin FROM product_page")}
        conn.close()
    urls = []
    with io.open(URLS, encoding="utf-8") as fh:
        for line in fh:
            u = line.strip()
            if not u:
                continue
            asin = u.rstrip("/").rsplit("/", 1)[-1]
            if asin and asin not in done:
                urls.append((asin, u))
    random.seed(seed)
    if limit and limit < len(urls):
        urls = random.sample(urls, limit)
    return urls


def write_shards(items: list, n: int) -> list:
    os.makedirs(SHARDS, exist_ok=True)
    shards = []
    size = max(1, (len(items) + n - 1) // n)
    for i in range(n):
        part = items[i * size:(i + 1) * size]
        path = os.path.join(SHARDS, "shard_%02d.csv" % (i + 1))
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["asin", "source_url"])
            w.writerows(part)
        shards.append((path, len(part)))
    return shards


def db_counts() -> dict:
    if not os.path.isfile(DB):
        return {}
    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    rows = conn.execute("SELECT status, COUNT(*) FROM product_page GROUP BY status").fetchall()
    conn.close()
    return dict(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="多进程并行采集")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--per-worker", type=int, default=100)
    ap.add_argument("--global-interval", type=float, default=6.0,
                    help="聚合最小间隔（秒/请求）；每个 worker 的间隔 = 该值 × workers。"
                         "实测预算约 130–190 次/窗口，6–8 秒可让预算撑到 15–20 分钟")
    ap.add_argument("--purge-every-min", type=float, default=30.0,
                    help="每 N 分钟清理一次验证页快照（0=不清理）")
    ap.add_argument("--refresh-every-min", type=float, default=0.0,
                    help="每 N 分钟刷新一次目标层（0=不刷新；采集中刷新代价较高）")
    ap.add_argument("--no-refresh-at-end", action="store_true",
                    help="采集结束后不自动刷新目标层（默认会刷新一次）")
    ap.add_argument("--initial-cooldown-min", type=float, default=0.0,
                    help="开工前先等 N 分钟（被限流后立刻重启会继续撞验证页）")
    ap.add_argument("--concurrency-per-worker", type=int, default=1,
                    help="单 worker 内线程数（默认 1，避免内部再放大请求频率）")
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--max-hours", type=float, default=4.0)
    ap.add_argument("--min-free-gb", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    workers = max(1, args.workers)
    total = workers * max(1, args.per_worker)
    worker_delay = max(0.5, args.global_interval * workers)
    items = load_pending(total, args.seed)
    print("pending_loaded = %d (请求 %d)" % (len(items), total))
    print("workers        = %d" % workers)
    print("worker_delay   = %.1fs / worker  =>  聚合 %.2f 请求/秒"
          % (worker_delay, 1.0 / worker_delay * workers))
    if not items:
        print("没有待采集目标。")
        return 0

    shards = write_shards(items, workers)
    for path, n in shards:
        print("  %s  %d 个目标" % (os.path.basename(path), n))
    if args.dry_run:
        return 0

    os.makedirs(LOGS, exist_ok=True)
    for p in (STOP, PAUSE):
        if os.path.isfile(p) and p.endswith("STOP"):
            os.remove(p)

    if args.initial_cooldown_min > 0:
        end = time.time() + args.initial_cooldown_min * 60
        print("initial cooldown %.0f min（撞到限流后立刻重启会继续吃验证页）"
              % args.initial_cooldown_min)
        while time.time() < end:
            if os.path.isfile(STOP):
                print("STOP during cooldown -> exit")
                return 0
            time.sleep(min(15, max(1, end - time.time())))

    procs = []
    started = time.time()
    for i, (path, n) in enumerate(shards, start=1):
        if n == 0:
            continue
        logf = open(os.path.join(LOGS, "worker_%02d.log" % i), "w", encoding="utf-8")
        cmd = [PY, "-u", CRAWLER,
               "--list", path,
               "--delay", "%.2f" % worker_delay,
               "--jitter", "1.0",
               "--concurrency", str(args.concurrency_per_worker),
               "--pause-file", PAUSE,
               "--min-free-gb", str(args.min_free_gb),
               "--stop-file", STOP,
               "--seed", str(args.seed + i)]
        procs.append((i, subprocess.Popen(cmd, cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT), logf))
    print("started %d workers" % len(procs))

    def run_side(script: str, logname: str) -> int:
        """【KEY-69】在采集期间执行维护脚本（清理/刷新），输出落 logs/。"""
        with open(os.path.join(LOGS, logname), "a", encoding="utf-8") as fh:
            r = subprocess.run([PY, "-u", script], cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
        return r.returncode

    last_report = 0.0
    now0 = time.time()
    last_purge = now0
    last_refresh = now0
    exit_codes = {}
    while True:
        alive = [p for _, p, _ in procs if p.poll() is None]
        if not alive:
            break
        if os.path.isfile(STOP):
            print("STOP found -> terminating workers")
            for _, p, _ in procs:
                if p.poll() is None:
                    p.terminate()
            break
        if (time.time() - started) / 3600.0 >= args.max_hours:
            print("max hours reached -> terminating workers")
            for _, p, _ in procs:
                if p.poll() is None:
                    p.terminate()
            break
        now = time.time()
        if args.purge_every_min and now - last_purge >= args.purge_every_min * 60:
            last_purge = now
            print("[%s] purge validation snapshots" % datetime.now().strftime("%H:%M:%S"))
            run_side(PURGE, "purge.log")
        if args.refresh_every_min and now - last_refresh >= args.refresh_every_min * 60:
            last_refresh = now
            print("[%s] refresh target layer" % datetime.now().strftime("%H:%M:%S"))
            rc = run_side(PIPELINE, "refresh.log")
            print("[%s] refresh exit=%d" % (datetime.now().strftime("%H:%M:%S"), rc))
        if now - last_report >= 60:
            last_report = now
            print("[%s] alive=%d  %s" % (datetime.now().strftime("%H:%M:%S"), len(alive), db_counts()))
        time.sleep(5)

    for i, p, logf in procs:
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
        exit_codes[i] = p.returncode
        logf.close()

    if not args.no_refresh_at_end:
        print("refresh target layer after crawl ...")
        rc = run_side(PIPELINE, "refresh.log")
        print("refresh exit=%d" % rc)

    print("\nexit_codes = %s" % exit_codes)
    print("final      = %s" % db_counts())
    print("elapsed    = %.1f s" % (time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
