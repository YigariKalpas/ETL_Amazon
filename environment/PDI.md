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
│   └── kettle\                             # KETTLE_HOME
│       └── .kettle\
│           └── kettle.properties           # 全局属性（可提交）
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
| `PENTAHO_DI_JAVA_OPTIONS` | `-Xmx4g -Dfile.encoding=UTF-8` | 堆上限 + **强制 UTF-8** |
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
| 内存不足 | `-Xmx4g` 偏小/偏大 | 按本机物理内存调整（见 §八） |
| 找不到数据库驱动 | 驱动未放入 `lib` | 放 JDBC jar 到 `data-integration\lib`（待 P0-3） |

## 八、待定项（不阻塞安装，但影响后续）

1. **`-Xmx` 取值**：本机物理内存无法通过沙箱探测（WMI 被拒），暂定 4 GB。
   请运行 `systeminfo | findstr /C:"Total Physical Memory"` 告我内存总量，我再定值。
   （经验：取物理内存的 1/3 ~ 1/2，且不超过 8 GB。）
2. **数据库与 JDBC 驱动**（P0-3）：确定后把驱动 jar 放入 `data-integration\lib`，
   并在 `kettle.properties` 启用 `KETTLE_TRANS_LOG_DB` 等执行日志表。
3. **许可**：CE 9.4 无激活环节；若改用 10.x/11 Developer Edition，需走 Academic Access
   申请并确认 BSL 1.1 的使用条件。

## 九、来源

- Pentaho 官方文档 · 组件参考（9.3）Java 版本要求 —— https://docs.pentaho.com/install/9.3-install/components-reference/java-virtual-machine
- Pentaho 官方文档 · 组件参考（10.2 / 11.0）Java 版本要求 —— https://docs.pentaho.com/install/10.2-install/components-reference
- Pentaho 官方文档 · 提高 PDI 内存上限（`PENTAHO_DI_JAVA_OPTIONS`）—— https://docs.pentaho.com/install/10.2-install/pentaho-configuration/
- Pentaho 官方文档 · 更改 PDI home 目录（`KETTLE_HOME` 语义）—— https://docs.pentaho.com/pdia-admin/10.2-admin/manage-the-pentaho-system/manage-the-pentaho-server/customize-the-pentaho-server/change-the-pdi-home-directory-location
- Legacy Pentaho CE 存档（SourceForge 已下架版本的镜像）—— https://github.com/ambientelivre/legacy-pentaho-ce
- Hitachi Vantara · Pentaho Community Edition 下载入口 —— https://www.hitachivantara.com/en-us/products/pentaho-plus-platform/data-integration-analytics/pentaho-community-edition.html
- Eclipse Temurin 官方 API（JDK 下载）—— https://api.adoptium.net
