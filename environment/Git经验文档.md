# Git 使用经验文档：仓库事故复盘与规范

> 本文复盘本项目在 Git 上连续出现的四类事故，逐条给出机制、证据与防范措施。
> 事实均来自 `git reflog` / `git log` / `git ls-tree` / `git count-objects` 的实测输出。

## 一、事故时间线（含证据）

| 时间 | 事件 | 客观证据 |
|---|---|---|
| 初次建仓 | 先 `git add .` + commit，`.gitignore` 尚不存在 | 首提交含 `movies.txt`(8.9GB) 与 `movies.txt.gz`(3.17GB)；`.git` 达 **3.39 GB** |
| — | 事后补 `.gitignore` 无效，遂**删除 .git 重建**（`.gitignore` 先行） | `.git` 3.39 GB → **0.09 MB**，跟踪文件 9 个 |
| — | PDI 阶段在 `main` 连续提交 | `e755b85` → `56703d0` 共 4 个提交 |
| — | **从旧提交 `8ed61f3` 拉 `develop` 并切换** | 7 个"main 有、develop 没有"的已跟踪文件被删除 |
| — | `git merge main` 合并回 develop | 仅在 `.gitignore` 冲突，已解 |
| — | **在 main 提交 `53c78a5 <fulfill env>`** | 该提交**只含** 1,272 个 `data/pgdata/**` 文件，并推送到 origin |
| — | 方案 A：`git rm -r --cached data` | 提交 `259e4bf`，HEAD 跟踪 data = 0 |
| — | 清理：重写 main 历史 | `8776332`，`.git` 5.37 MB → 3.62 MB；570 对象仍由 `origin/main` 持有 |
| 2026-10-19 14:03:20 | `checkout: moving from main to develop` | reflog |
| 2026-10-19 14:03:25 | `checkout: moving from develop to main` | reflog |
| 2026-10-19 14:03:32 | **`git pull origin`（默认 merge）** | 生成 merge 提交 `8e53054`，把 `53c78a5` 合并回来 |
| 同上 | 该提交被推送到 origin | `origin/main` = `8e53054`，main 重新跟踪 **1,272** 个 data 文件 |
| 事后 | 本地 `git reset --hard 8776332` 恢复干净 | HEAD 跟踪 data = 0（**远端仍未修复**） |

## 二、六个根因（按层次）

### R1　首次提交前缺 `.gitignore`，并用 `git add .` 全量暂存

**机制**：`git add .` 会把当前目录下所有未忽略文件纳入索引。仓库刚创建时 `.gitignore` 为空/不存在，
于是 12 GB 的原始数据一次性进入对象库，且**进入后极难退出**。

**证据**：`.git` 3.39 GB；`git ls-tree` 显示 `movies.txt`(8.9GB)、`movies.txt.gz`(3.17GB)。

**防范**：新仓库第一件事是写 `.gitignore`，**再** `git add`；首次提交前用 `git status` 核对。

### R2　`.gitignore` 只对「未跟踪」文件生效

**机制**：忽略规则在 `git add` 阶段生效；**已被跟踪的文件不受影响**。
因此"我加了 .gitignore 但大文件还在库里"是必然结果，不是 bug。
`git check-ignore <path>` 对已跟踪路径也会返回"未忽略"（措辞容易误导）。

**防范**：先忽略后添加；若已入库，必须 `git rm --cached`（仅移出索引）或重写历史。

### R3　从旧提交拉新分支 → 切分支会删除文件

**机制**：`git checkout` 把工作区切换到目标分支的树。
若某文件在**源分支被跟踪、目标分支不存在**，git 会把它从工作区**删除**。
从旧提交拉分支时，旧提交之后新增的文件不在新分支里，一切换就"消失"。

**证据**：`environment/PDI.md`、`scripts/pdi.cmd` 等 7 个文件在切到 `develop` 后从磁盘消失。

**防范**：新分支一律从**最新提交**起（`git switch -c <new> main`）；
切换后立即 `git status` 与 `ls` 核对；消失的文件用 `git checkout <branch> -- <paths>` 或 `git merge` 找回。

