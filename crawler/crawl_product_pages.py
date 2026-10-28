#!/usr/bin/env python3
"""Amazon 产品页采集器（作业任务2）。

=== 合规声明（重要）===
作业文档 §四任务2 明确：配置 robots.txt、请求间隔与并发上限；
遇到访问限制不得以绕过验证、登录或封禁作为完成手段。
因此本脚本：
  - 尊重 robots.txt；
  - 并发有上限，且**全局请求间隔独立于线程数**（多线程不提升对站点的请求频率）；
  - 请求间隔带随机抖动，避免同步突发；
  - **不实现** IP 轮换 / 代理池 / 伪造 UA 等规避手段。
被封禁时应如实记录 restricted，并按文档「客观不可访问页面须有证据」如实报告覆盖率。

关键点用【KEY-n】标注。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import os
import random
import re
import shutil
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# ---------- 【KEY-1】状态词表 ----------
ST_SUCCESS       = "success"
ST_NOT_FOUND     = "not_found"
ST_RESTRICTED    = "restricted"
ST_TIMEOUT       = "timeout"
ST_PARSE_FAILED  = "parse_failed"
ST_NOT_COLLECTED = "not_collected"
ST_VALIDATION    = "validation_page"

# ---------- 【KEY-2】验证页特征（不可用通用联系方式，见 README §六） ----------
ROBOT_MARKERS = (
    "Enter the characters you see below",
    "validateCaptcha",          # 实测：受限页正文含该串（原写法要求 "/errors/" 前缀，漏判）
    "Sorry, we just need to make sure you're not a robot",
    "Robot Check",
    "Type the characters you see in this image",
)
SOFT404_MARKERS = ("Page Not Found", "dogs of Amazon")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

DEFAULT_LIST = r"E:\ETL_Amazon\data\crawler\product_list.csv"
DEFAULT_DB   = r"E:\ETL_Amazon\data\crawler\products.sqlite"
DEFAULT_DIR  = r"E:\ETL_Amazon\data\raw\pages"

USER_AGENT = "ETL-AmazonCoursework/1.0 (academic use; contact: student)"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def page_title(body_text: str) -> str:
    """提取 <title>，作为「页面模板是否变化」的可复核证据。"""
    m = TITLE_RE.search(body_text)
    if not m:
        return ""
    return re.sub(r"s+", " ", m.group(1)).strip()[:120]


def build_url(asin: str) -> str:
    """【KEY-5】ASIN -> 产品页 URL。"""
    return "https://www.amazon.com/dp/" + asin


PAUSE_FILE_DEFAULT = r"E:\ETL_Amazon\data\crawler\PAUSE"


def read_pause_until(path: str) -> float:
    """【KEY-63】读取跨进程共享的暂停截止时间（epoch 秒）；无文件或已过期返回 0。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return float(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0.0


def write_pause(path: str, seconds: float) -> None:
    """任一进程判定被限流时写入，其他并行进程都会等待同一段时间。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        until = time.time() + max(0.0, seconds)
        cur = read_pause_until(path)
        if until > cur:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("%.0f" % until)
    except OSError:
        pass


class RateLimiter:
    """【KEY-16】全局节流器：无论多少线程，请求发起的最小间隔不变。

    多线程只用于重叠「网络等待 / 落盘 / 解析」，**不提高对站点的请求频率**。
    """

    def __init__(self, interval: float, jitter: float, pause_file: str = "") -> None:
        self._interval = max(0.0, interval)
        self._jitter = max(0.0, jitter)
        self._pause_file = pause_file or ""
        self._lock = threading.Lock()
        self._next_at = 0.0
        self._pause_until = 0.0

    def wait(self) -> None:
        # 【KEY-64】先遵守跨进程暂停：任一 worker 撞到验证页，全体一起等
        if self._pause_file:
            while True:
                until = read_pause_until(self._pause_file)
                remain = until - time.time()
                if remain <= 0:
                    break
                time.sleep(min(remain, 30))
        with self._lock:
            now = time.monotonic()
            start = self._pause_until if self._pause_until > now else max(now, self._next_at)
            self._next_at = start + self._interval + random.uniform(0, self._jitter)
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def pause(self, seconds: float) -> None:
        """【KEY-19】全局暂停：所有线程在 pause 期间都不发请求。"""
        with self._lock:
            self._pause_until = max(self._pause_until, time.monotonic() + max(0.0, seconds))


class ValidationBackoff:
    """【KEY-20】自适应退避：按滑动窗口判断是否被限流，命中即全局暂停。

    用滑动窗口而不是"连续 N 次"：限流期间常夹杂零星成功页，
    用连续计数会被一次成功清零，导致永远不触发退避（实测踩到过）。

    规则：
      - 窗口 = 最近 window 次请求的结果；
      - 窗口内 validation_page/restricted 达到 bad 次 → 判定受限；
      - 暂停 = base × factor^(恶化轮数-1)，封顶 cap；暂停后清空窗口；
      - 某窗口内 0 次受限 → 认为已恢复，恶化轮数清零。

    合规：被限制时只降速/等待，不绕过验证（无 IP 轮换、无验证码识别）。
    """

    def __init__(self, limiter, window, bad, base, factor, cap, logger=print, abort_after=0):
        self.limiter = limiter
        self.window = max(2, int(window))
        self.bad = max(1, min(int(bad), self.window))
        self.abort_after = max(0, abort_after)
        self.base = max(1.0, base)
        self.factor = max(1.0, factor)
        self.cap = max(self.base, cap)
        self._recent: list = []
        self._bad_rounds = 0
        self._lock = threading.Lock()
        self._log = logger

    @property
    def bad_rounds(self) -> int:
        with self._lock:
            return self._bad_rounds

    def record(self, status: str) -> None:
        with self._lock:
            self._recent.append(1 if status in (ST_VALIDATION, ST_RESTRICTED) else 0)
            if len(self._recent) < self.window:
                return
            n_bad = sum(self._recent)
            if n_bad == 0:
                self._bad_rounds = 0          # 一个干净窗口 = 已恢复
                self._recent.clear()
                return
            if n_bad < self.bad:
                self._recent.pop(0)           # 窗口滑动一格再看
                return
            self._bad_rounds += 1
            if self.abort_after and self._bad_rounds >= self.abort_after:
                # 【KEY-26】已到中止轮数：不再暂停，交给主循环中止
                return
            secs = min(self.base * (self.factor ** (self._bad_rounds - 1)), self.cap)
            self.limiter.pause(secs)
            if getattr(self.limiter, "_pause_file", ""):
                write_pause(self.limiter._pause_file, secs)
            self._log("BACKOFF bad=%d/%d round=%d pause=%.0fs" % (
                n_bad, self.window, self._bad_rounds, secs))
            self._recent.clear()


class RedirectRecorder(urllib.request.HTTPRedirectHandler):
    """【KEY-6】记录重定向链。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener(proxy: str | None):
    # 【KEY-7】默认直连（本机系统代理会让部分域名 403）
    proxy_map = {"http": proxy, "https": proxy} if proxy else {}
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxy_map))


