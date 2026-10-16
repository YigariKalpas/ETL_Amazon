# PDI / Kettle 运行环境配置（P0-2）

> 本文是本项目 ETL 工具（Pentaho Data Integration CE）的**具体环境配置方法**。
> 所有结论均来自官方文档或实测，末尾附来源。

## 一、最终选型（已确定）

| 项 | 取值 | 理由 |
|---|---|---|
| ETL 工具 | **PDI CE 9.4.0.0-343** | 当前**可自由获取的最新 CE**（见 §二.2） |
| 运行 JVM | **Eclipse Temurin JDK 11**（便携 zip） | PDI 9.x **认证 Java 11**；与系统 Java 25 隔离 |
| 安装位置 | `<project>\tools\jdk-11`、`<project>\tools\pdi-ce-9.4.0.0-343` | 无空格路径，项目内自包含 |
| 配置目录 | `<project>\etl\kettle\.kettle` | `KETTLE_HOME` 项目内化，可提交、可复现 |
| 系统影响 | **零** | 只注入进程级环境变量，不改系统 JAVA_HOME/PATH |

**核心设计**：PDI 启动脚本**先读 `PENTAHO_JAVA_HOME`，其次才读 `JAVA_HOME`**。
因此把 `PENTAHO_JAVA_HOME` 指向项目内的 JDK 11，就能让 PDI 跑在 Java 11 上，
而本机原有的 **Java 25 完全不受影响**——这正是"多 JDK 共存"的标准做法。

## 二、三个必须知道的事实（决定了上面为什么不"直接装最新版"）

### 1. 本机 Java 25 **不被任何版本的 PDI 支持**

| PDI 版本 | 认证 Java | 支持 Java |
|---|---|---|
| 9.3 | Oracle Java 11.0.13 | 8.x / 11.x |
| 10.2 | Oracle Java 17 | 11.x / 17.x |
| 11.0 | Oracle Java 21 | 17 / 21 |

本机 `java -version` = **25.0.4.1**，且 `JAVA_HOME`/`PENTAHO_JAVA_HOME` **均未设置**。
若不处理，Spoon 启动会因 JVM 版本不兼容而失败（类/模块错误或直接闪退）。
→ **必须为 PDI 单独准备 JDK 11，并用 `PENTAHO_JAVA_HOME` 指定。**

### 2. 你提供的下载渠道已过时：SourceForge 上的新版 CE 已被下架

实测：`https://sourceforge.net/projects/pentaho/files/` 已**不再列出 9.2/9.3/9.4/10.x** 目录
（探测 `/Pentaho 9.2`、`/Pentaho 9.4`、`/Pentaho 10.2` 等路径均返回 404），
官方存档页明确写道：

> *"Older versions of pentaho have been removed from sourceforge.net/projects/pentaho."*

而 Hitachi 官方 CE 页面当前**只提供 9.4 / 9.3 / 9.2 / 8.3**。
自 **10.x 起**产品改名为 **Developer Edition**，采用 **BSL 1.1 许可**（禁止生产使用，
学习/开发/测试可用），且**需经授权申请**（学生走 Academic Access Program），
不再是可自由下载的 CE。

→ 本项目固定使用 **CE 9.4.0.0-343**（最后一个可自由获取的 CE，Apache 许可、无激活环节）。
可用镜像：`https://github.com/ambientelivre/legacy-pentaho-ce`（已实测 HTTP 200，367.7 MB）。

### 3. 路径不能含空格

官方明确提示：**"Spaces in the path are a classic cause of odd startup failures."**
本方案路径 `E:\ETL_Amazon\tools\...` 无空格，已满足。

## 三、目录规划

```
E:\ETL_Amazon\
├── tools\                                  # 第三方运行时（已 gitignore，可整体删除重建）
│   ├── downloads\                          # 原始 zip + 校验值
│   ├── jdk-11\                             # Temurin 11 便携版
│   └── pdi-ce-9.4.0.0-343\
│       └── data-integration\               # Spoon.bat / Pan.bat / Kitchen.bat / lib / plugins
├── etl\
│   ├── kettle\                             # KETTLE_HOME
│   │   ├── .kettle\kettle.properties       # 全局属性（可提交）
│   │   └── .pentaho\metastore\             # 元数据存储（-DPENTAHO_METASTORE_FOLDER 重定向）
│   └── transformations\                    # ETL 作业与转换定义（.ktr / .kjb）
└── scripts\
    ├── setup_pdi.cmd / setup_pdi.ps1       # 下载 + 解压 + 验证
    └── pdi.cmd                             # 启动器（注入环境）
```

