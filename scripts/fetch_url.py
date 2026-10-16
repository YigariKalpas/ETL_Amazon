#!/usr/bin/env python3
"""项目引导用下载器：可绕过系统代理 + 断点续传 + 重试 + SHA-256。

为什么需要它（本机实测）：
  - 系统 Internet 设置里配了代理 127.0.0.1:7897（ProxyEnable=1）。
  - Python urllib 默认读该注册表代理；对部分域名返回 403。
  - Windows PowerShell 5.1 的 .NET HTTP 栈在本沙箱下直接失败
    （"基础连接已经关闭"），即使显式把 DefaultWebProxy 置空。
  - 实测：GitHub release 直连超时；经代理约 0.36 MB/s；国内镜像直连最快。
故本脚本把「是否走代理」做成显式开关，并对大文件支持断点续传与重试。

用法:
    python fetch_url.py <url> <dest> [--proxy URL] [--sha256] [--retries N] [--timeout S]
退出码: 0 成功 / 2 参数错误 / 1 失败
"""
from __future__ import annotations

import hashlib
import os
import ssl
import sys
import time
import urllib.request

CHUNK = 1 << 20
PROGRESS_STEP = 32 << 20


def parse(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    positional: list[str] = []
    opts: dict[str, str] = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if key == "sha256":
                opts[key] = "1"
            else:
                i += 1
                opts[key] = argv[i] if i < len(argv) else ""
        else:
            positional.append(a)
        i += 1
    return positional, opts


def build_opener(proxy: str | None):
    handlers = [urllib.request.HTTPSHandler(context=ssl.create_default_context())]
    handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
    return urllib.request.build_opener(*handlers)


def attempt(opener, url: str, dest: str, timeout: int) -> int:
    """单次尝试；若 .part 已存在则请求 Range 续传。返回本次新增字节数。"""
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": "Mozilla/5.0 (dsh-etl-bootstrap)"}
    if have:
        headers["Range"] = "bytes=%d-" % have
    req = urllib.request.Request(url, headers=headers)

    with opener.open(req, timeout=timeout) as resp:
        resumed = have > 0 and resp.status == 206
        if have and not resumed:
            have = 0
        mode = "ab" if resumed else "wb"
        got = 0
        last_report = have
        with open(part, mode) as out:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                got += len(chunk)
                if have + got - last_report >= PROGRESS_STEP:
                    last_report = have + got
                    sys.stdout.write("    ... %.1f MB\n" % ((have + got) / 1048576))
                    sys.stdout.flush()
    return got


def main(argv: list[str]) -> int:
    positional, opts = parse(argv[1:])
    if len(positional) < 2:
        sys.stderr.write("usage: fetch_url.py <url> <dest> [--proxy URL] [--sha256] [--retries N] [--timeout S]\n")
        return 2

    url, dest = positional[0], positional[1]
    dest = os.path.abspath(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    proxy = opts.get("proxy") or None
    retries = int(opts.get("retries", "3"))
    timeout = int(opts.get("timeout", "60"))

    print("  proxy=%s" % (proxy or "(direct)"))
    opener = build_opener(proxy)
    t0 = time.time()
    for i in range(1, retries + 1):
        try:
            attempt(opener, url, dest, timeout)
            break
        except Exception as exc:  # noqa: BLE001
            part = dest + ".part"
            have = os.path.getsize(part) if os.path.exists(part) else 0
            print("    attempt %d/%d failed (%.1f MB so far): %s" % (i, retries, have / 1048576, str(exc)[:110]))
            if i == retries:
                sys.stderr.write("download failed after %d attempts\n" % retries)
                return 1
            time.sleep(2)

    part = dest + ".part"
    size = os.path.getsize(part)
    digest = hashlib.sha256()
    with open(part, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    os.replace(part, dest)

    dt = max(time.time() - t0, 0.001)
    print("  done %.1f MB in %.1fs (%.2f MB/s)" % (size / 1048576, dt, size / 1048576 / dt))
    if "sha256" in opts:
        print("  sha256=%s" % digest.hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