def init_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)   # 【KEY-17】多线程共享连接 + 外部加锁
    # 【KEY-62】多进程并行采集：WAL + busy_timeout，避免 database is locked
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS product_page (
            asin TEXT PRIMARY KEY, source_url TEXT NOT NULL, final_url TEXT,
            status TEXT NOT NULL, http_status INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT, snapshot_path TEXT, content_sha256 TEXT,
            content_length INTEGER, note TEXT, updated_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_pp_status ON product_page(status)")
    conn.commit()
    return conn


def load_targets(list_path: str) -> list[str]:
    if not os.path.isfile(list_path):
        raise SystemExit("采集清单不存在: %s（先运行 extract_asins.py）" % list_path)
    out = []
    with open(list_path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            a = (row.get("asin") or "").strip()
            if a:
                out.append(a)
    return out


def select_pending(conn: sqlite3.Connection, targets: list[str], max_attempts: int) -> list[str]:
    """【KEY-8】断点续跑。"""
    state = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT asin, status, attempts, COALESCE(note,'') FROM product_page")}
    pending = []
    for a in targets:
        if a not in state:
            pending.append(a)
            continue
        status, attempts, note = state[a]
        if status in (ST_SUCCESS, ST_NOT_FOUND):
            continue
        if note == "robots_disallow":
            continue
        if attempts < max_attempts:
            pending.append(a)
    return pending


def classify(http_status: int, body_text: str, asin: str) -> tuple[str, str]:
    """【KEY-9】先 HTTP 状态、后正文特征。"""
    if http_status in (404, 410):
        return ST_NOT_FOUND, "http_%d" % http_status
    if http_status in (403, 429, 503):
        return ST_RESTRICTED, "http_%d" % http_status
    if http_status != 200:
        return ST_PARSE_FAILED, "http_%d" % http_status

    for m in ROBOT_MARKERS:
        if m in body_text:
            return ST_VALIDATION, "robot_marker:" + m
    if any(m in body_text for m in SOFT404_MARKERS):
        return ST_NOT_FOUND, "soft_404"
    # 【KEY-10】核对页面与目标 ASIN 的对应关系
    if asin not in body_text:
        title = page_title(body_text)
        note = "asin_not_found_in_page"
        if title:
            note += ";title=" + title[:80]
            if "prime video" in title.lower():
                note += ";template=prime_video"     # 实测：数字视频 ASIN 会返回通用落地页
        return ST_PARSE_FAILED, note
    return ST_SUCCESS, ""


def fetch_once(url: str, asin: str, timeout: int, proxy: str | None):
    opener = build_opener(proxy)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "close",
    })
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
            final_url = resp.geturl()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        code = e.code
        final_url = e.geturl() if hasattr(e, "geturl") else url
        status, note = classify(code, body.decode("utf-8", "replace"), asin)
        return status, code, final_url, body, note
    except (socket.timeout, TimeoutError):
        return ST_TIMEOUT, None, url, b"", "socket_timeout"
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return ST_TIMEOUT, None, url, b"", "url_timeout"
        return ST_PARSE_FAILED, None, url, b"", "url_error:" + str(reason)[:120]
    except Exception as e:  # noqa: BLE001
        return ST_PARSE_FAILED, None, url, b"", "exception:" + type(e).__name__ + ":" + str(e)[:100]

    status, note = classify(code, body.decode("utf-8", "replace"), asin)
    if final_url and ("/dp/" in final_url or "/gp/product/" in final_url):
        tail = final_url.split("/dp/")[-1].split("/gp/product/")[-1][:10]
        if tail and tail.upper() != asin:
            note = (note + ";" if note else "") + ("redirect_asin:" + tail.upper())
    return status, code, final_url, body, note