## 四、具体配置步骤

### 步骤 0　一键完成（推荐）

```cmd
scripts\setup_pdi.cmd
```

脚本会：下载 Temurin JDK 11 → 下载 PDI CE 9.4 → 解压到无空格路径 →
创建 `KETTLE_HOME\.kettle` → 打印每个 zip 的 **大小与 SHA-256** → 验证三个启动脚本存在。
重复执行是幂等的（已存在的组件跳过）；加 `-Force` 可强制重下。

### 步骤 1　手动方式（等价操作，便于理解）

```powershell
# 1) 便携 JDK 11（免安装、免管理员；URL 走官方 API，永远取最新 11 GA）
Invoke-WebRequest -Uri "https://api.adoptium.net/v3/binary/latest/11/ga/windows/x64/jdk/hotspot/normal/eclipse" `
  -OutFile "$env:TEMP\temurin-jdk-11.zip"
Expand-Archive "$env:TEMP\temurin-jdk-11.zip" -DestinationPath "E:\ETL_Amazon\tools" -Force
# 解压后目录名形如 jdk-11.0.32.1+1，重命名为 jdk-11
Rename-Item "E:\ETL_Amazon\tools\jdk-11.0.32.1+1" "jdk-11"

# 2) PDI CE 9.4
Invoke-WebRequest -Uri "https://github.com/ambientelivre/legacy-pentaho-ce/releases/download/pdi-ce-9.4.0.0-343/pdi-ce-9.4.0.0-343.zip" `
  -OutFile "$env:TEMP\pdi-ce-9.4.0.0-343.zip"
Expand-Archive "$env:TEMP\pdi-ce-9.4.0.0-343.zip" -DestinationPath "E:\ETL_Amazon\tools" -Force
```

### 步骤 2　设置环境变量（**进程级，不改系统**）

| 变量 | 取值 | 作用 |
|---|---|---|
| `PENTAHO_JAVA_HOME` | `<project>\tools\jdk-11` | **PDI 优先读取**，指向 JDK 11 |
| `JAVA_HOME` | 同左 | 供 Pan/Kitchen 及子进程复用 |
| `PENTAHO_DI_HOME` | `...\pdi-ce-9.4.0.0-343\data-integration` | PDI 安装目录 |
| `KETTLE_HOME` | `<project>\etl\kettle` | **只填到 `.kettle` 的父目录**（官方要求） |
| `PENTAHO_DI_JAVA_OPTIONS` | `-Xmx6g -Dfile.encoding=UTF-8` | 堆上限（16 GB 物理内存的 ~3/8）+ **强制 UTF-8** |
| `PATH` | 前置 `%JAVA_HOME%\bin` | 使该进程内 `java` = 11 |

**为什么必须加 `-Dfile.encoding=UTF-8`**：Java 11 在中文 Windows 上默认字符集是 GBK，
而本次源数据是 UTF-8 文本。不加会导致中文/特殊字符乱码。**这是本项目最容易踩的隐性坑。**

**为什么 `KETTLE_HOME` 不写 `.kettle`**：官方原文——
*"You can specify a single, universal `.kettle` directory ... When declaring the variable, leave out the `.kettle`."*
即 PDI 会自动在 `KETTLE_HOME` 之下找 `.kettle`。

### 步骤 3　启动与验证

```cmd
scripts\pdi.cmd status      :: 打印注入后的环境 + java -version + 关键文件检查
scripts\pdi.cmd spoon       :: 启动图形化设计器 Spoon
scripts\pdi.cmd pan     trans.ktr -param:x=1    :: 命令行跑转换
scripts\pdi.cmd kitchen job.kjb                 :: 命令行跑作业
```

验收要点：`pdi.cmd status` 中 `java -version` 必须显示 **11**（不是 25）；
Spoon 能正常打开主画布。

## 五、命令行工具（对应作业的"可重复运行"）

作业要求"作业定义、依赖关系、参数、执行日志和失败分支"，且"只交若干不相连的脚本
不能证明已实现完整 ETL"。PDI 天然提供：