### R4　把运行期数据目录（PostgreSQL cluster）纳入版本控制

**机制**：`data/pgdata` 是**二进制、随进程写而变动、且不可移植**的运行期状态。一旦被跟踪：
① 每次启动数据库都会让 `git status` 变脏；
② 切换分支会按分支差异增删这些文件（可能直接毁掉聚类）；
③ 提交它没有任何交付价值（换机器/换路径即失效）。

**证据**：`53c78a5` 含 1,272 个 `data/pgdata/**`；后续 `git status` 出现 50+ 个 ` M data/pgdata/...`。

**防范**：`data/`、`tools/`、`.venv/`、`logs/`、`*.db`、`*.duckdb`、`*.jar/zip/gz` 一律入 `.gitignore`，
并在**任何分支**上都保持一致（本次 `main` 的 `.gitignore` 曾落后于 `develop`，正是缺口）。

### R5　重写历史后没有强推 → `git pull` 把旧历史合并回来

**机制**：重写只改变**本地**提交图。远端 `origin/main` 仍指向旧提交。
此时若执行默认的 `git pull`（= `fetch` + `merge`），git 会把远端旧提交**合并**进本地，
于是被清理的历史**原样回来了**，还多出一个 merge 提交。
清理"看起来做了，实际被静默撤销"。

**证据**：reflog `14:03:32 pull --ff ...: Merge made by the 'ort' strategy`；
`git diff 8776332 8e53054 --name-only` = 1,272 个文件，**全部是 data**。

**防范**：历史重写后**必须** `git push --force-with-lease`；
协作者（含自己的其他客户端）不得 `pull`，应 `git fetch` + `git reset --hard origin/<branch>` 或重新 clone。

### R6　本机 Git 缺 `git-remote-*` 传输助手 → 命令行无法 push/fetch

**机制**：`git --exec-path` = `E:/Git/mingw64/libexec/git-core`，该目录下 `git-remote-https.exe`、
`git-remote-http.exe`、`git-remote-curl.exe` **全部缺失（0 个 git-remote-*）**。
任何 HTTPS 远端操作都报 `git: 'remote-https' is not a git command`。
于是只能借第三方 GUI 客户端推送——而 GUI 的 pull 默认是 merge，**直接放大了 R5**。

**补充证据（同一安装的连带缺陷）**：`E:\Git\usr\bin\sh.exe` 与 `bash.exe` **均缺失**，
`E:\Git\bin\bash.exe` 只是指向不存在文件的坏 shim。这带来两个后果：
① **shell 钩子（pre-commit 等）无法 spawn**——见 §四；
② Git 内部某些依赖 sh 的辅助脚本同样不可用。

**防范**：修复安装（重装 Git for Windows 完整包，补回 `usr/bin/sh.exe`、`bash.exe` 与
`mingw64/libexec/git-core/git-remote-*.exe`）；
在修复前，凡涉及远端历史变更的操作，**先确认客户端会走 force 而不是 merge**。

## 三、正确的操作规范（可直接当检查表）

1. **仓库初始化顺序**：`.gitignore` → `git init` → `git add` → `git commit`。
2. **提交前守卫**（三条，至少做前两条）：
   - `git status --short` 人工核对清单；
   - `git ls-files -s | ...` 或 `git ls-tree -r -l HEAD` 检查是否有大文件；
   - 装 pre-commit 钩子（见 §四）。
3. **分支**：新分支从 `main` 的**最新提交**起；切分支后立刻 `git status` 与目录核对。
4. **永不入库**：`data/`、`tools/`、`.venv/`、`logs/`、`movies.txt*`、`*.db`、`*.duckdb`、`*.jar/zip/gz`。
5. **历史重写流程**：
   ```
   重写 → git push --force-with-lease origin <branch> → 其他人 fetch + reset --hard（禁止 pull）
   ```
6. **客观验证**（不要只看日志）：
   ```
   git ls-tree -r <ref> -- data        # 该引用是否跟踪 data（应为 0）
   git rev-list --objects --all | grep data/pgdata   # 还有哪些引用持有对象
   git count-objects -vH               # 对象与体积
   ```

