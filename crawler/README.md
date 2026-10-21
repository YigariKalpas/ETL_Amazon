# 产品页采集（作业任务2）

> 由评论中的 `product/productId`（即 ASIN）组成产品页 URL，逐页采集并留存原始证据。
> 代码：`extract_asins.py`（前置）、`crawl_product_pages.py`（主爬虫）。

## 一、流程

```cmd
rem 1) 流式提取去重 ASIN（不加载原文）
.venv\Scripts\python.exe crawler\extract_asins.py
rem    可选有界抽样：--max-lines 200000

rem 2) 采集产品页（robots 先行，限速，可断点续跑）
.venv\Scripts\python.exe crawler\crawl_product_pages.py --limit 20 --delay 3
rem    只演练不发请求：--dry-run
```

URL 构造：`https://www.amazon.com/dp/<ASIN>`（与文档 §二「amazon.com/dp/<ASIN>」一致）。

## 二、状态词表（对应文档「状态须区分成功、页面不存在、访问受限、超时、解析失败、未采集」）

| 状态 | 含义 | 判定依据 |
|---|---|---|
| `success` | 成功 | HTTP 200 且正文含目标 ASIN（核对页面与 ASIN 的对应关系） |
| `not_found` | 页面不存在 | 404/410，或 200 的软 404（"Page Not Found"） |
| `restricted` | 访问受限 | 403/429/503，或 robots.txt 禁止 |
| `timeout` | 超时 | socket/URL 超时 |
| `parse_failed` | 解析失败 | 200 但正文不含目标 ASIN，或异常状态码 |
| `validation_page` | 验证页（不得算成功） | 200 但命中机器人校验特征 |
| `not_collected` | 未采集 | 尚未进入队列 |

## 三、关键设计（代码中【KEY-n】标注）

| 编号 | 位置 | 内容 |
|---|---|---|
| KEY-1 | 状态词表 | 六类状态 + 验证页，杜绝「200 即成功」 |
| KEY-2 | `ROBOT_MARKERS` | 验证页特征（**不可用通用联系方式作特征**，见 §六） |
| KEY-5 | `build_url` | ASIN → `amazon.com/dp/<ASIN>` |
| KEY-6 | `RedirectRecorder` | 记录重定向链，核对最终页面是否仍是目标 ASIN |
| KEY-7 | `build_opener` | 默认直连（本机系统代理会让部分域名 403） |
| KEY-8 | `select_pending` | 断点续跑：终态跳过、可重试态按次数保留 |
| KEY-9 | `classify` | **先按 HTTP 状态、再看正文特征** |
| KEY-10 | `classify` | 核对 ASIN 是否出现在页面中 |
| KEY-12 | `save_snapshot` | 原始响应 gzip 落盘 + SHA-256 |
| KEY-13 | `ensure_robots` | robots.txt 取不到就中止，不擅自绕过 |
| KEY-15 | 主循环 | 有上限重试 + 指数退避 |
| KEY-16 | `RateLimiter` | **全局**请求间隔（与线程数无关）+ 随机抖动 |
| KEY-17 | `init_db` | `check_same_thread=False` + 写锁，支撑「有界并发」 |

## 四、证据与产物

| 产物 | 位置 | 说明 |
|---|---|---|
| 采集清单 | `data/crawler/product_list.csv` | `asin,source_url,first_line` |
| 状态库 | `data/crawler/products.sqlite` | 每 ASIN 一行：状态/HTTP/尝试次数/时间/快照/哈希 |
| 页面快照 | `data/raw/pages/<ASIN>.html.gz` | 原始响应（gzip），配 SHA-256 |
| robots 缓存 | `data/crawler/robots_amazon.txt` | 合规证据 |

`product_page` 表字段：
`asin, source_url, final_url, status, http_status, attempts, fetched_at,`
`snapshot_path, content_sha256, content_length, note, updated_at`。

## 五、合规边界

- `robots.txt` **先取后用**；取不到即中止（`--ignore-robots` 仅供调试，属不合规）。
- **并发有界**：`--concurrency` 默认 2、上限 8；多线程只重叠「网络等待 / 落盘 / 解析」，
  **不提高对站点的请求频率**。
- **聚合速率由全局限速器控制**：`--delay` 是任意两次请求之间的最小间隔（与线程数无关），
  另加 `--jitter` 随机抖动避免同步突发。默认 `2 s + ≤0.8 s` ≈ **≤0.5 req/s**。