| 工具 | 用途 | 对应作业要求 |
|---|---|---|
| `Spoon.bat` | 图形化设计器（开发期） | 产出 `.ktr`/`.kjb` 作业定义 |
| `Pan.bat` | 命令行执行转换 | 可重复运行、参数化 |
| `Kitchen.bat` | 命令行执行作业（含失败分支/依赖） | ETL 编排、日志、失败分支 |
| `Carte` | 远程执行/调度（可选） | 长期运行与并行 |

**注意**：`Pan.bat`/`Kitchen.bat` 读取 `Spoon.bat` 中的内存设置，
故 `PENTAHO_DI_JAVA_OPTIONS` 对三者一致生效。

## 六、与 Python（P0-1）的协作

作业文档允许"爬虫与复杂匹配可用 Python 等实现，并由 ETL 作业调用"。
PDI 侧用 **"Execute a process"/"Shell"** 步骤调用：

```cmd
<E:\ETL_Amazon\.venv\Scripts\python.exe> <脚本.py> --args ...
```

务必用 **venv 内的 python 绝对路径**，不要用裸 `python`（本机裸 `python` 是 2.7.9）。

## 七、常见报错与处置

| 现象 | 原因 | 处置 |
|---|---|---|
| 双击 Spoon.bat 窗口一闪而过 | JVM 版本不符（常因误用 Java 25） | 用 `pdi.cmd spoon`，或查日志 `data-integration\logs` |
| 启动报 `Unsupported class file major version` | 同上 | 确认 `PENTAHO_JAVA_HOME` 指向 JDK 11 |
| 中文乱码 | 缺 `-Dfile.encoding=UTF-8` | 已在 `PENTAHO_DI_JAVA_OPTIONS` 中设置 |
| 路径含空格导致异常 | 官方已知问题 | 已规避（`E:\ETL_Amazon` 无空格） |
| `Cannot find Java` | 环境变量未注入 | 必须经 `pdi.cmd` 启动，直接双击不会注入 |
| 内存不足 | `-Xmx6g` 偏小/偏大 | 按本机物理内存调整（见 §八） |
| 找不到数据库驱动 | 驱动未放入 `lib` | 放 JDBC jar 到 `data-integration\lib`（待 P0-3） |

## 八、待定项（不阻塞安装，但影响后续）

1. **`-Xmx` 取值**：本机物理内存 **16 GB**（已确认）。取 ~3/8 定为 **`-Xmx6g`**，
   给后续可能并行的 Python / 数据库留出余量；如遇大排序/查找步骤 OOM，可上调至 `-Xmx8g`（上限）。
   注意：9.3 GB 源文件必须走 **流式**读取，不要整体驻留堆。
2. **数据库与 JDBC 驱动**（P0-3）：确定后把驱动 jar 放入 `data-integration\lib`，
   并在 `kettle.properties` 启用 `KETTLE_TRANS_LOG_DB` 等执行日志表。
3. **许可**：CE 9.4 无激活环节；若改用 10.x/11 Developer Edition，需走 Academic Access
   申请并确认 BSL 1.1 的使用条件。

## 九、实测验证与踩坑记录（本次安装）

### 9.1 验证结果：PDI 已在 Java 11 上成功执行转换

```
$ scripts\pdi.cmd pan -file:...\etl\transformations\smoke_test.ktr -level:Basic
DEBUG: Using PENTAHO_JAVA_HOME
DEBUG: _PENTAHO_JAVA_HOME=E:\ETL_Amazon\tools\jdk-11
...
2026/10/16 08:07:04 - Pan - Kettle 版本9.4.0.0-343, 编译日期 : 2022-11-08 07.50.27
2026/10/16 08:07:04 - Generate Rows.0 - 完成处理 (I=0, O=0, R=0, W=10, U=0, E=0)
2026/10/16 08:07:04 - Dummy (do nothing).0 - 完成处理 (I=0, O=0, R=10, W=10, U=0, E=0)
2026/10/16 08:07:04 - Pan - 完成!
PAN EXIT = 0
```

`DEBUG: Using PENTAHO_JAVA_HOME` 是**决定性证据**：PDI 用的是项目内 JDK 11，
而非系统 Java 25；且 `-Xmx6g`、`-Dfile.encoding=UTF-8`、`-DKETTLE_HOME` 均已注入。

### 9.2 坑 1：metastore 被写到用户主目录（沙箱拒绝）

