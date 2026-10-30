# ETL 作业框架（PDI / Kettle 9.4）

> 生成器：crawler/gen_kettle_job.py　作业：etl/jobs/j_etl_pipeline.kjb　冒烟：etl/jobs/j_smoke.kjb
> 每一步的包装脚本：etl/jobs/steps/*.cmd（纯 ASCII）

## 一、为什么这样组织

作业 §八 要求：使用开源 ETL 工具组织抽取、转换、装载与质量检查，
提交**作业定义、依赖关系、参数、执行日志和失败分支**；
爬虫与复杂匹配可用 Python 实现，但**必须由 ETL 作业调用**——不能只交一堆互不相连的脚本。

因此框架做成两层：

| 层 | 文件 | 职责 |
|---|---|---|
| 作业层 | j_etl_pipeline.kjb | 串起 12 个步骤、定义成功/失败分支、写执行日志 |
| 步骤层 | steps/NN_*.cmd | 每步一个 ASCII 包装，调用对应 Python 脚本并透传退出码 |
| 实现层 | crawler/*.py | 真实逻辑（解析、分类、实体、血缘、去重、落库） |

这样 Kettle 只承担“编排 + 日志 + 失败分支”，业务逻辑仍在可测试的 Python 里，
既满足作业对 ETL 工具的要求，也避免把匹配算法写进 Kettle 组件。

## 二、作业结构

    Start
      └─> 01_parse_pages ─┐
          02_classify     │  任一步失败
          03_movie_entities│  ────────> Failure（写入日志并结束）
          04_person_entities
          05_field_assertions
          06_build_target
          07_release_dates
          08_field_conflicts
          09_dedup_reviews
          10_build_target2
          11_load_pg
          12_verify_target ─> Success

失败分支：SHELL 项设置 `<failOnError>Y` 语义（非 0 退出即算失败），
每个步骤都有一条 `evaluation=N` 的跳转到 Failure。

**例外（PG 非阻断）**：`11_load_pg` 失败不再中断整条作业——它有一条专门的失败跳转到 `WARN_pg_unavailable`（DUMMY 节点），该节点再无条件跳回 `12_verify_target`。这样 PostgreSQL 不可用时 SQLite 侧仍完成建表、去重与自检，避免「PG 挂掉导致整轮 ETL 失败」的连锁损失。

    Start → ... → 10_build_target2 → 11_load_pg ─(成功)→ 12_verify_target
                                        └─(失败)→ WARN_pg_unavailable → 12_verify_target

## 三、参数

| 参数 | 默认 | 说明 |
|---|---|---|
| PROJECT_ROOT | E:/ETL_Amazon | 项目根目录（.cmd 也自行推导，双保险） |

> 注意：通过 pdi.cmd 传 -param:X=Y 时，cmd 的参数收集会把 = 两侧拆开，
> Kitchen 会报一次「Command Line Options」（退出码变 1，但作业仍按默认值跑完）。
> 需要改参数时直接改 .kjb 的 <default_value> 后重新生成，或用引号整体包裹。

运行时覆盖：

    scripts\pdi.cmd kitchen /file:E:\ETL_Amazon\etl\jobs\j_etl_pipeline.kjb -param:PROJECT_ROOT=E:/ETL_Amazon

## 四、执行日志

| 日志 | 内容 |
|---|---|
| logs/pdi_<step>.log | 每个步骤的 stdout/stderr（Kettle 的 set_logfile=Y） |
| logs/pdi_full.log | Kitchen 主日志（步骤开始/结束/结果） |
| logs/pipeline.log | Python 管线自身写的批次日志 |

## 五、运行方式

    # 冒烟：只跑一步校验，验证作业/Shell/分支接线（约 1 秒）
    scripts\pdi.cmd kitchen /file:E:\ETL_Amazon\etl\jobs\j_smoke.kjb

    # 全量：跑完 12 步（约 10-15 分钟，取决于采集与评论量）
    scripts\pdi.cmd kitchen /file:E:\ETL_Amazon\etl\jobs\j_etl_pipeline.kjb

    # 修改流程后重新生成（步骤清单在 crawler/gen_kettle_job.py 的 STEPS）
    .venv\Scripts\python.exe crawler\gen_kettle_job.py

## 六、已验证的事实

- Kitchen 能加载作业并执行 SHELL 项（`j_smoke` 退出码 0，输出落 logs/pdi_00_verify.log）；
- .cmd 必须纯 ASCII（含规则文件路径），因此把中文名规则复制成 design/classify_rules_v0.4.json；
- 作业 XML 依据 PDI 9.4 自带样例的结构（SPECIAL 用 start/dummy，SHELL 用 filename/exec_per_row/set_logfile）。

## 七、扩展方式

新增一步只需：

1. 在 crawler/gen_kettle_job.py 的 STEPS 里加一行 `(步骤名, 脚本名, 额外参数, 说明)`；
2. 重新运行生成器；
3. 用 j_smoke 的同一套分支结构即可保证失败可中断。