- **不实现 IP 轮换 / 代理池 / 伪造 UA**。作业文档 §四任务2 明确禁止「以绕过验证、登录或
  封禁作为完成手段」；被封禁时如实记为 `restricted`，并在报告中说明覆盖率边界。
- 不登录、不绕验证。User-Agent 明示学术用途。
- 采集时间与评论历史时间分别记录（评论时间来自源文件，采集时间记 `fetched_at`）。

## 六、已知限制与教训

1. **验证页特征不能太宽**：最初把 `api-services-support@amazon.com` 当验证页特征，
   但该字符串同时出现在亚马逊普通 404 页中，导致 404 被误判为 `validation_page`。
   已改为「先 HTTP 状态、后正文特征」，并移除该特征。
2. **200 不等于成功**：必须核对正文是否含目标 ASIN，必要时人工抽查快照。
   实测发现：数字视频类 ASIN 会返回 **Prime Video 通用落地页**（标题 `Prime Video`、正文无该 ASIN），
   这类统一记为 `parse_failed`，并把 `title=...;template=prime_video` 写入 `note` 作为模板变化证据。
3. **Amazon 可能对若干请求返回 503/验证页**；这属正常受限，应如实记为 `restricted`/`validation_page`，不得伪造成功率。
4. `data/` 目前被 `.gitignore` 排除。若课程要求把「产品采集清单」作为交付物，需要
   把清单/统计另存到非忽略目录，或 `git add -f` 指定文件（不建议提交快照本体）。

## 七、与 Scrapy 的对应关系（若课程要求 Scrapy）

本脚本是「同类工具」实现，概念一一对应：

| 本脚本 | Scrapy |
|---|---|
| `--delay` + `random` | `DOWNLOAD_DELAY` + `RANDOMIZE_DOWNLOAD_DELAY` |
| 单线程循环 | `CONCURRENT_REQUESTS = 1` |
| 指数退避重试 | `RETRY_TIMES` + `RETRY_HTTP_CODES` |
| `ensure_robots` | `ROBOTSTXT_OBEY = True` |
| SQLite 状态库 | `FEED_EXPORT` / `JOBDIR` + Pipeline |
| `RedirectRecorder` | `RedirectMiddleware` + `response.meta["redirect_urls"]` |

## 八、本次实测结果（2026-10）

### 8.1 全量 ASIN 提取（与官方统计精确吻合）

```
lines_read      = 71,205,231
productId_lines = 7,911,684     <- 与 SNAP 官方 reviews 完全一致
distinct_asin   =   253,059     <- 与 SNAP 官方 products 完全一致
invalid_asin    =         0
elapsed         =      48.2 s
```

**这是任务1 要求的「读取记录数核对」，已通过。** 全量统计与校验值见
`crawler/extract_manifest.json`（含清单 CSV 的 SHA-256 与重新生成命令）。

### 8.2 产品页采集冒烟（真实请求）

```
B003AI2VGA -> not_found (http=404)  快照 1.1 KB  title="Page Not Found"
B00006HAXW -> success   (http=200)  快照 298.7 KB title="Rock Rhythm and Doo Wop: Greatest Song"
B00004CQT3 -> success   (http=200)  快照 251.9 KB title="Far from Home: The Adventures of Yellow Dog [VHS]"
```

三条结果均带 `final_url` 与 `content_sha256`，可复核。

### 8.3 吞吐现实

253,059 页 × 3 s = **约 8.8 天**。因此应按批次采集（可按评论数取 Top-N），
并把「实时采集 / 离线快照 / 第三方补充」三条通道的分子分母分别统计。

### 8.4 100 条实采结果（concurrency=2, delay=3s）

```
elapsed          = 378.0 s           （100 目标，含重试共约 118 次请求）
success          =  87 / 100        本次成功率 87%
累计状态库(108行) = success 90 (83.3%) | not_found 11 (10.2%) | parse_failed 7 (6.5%)
restricted       =   0               ← 3 s 间隔下未被阻断
validation_page  =   0
http_status      = 200×97, 404×11
快照             = 108 个 / 19.9 MB（gzip 后平均 188.6 KB；成功页原始 HTML 平均 925.7 KB）
```

结论：
1. **限速有效**：3 s 间隔 + 并发 2 跑完 100 条，**没有触发 restricted / 验证页**；
2. **失败原因集中且可解释**：11 条 404（1997–2012 的历史商品已下架）、7 条 Prime Video 模板页；
3. **gzip 压缩比约 4:1**（925.7 KB → ~221 KB），是容量估算的关键依据。