现象：
```
Unable to create XML meta store root folder: C:\Users\<user>\.pentaho\metastore
org.pentaho.metastore.api.exceptions.MetaStoreException
```

**定位方法**：用 JDK 自带的 `javap` 反汇编 `kettle-core` 中的 `MetaStoreConst`：
```cmd
tools\jdk-11\bin\javap.exe -p -c -cp tools\pdi-ce-9.4.0.0-343\data-integration\lib\kettle-core-9.4.0.0-343.jar org.pentaho.di.metastore.MetaStoreConst
```
字节码显示：
```
ldc           PENTAHO_METASTORE_FOLDER
invokestatic  java/lang/System.getProperty      <- 是 getProperty，不是 getenv！
              getDefaultPentahoMetaStoreLocation() = System.getProperty("user.home") + ".pentaho"
              new File(<folder>, "metastore")    <- PDI 还会自行追加 metastore
```

**结论**：`PENTAHO_METASTORE_FOLDER` 是 **JVM 系统属性**，不是环境变量；且 PDI 会在其后追加 `\metastore`。
正确写法（已落在 `scripts\pdi.cmd`）：
```
-DPENTAHO_METASTORE_FOLDER=<project>\etl\kettle\.pentaho
```
实际落盘 `<project>\etl\kettle\.pentaho\metastore`。

> 教学点：类文件常量池里出现一个全大写名字，**不代表它是环境变量**。
> `System.getenv` 与 `System.getProperty` 是两条完全不同的通道，只有反汇编或源码才能确认。

### 9.3 坑 2：访问 GitHub 必须走系统代理

本机 `HKCU\...\Internet Settings`：`ProxyEnable=1`，`ProxyServer=127.0.0.1:7897`。

| 通道 | 实测 |
|---|---|
| GitHub 直连 | ❌ 超时 `WinError 10060` / 约 32 KB/s |
| GitHub 经系统代理 | ✅ 约 0.36 MB/s |
| 清华 TUNA 镜像直连（JDK） | ✅ 约 1.5 MB/s |

因此 `scripts/fetch_url.py` 把"是否走代理"做成显式开关（`--proxy`）并支持**断点续传**；
`setup_pdi.ps1` 自动读取注册表代理，并按来源选择通道（GitHub 代理优先、国内镜像直连优先）。

### 9.4 坑 3：PowerShell / cmd 的编码与错误处理

| 现象 | 原因 | 处置 |
|---|---|---|
| `.ps1` 报大量语法错误 | PS 5.1 读无 BOM 的 UTF-8 会按 ANSI(GBK) 解析 | `.ps1` 一律存为 **UTF-8 with BOM** |
| `.cmd` 报 `'xx' is not recognized` | cmd.exe 按 OEM 代码页解析，非 ASCII 注释错位 | `.cmd` **保持纯 ASCII** |
| 脚本在验证阶段误报失败 | PS 5.1 下原生命令写 stderr 会被 `$ErrorActionPreference='Stop'` 当成终止错误（`java -version` 正常写 stderr） | 局部放宽或捕获输出 |

## 十、来源

- Pentaho 官方文档 · 组件参考（9.3）Java 版本要求 —— https://docs.pentaho.com/install/9.3-install/components-reference/java-virtual-machine
- Pentaho 官方文档 · 组件参考（10.2 / 11.0）Java 版本要求 —— https://docs.pentaho.com/install/10.2-install/components-reference
- Pentaho 官方文档 · 提高 PDI 内存上限（`PENTAHO_DI_JAVA_OPTIONS`）—— https://docs.pentaho.com/install/10.2-install/pentaho-configuration/
- Pentaho 官方文档 · 更改 PDI home 目录（`KETTLE_HOME` 语义）—— https://docs.pentaho.com/pdia-admin/10.2-admin/manage-the-pentaho-system/manage-the-pentaho-server/customize-the-pentaho-server/change-the-pdi-home-directory-location
- Legacy Pentaho CE 存档（SourceForge 已下架版本的镜像）—— https://github.com/ambientelivre/legacy-pentaho-ce
- Hitachi Vantara · Pentaho Community Edition 下载入口 —— https://www.hitachivantara.com/en-us/products/pentaho-plus-platform/data-integration-analytics/pentaho-community-edition.html
- Eclipse Temurin 官方 API（JDK 下载）—— https://api.adoptium.net