def page_bucket(asin: str) -> str:
    """【KEY-12a】分桶：sha1(ASIN)[:2]，避免 25 万个文件挤在同一目录。"""
    return hashlib.sha1(asin.encode("utf-8")).hexdigest()[:2]


def snapshot_path_for(pages_dir: str, asin: str) -> str:
    return os.path.join(pages_dir, page_bucket(asin), asin + ".html.gz")


def save_snapshot(pages_dir: str, asin: str, body: bytes) -> tuple[str, str, int]:
    """【KEY-12】原始响应 gzip 落盘（分桶）+ SHA-256。"""
    path = snapshot_path_for(pages_dir, asin)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wb", compresslevel=6) as fh:
        fh.write(body)
    return os.path.abspath(path), hashlib.sha256(body).hexdigest(), len(body)


UPSERT_SQL = """
INSERT INTO product_page (asin, source_url, final_url, status, http_status,
                          attempts, fetched_at, snapshot_path, content_sha256,
                          content_length, note, updated_at)
VALUES (:asin, :source_url, :final_url, :status, :http_status,
        :attempts, :fetched_at, :snapshot_path, :content_sha256,
        :content_length, :note, :updated_at)
ON CONFLICT(asin) DO UPDATE SET
    final_url=excluded.final_url, status=excluded.status,
    http_status=excluded.http_status, attempts=excluded.attempts,
    fetched_at=excluded.fetched_at, snapshot_path=excluded.snapshot_path,
    content_sha256=excluded.content_sha256, content_length=excluded.content_length,
    note=excluded.note, updated_at=excluded.updated_at
"""