## 四、预防措施：守卫脚本（**钩子在本机不可用**）

### 4.1 现状：shell 钩子无法运行

本机 Git 残缺（无 `usr/bin/sh.exe`/`bash.exe`），`core.hooksPath` 一旦指向钩子目录，
**每次提交都会失败**：

```
error: cannot spawn scripts/git-hooks/pre-commit: No such file or directory
```

实测已把 `core.hooksPath` **解除**，否则你当前的提交全部会被卡住。
`scripts/git-hooks/pre-commit` 仍保留在仓库中，**待 Git 修复后**用 `scripts\install_git_hooks.cmd` 启用。

### 4.2 当前可用：`scripts\git_guard.cmd`

提交前手动（或接入流程）运行：

```cmd
scripts\git_guard.cmd
```

- 退出码 `0` = 通过，`1` = 拦截；
- **路径规则**：`data/`、`tools/`、`.venv/`、`logs/`、`movies.txt*`、`*.zip/gz/7z/tar/jar`、`*.duckdb/sqlite/db/parquet/pkl`；
- **体积规则**：单个暂存文件超过 50 MB（`-MaxMB` 可调）。

已实测：合法文件放行、`data/` 下文件被拦截。

### 4.3 Git 修复后

1. 重装 Git for Windows 完整包；
2. `scripts\install_git_hooks.cmd` 启用钩子；
3. 用 `git push` 验证传输能力（能推说明 `git-remote-*` 已恢复）。

## 五、当前待办（远端仍需一次强推）

本地已恢复干净（`main` = `8776332`，跟踪 data = 0），但**远端 `origin/main` 仍是 `8e53054`（含 1,272 个 data 文件）**。
需要你执行：

```cmd
git push --force-with-lease origin main
```

推送后再收尾：

```cmd
git fetch --prune origin
git gc --prune=now
```

**关键提醒**：推送后**不要**在 main 上执行 `git pull`（那会把旧历史再合并回来，R5 复现）。
若需同步，用 `git fetch` + `git reset --hard origin/main`。

## 六、速查：本次用到的诊断命令

| 目的 | 命令 |
|---|---|
| 看全部引用与分叉 | `git log --all --graph --decorate --oneline` |
| 还原操作序列 | `git reflog --date=iso` |
| 某分支是否跟踪目录 | `git ls-tree -r --name-only <ref> -- data` |
| 哪些引用还持有对象 | `git rev-list --objects --all \| grep data/pgdata` |
| 对象与体积 | `git count-objects -vH` |
| 两个提交的差异 | `git diff <a> <b> --name-only` |
| 本机传输能力 | `git --exec-path` + 检查 `git-remote-*` |
| 修复"已跟踪但该忽略" | `git rm -r --cached <path>` |

## 七、已验证的推送配方（2026-10 实测跑通）

本机环境有三个叠加障碍，缺一不可：

| 障碍 | 现象 | 解决 |
|---|---|---|
| 系统 schannel TLS | `SEC_E_NO_CREDENTIALS` | `-c http.sslBackend=openssl` |
| 凭据助手弹 GUI | 命令挂起、无输出 | 环境变量 `GCM_GUI_PROMPT=false` + `GCM_INTERACTIVE=never` |
| HTTP/2 上传卡死 | dry-run 秒过，真实 push 挂起 | `-c http.version=HTTP/1.1` |

**完整可用命令**（在普通终端执行）：

```cmd
set GIT_TERMINAL_PROMPT=0
set GCM_INTERACTIVE=never
set GCM_GUI_PROMPT=false

tools\git\cmd\git.exe ^
  -c http.sslBackend=openssl ^
  -c credential.helper=manager ^
  -c http.version=HTTP/1.1 ^
  push --no-progress origin develop
```

实测结果：

```
9d6b56f..dbadf42  develop -> develop          （普通推送）
+ 8e53054...8776332 main -> main (forced)     （历史重写后强推）
```

收尾后 `.git` 由 3.69 MB 降至 **0.18 MB**，`git rev-list --objects --all` 中
`data/pgdata` 对象数为 **0**。