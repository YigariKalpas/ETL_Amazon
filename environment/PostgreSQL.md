# PostgreSQL 17 部署说明（P0-3 落地）

> 目标层数据库已选定 **PostgreSQL 17**（文档未指定厂商，见 `数据库选型.md`）。
> 本文记录**在本机沙箱内实测通过**的部署方式与三条硬约束。

## 一、结论与产物

| 项 | 值 |
|---|---|
| 版本 | PostgreSQL 17.11（EDB binaries 便携版） |
| 程序目录 | `tools\pgsql` |
| 数据目录 | `data\pgdata`（**必须在项目内**，工作区外写入被拒） |
| 日志目录 | `logs\`（服务端自带 logging_collector） |
| 端口/监听 | `127.0.0.1:5432` |
| 库/用户 | `etl_amazon` / `etl`（本地 trust 认证，无密码） |
| JDBC 驱动 | `postgresql-42.7.14.jar` → 放入 `data-integration\lib\` |

## 二、获取方式

```
# EDB「binaries only」归档：免管理员、不注册 Windows 服务
https://www.enterprisedb.com/download-postgresql-binaries
  -> Binaries from installer Version 17.11
  -> https://get.enterprisedb.com/postgresql/postgresql-17.11-1-windows-x64-binaries.zip  (324.9 MB)

# 解压到 tools\pgsql（压缩包顶层就是 pgsql\）
tar -xf postgresql-17.11-1-windows-x64-binaries.zip -C tools

# JDBC 驱动
https://jdbc.postgresql.org/download/postgresql-42.7.14.jar
```

## 三、日常操作（scripts\pg.cmd）

```cmd
scripts\pg.cmd serve     :: 前台运行（阻塞）；由外层终端窗口或 DSH 后台任务托管
scripts\pg.cmd status    :: 端口 + 版本自检
scripts\pg.cmd psql      :: 交互式会话
scripts\pg.cmd stop      :: 停止（已就绪时才允许）
scripts\pg.cmd reinit -ForceConfirm   :: 重建聚类（危险）
```

初始化与建库：

```cmd
tools\pgsql\bin\initdb.exe -D data\pgdata -U etl -E UTF8 --locale=C
tools\pgsql\bin\createdb.exe -h 127.0.0.1 -p 5432 -U etl -E UTF8 -T template0 etl_amazon
```

## 四、本沙箱的三条硬约束（均有实测证据）

### 4.1 pg_ctl 完全不可用

```
pg_ctl start : could not create restricted token: error code 87 / could not start server: error code 3
pg_ctl stop  : could not send stop signal (PID: ...): Operation not permitted
pg_ctl kill  : could not send signal 2 (PID: ...): Operation not permitted
```

原因：DSH 沙箱为子进程附加受限令牌，而 pg_ctl 在 Windows 上会**再创建一层**受限令牌。
注意 `initdb` 遇到同样问题会**自动降级并成功**，`pg_ctl` 不会——两者行为不同，不要因为 initdb 成功就认为 pg_ctl 也能用。

### 4.2 只有「长生命周期父进程」托管的 postgres 才能正常启动

| 启动方式 | 结果 |
|---|---|
| 后台任务直接子进程 | **1 秒就绪** ✅ |
| 分离 / 脱离作业对象（`CREATE_BREAKAWAY_FROM_JOB`、`DETACHED_PROCESS`） | `startup process exited with exit code 1`（约 67 秒后） ❌ |

原因：DSH 用 Job Object 管理工具调用的进程树；脱离作业对象的进程虽能存活，
但 postmaster 创建 startup 子进程的链路不完整，导致 startup 退出。
→ 沙箱内用 `pg.cmd serve`（前台）并交给外层托管。

### 4.3 只能在「已就绪」时结束进程

在启动/恢复期间强杀会导致聚类损坏，之后恢复失败、**必须 reinit**。实测日志：

```
database system was interrupted while in recovery at ...
HINT: This probably means that some data is corrupted and you will have to use the last backup
... 之后永久卡在 "the database system is starting up"，postmaster.pid 状态为 starting
```

→ `pg.cmd stop` 会先探测就绪，**未就绪则拒绝执行**，以结构上避免这个坑。

## 五、能力自检（已实测通过）

```sql
create temp table p(id int primary key, n text);
create temp table c(id int primary key, ref int references p(id));
insert into p values (1, 'a');
insert into c values (1, 1);
do $$ begin
  begin insert into c values (2, 999); raise notice 'FK NOT ENFORCED';
  exception when foreign_key_violation then raise notice 'FK ENFORCED OK';
  end;
end $$;
select (array['drama','scifi'])[2];        -- scifi   多值字段
select ('{"a":1}'::jsonb)->>'a';        -- 1       半结构化
```

结果：`NOTICE: FK ENFORCED OK`，数组与 JSONB 均可用。

## 六、与 PDI 的对接

- PDI **自带** `postgresql-42.2.23.jar`；为 PG 17 兼容性改用 **42.7.14**，
  并把旧驱动移出 `lib\`，避免同一驱动出现两个版本造成类路径歧义。
- 连接串：`jdbc:postgresql://127.0.0.1:5432/etl_amazon`，用户 `etl`，本地 trust 认证无密码。
- 驱动生效需重启 Spoon/Pan/Kitchen。

## 七、排错速查

| 现象 | 原因 | 处置 |
|---|---|---|
| `could not create restricted token: error code 87` | 沙箱受限令牌（仅 pg_ctl） | 改用 `pg.cmd serve` |
| `connection refused 10061` | 服务未运行，或启动期已退出 | `pg.cmd status` 查看；必要时 `serve` 前台观察输出 |
| 永久 `the database system is starting up` | 聚类在恢复期被强杀而损坏 | `pg.cmd reinit -ForceConfirm` 后重新建库 |
| `startup process exited with exit code 1` | 用了分离/脱离方式启动 | 改用前台 `serve` 托管 |

## 八、来源

- PostgreSQL 官方 Windows 下载：https://www.postgresql.org/download/windows/
- EDB PostgreSQL Binaries（便携归档）：https://www.enterprisedb.com/download-postgresql-binaries
- PostgreSQL JDBC：https://jdbc.postgresql.org/download/