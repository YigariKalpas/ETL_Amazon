# Python 运行环境（P0-1）

## 目标
为 ETL 项目提供**可复现**的 Python 3 运行时，并隔离本机两处已知陷阱。
所有实现代码、ETL 作业与文档统一使用本项目 venv，不依赖裸 `python`。

## 目录约定

| 路径 | 作用 | 是否提交 |
|---|---|---|
| `.venv/` | 项目虚拟环境（Python 3.13.14） | 否（生成物） |
| `environment/sitecustomize.py` | 沙箱兼容层源文件 | **是** |
| `scripts/bootstrap_env.cmd` | 一键引导入口（推荐） | **是** |
| `scripts/bootstrap_env.ps1` | 引导脚本本体 | **是** |
| `.tmp/` | 临时工作目录（可清空） | 否 |

## 使用方法

```cmd
rem 首次或修复环境（幂等）
scripts\bootstrap_env.cmd -Recreate

rem 日常检查
.venv\Scripts\python.exe -V
.venv\Scripts\python.exe -m pip list
```

> **统一调用方式**：`.venv\Scripts\python.exe`（绝对路径）。
> **禁止**在项目中使用裸 `python` 或裸 `pip`。

## 本机已实测的四个约束（踩坑记录）

1. **裸 `python` 是 Python 2.7.9**
   PATH 首位的 `E:\Unity_Project_Cache\python.exe` 是 Unity 残留。
   因此引导脚本使用 `python3`，并设置 `>= 3.11` 的解释器门禁。

2. **沙箱下 `os.mkdir(path, 0o700)` 会造就"不可访问目录"**
   DSH 沙箱给子进程附加受限访问控制：工作区目录 ACL 上继承一条授予受限 SID
   `S-1-4-*` 的写 ACE。而 CPython 按 mode 建目录时会生成**不继承**的收紧 DACL，
   其中不含该 SID，于是 `tempfile.mkdtemp()` 建出的目录连创建者都无法写入/列举，
   表现为 `ensurepip` 与 `pip install` 报 `PermissionError: [Errno 13]`。
   **对策**：`environment/sitecustomize.py` 在 win32 上把 `os.mkdir` 的 mode
   放宽为继承式权限；引导脚本用 `--without-pip` 建 venv，先注入该兼容层再引导 pip。

3. **执行策略禁止运行 .ps1**
   本机 PowerShell 为 **Windows PowerShell 5.1**，`ExecutionPolicy` 禁止脚本。
   `scripts/bootstrap_env.cmd` 内部以 `-ExecutionPolicy Bypass` 调用 `.ps1`，
   因此**从 `.cmd` 入口执行**即可，无需修改全局策略。
   若直接跑 `.ps1`，需先 `Set-ExecutionPolicy -Scope Process Bypass`。

4. **PS 5.1 读取无 BOM 的 UTF-8 脚本会按 ANSI 解析**
   含中文的 `.ps1` 必须存为 **UTF-8 with BOM**，否则报大量语法错误。
   `.cmd` 则保持**纯 ASCII**，避免 cmd.exe 代码页（GBK）误解析中文注释。

## 版本基线（2026-10 实测）

- 解释器：`Python 3.13.14`（Microsoft Store 版，`base_prefix` 位于 `C:\Program Files\WindowsApps\...`）
- 工具链：`pip 26.2.1`、`setuptools 84.0.0`、`wheel 0.48.0`、`packaging 26.3`
- 隔离性：`sys.prefix` = `.venv`，`include-system-site-packages = false`

## 下一步（待定，依赖 P0-2 / P0-3 决策）

- 第三方依赖尚未安装：具体清单取决于 ETL 工具与数据库选型，届时写入
  `requirements.txt` 并用 `pip install -r` 固化，**不在环境层预装无关包**。
- 建议同批补充 `.gitignore`（至少含 `.venv/`、`.tmp/`、`movies.txt*`、`*.duckdb`、`debug.log`）。