def ensure_robots(cache_path: str, proxy: str | None, timeout: int):
    """【KEY-13】合规前置：取不到 robots.txt 就中止。"""
    import urllib.robotparser
    rp = urllib.robotparser.RobotFileParser()
    if os.path.isfile(cache_path):
        with open(cache_path, "r", encoding="utf-8", errors="replace") as fh:
            rp.parse(fh.readlines())
        return rp, "cache"
    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    url = "https://www.amazon.com/robots.txt"
    opener = build_opener(proxy)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with opener.open(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
        with open(cache_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        rp.parse(text.splitlines())
        return rp, "fetched"
    except Exception as e:  # noqa: BLE001
        raise SystemExit("无法获取 robots.txt（%s），为遵守数据源规则中止采集。" % e)


def process_one(asin: str, ctx: dict) -> dict:
    """处理单个 ASIN（在线程池中执行）。"""
    args, conn, db_lock, robots, limiter = ctx["args"], ctx["conn"], ctx["db_lock"], ctx["robots"], ctx["limiter"]
    lock = ctx["print_lock"]
    url = build_url(asin)

    # 【KEY-23】已发出中止信号/停止标记：不再消耗任何请求
    ev = ctx.get("abort_event")
    if (ev is not None and ev.is_set()) or os.path.isfile(args.stop_file):
        return {"asin": asin, "status": ST_NOT_COLLECTED, "skipped": True}

    # 【KEY-14】robots 逐 URL 判定
    if robots is not None and not robots.can_fetch(USER_AGENT, url):
        row = {"asin": asin, "source_url": url, "final_url": url, "status": ST_RESTRICTED,
               "http_status": None, "attempts": 0, "fetched_at": utcnow(), "snapshot_path": None,
               "content_sha256": None, "content_length": None, "note": "robots_disallow",
               "updated_at": utcnow()}
        with db_lock:
            conn.execute(UPSERT_SQL, row); conn.commit()
        with lock:
            print("  %s -> restricted (robots_disallow)" % asin)
        return row

    status, http_status, final_url, body, note = ST_NOT_COLLECTED, None, url, b"", ""
    attempts = 0
    # 【KEY-15】有上限重试 + 指数退避；每次请求都先过全局节流器
    while attempts < args.max_attempts:
        attempts += 1
        limiter.wait()
        status, http_status, final_url, body, note = fetch_once(url, asin, args.timeout, ctx["proxy"])
        ctx["backoff"].record(status)
        if status in (ST_SUCCESS, ST_NOT_FOUND):
            break
        if status == ST_VALIDATION:
            # 【KEY-21】遇到验证页不原地重试：交给退避暂停，后续运行再从 pending 取
            break
        if attempts < args.max_attempts and status in (ST_RESTRICTED, ST_TIMEOUT, ST_PARSE_FAILED):
            time.sleep(min(args.delay * (2 ** (attempts - 1)), 30) + random.uniform(0, args.jitter))

    snap = digest = None
    length = None
    if body:
        snap, digest, length = save_snapshot(args.pages_dir, asin, body)

    row = {"asin": asin, "source_url": url, "final_url": final_url, "status": status,
           "http_status": http_status, "attempts": attempts, "fetched_at": utcnow(),
           "snapshot_path": snap, "content_sha256": digest, "content_length": length,
           "note": note, "updated_at": utcnow()}
    with db_lock:
        conn.execute(UPSERT_SQL, row); conn.commit()
    with lock:
        print("  %-11s -> %-15s http=%-4s attempts=%d%s" % (
            asin, status, http_status, attempts, "" if not note else " " + note))
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Amazon 产品页采集器（限速 + 有界并发）")
    ap.add_argument("--list", default=DEFAULT_LIST)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--pages-dir", default=DEFAULT_DIR)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sample", type=int, default=0,
                    help="随机抽样 N 个待采集目标（种子固定，可复现）；优先于 --limit")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="【全局】相邻两次请求的最小间隔秒数（与线程数无关）")
    ap.add_argument("--jitter", type=float, default=0.8, help="在最小间隔上叠加的随机抖动上限（秒）")
    ap.add_argument("--concurrency", type=int, default=2,
                    help="工作线程数上限（仅重叠网络等待/落盘，不提升请求频率）")
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--proxy", default="", help="自有代理地址；不得用于规避访问限制")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ignore-robots", action="store_true", help="【不合规】仅调试")
    ap.add_argument("--min-free-gb", type=float, default=20.0, help="磁盘可用低于此值即停止")
    ap.add_argument("--chunk", type=int, default=200, help="每批提交数（批间检查水位与停止标记）")
    ap.add_argument("--stop-file", default=r"E:\ETL_Amazon\data\crawler\STOP", help="存在即停止")
    ap.add_argument("--pause-file", default=PAUSE_FILE_DEFAULT,
                    help="跨进程共享的暂停标记；任一 worker 被限流时全体等待")
    ap.add_argument("--seed", type=int, default=20261023, help="抽样种子；并行 worker 用不同 seed 避免重叠")
    ap.add_argument("--backoff-window", type=int, default=10, help="滑动窗口大小（最近 N 次结果）")
    ap.add_argument("--backoff-bad", type=int, default=4, help="窗口内多少次验证页/受限即判定被限流")
    ap.add_argument("--backoff-base", type=float, default=60.0, help="首次暂停秒数")
    ap.add_argument("--backoff-factor", type=float, default=2.0, help="暂停时长增长倍数")
    ap.add_argument("--backoff-cap", type=float, default=1800.0, help="单次暂停上限秒数")
    ap.add_argument("--abort-after-blocks", type=int, default=0,
                    help="连续这么多次验证页/受限即中止本次运行（0=不中止）")
    args = ap.parse_args(argv)

    if args.concurrency < 1:
        args.concurrency = 1
    if args.concurrency > 8:
        print("警告：并发数 %d 偏大，请确认目标站点允许；已按 8 截断。" % args.concurrency)
        args.concurrency = 8

    conn = init_db(args.db)
    targets = load_targets(args.list)
    pending = select_pending(conn, targets, args.max_attempts)
    if args.sample > 0 and args.sample < len(pending):
        # 固定种子，保证同一批待采集集合产生同一抽样，可复现
        random.seed(args.seed)
        pending = random.sample(pending, args.sample)
        print("sampled       = %d (seed 20261023)" % len(pending))
    elif args.limit > 0:
        pending = pending[:args.limit]

    print("targets_total = %d" % len(targets))
    print("pending       = %d" % len(pending))
    print("concurrency   = %d (bounded)" % args.concurrency)
    print("min_interval  = %.2fs + jitter<=%.2fs  =>  <= %.2f req/s" % (
        args.delay, args.jitter, 1.0 / max(args.delay, 0.001)))
    if not pending:
        print("没有待采集目标。")
        return 0
    if args.dry_run:
        for a in pending[:10]:
            print("  would fetch %s" % build_url(a))
        if len(pending) > 10:
            print("  ... (%d more)" % (len(pending) - 10))
        return 0

    proxy = args.proxy or None
    if args.ignore_robots:
        robots = None
        print("!! 已跳过 robots 检查（不合规，仅调试用）")
    else:
        robots, rsrc = ensure_robots(r"E:\ETL_Amazon\data\crawler\robots_amazon.txt", proxy, args.timeout)
        print("robots        = %s" % rsrc)

    ctx = {
        "args": args, "conn": conn, "db_lock": threading.Lock(),
        "print_lock": threading.Lock(), "robots": robots, "proxy": proxy,
        "limiter": RateLimiter(args.delay, args.jitter, args.pause_file),
    }
    ctx["backoff"] = ValidationBackoff(
        ctx["limiter"], args.backoff_window, args.backoff_bad, args.backoff_base,
        args.backoff_factor, args.backoff_cap, logger=print,
        abort_after=args.abort_after_blocks)

    ok = 0
    processed = 0
    t0 = time.time()

    def should_stop():
        """【KEY-18】批次间断点：外部停止标记 + 磁盘水位。"""
        if os.path.isfile(args.stop_file):
            return "stop_file"
        try:
            free_gb = shutil.disk_usage(args.pages_dir).free / (1024 ** 3)
        except Exception:
            return ""
        return ("low_disk:%.1fGB" % free_gb) if free_gb < args.min_free_gb else ""

    ctx["abort_event"] = threading.Event()
    chunk = max(1, int(args.chunk))
    aborted = ""
    for start in range(0, len(pending), chunk):
        reason = should_stop()
        if reason:
            print("STOPPED: " + reason)
            break
        if args.abort_after_blocks > 0 and ctx["backoff"].bad_rounds >= args.abort_after_blocks:
            aborted = "backoff_abort:rounds=%d" % ctx["backoff"].bad_rounds
            print("STOPPED: " + aborted)
            break
        batch = pending[start:start + chunk]
        pool = ThreadPoolExecutor(max_workers=args.concurrency)
        try:
            futures = [pool.submit(process_one, a, ctx) for a in batch]
            for fut in as_completed(futures):
                row = fut.result()
                if row.get("skipped"):
                    continue
                processed += 1
                if row["status"] == ST_SUCCESS:
                    ok += 1
                if args.abort_after_blocks > 0 and ctx["backoff"].bad_rounds >= args.abort_after_blocks:
                    aborted = "backoff_abort:rounds=%d" % ctx["backoff"].bad_rounds
                    break
        finally:
            # 【KEY-24】中止后必须取消排队任务，否则已提交的 future 还会继续发请求
            if aborted:
                ctx["abort_event"].set()
                pool.shutdown(wait=False, cancel_futures=True)
            else:
                pool.shutdown(wait=True)
        if aborted:
            print("STOPPED: " + aborted)
            break
        try:
            free_gb = shutil.disk_usage(args.pages_dir).free / (1024 ** 3)
        except Exception:
            free_gb = -1
        print("progress: %d/%d (%.1f%%)  free=%.1fGB" % (
            processed, len(pending), 100.0 * processed / max(len(pending), 1), free_gb))

    by_status: dict[str, int] = {}
    for r in conn.execute("SELECT status, COUNT(*) FROM product_page GROUP BY status"):
        by_status[r[0]] = r[1]
    print("elapsed       = %.1f s" % (time.time() - t0))
    print("success_this_run = %d / %d" % (ok, len(pending)))
    print("db_status     = %s" % by_status)
    if aborted:
        print("aborted       = " + aborted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
