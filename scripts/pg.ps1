<#
.SYNOPSIS
    项目内 PostgreSQL 17 控制脚本（serve / start / stop / status / psql / reinit）。

.DESCRIPTION
    本机（DSH 沙箱）实测到的硬约束，决定了本脚本的形态：

    1) pg_ctl 在沙箱内不可用
         pg_ctl start : could not create restricted token: error code 87 / could not start server: error code 3
         pg_ctl stop  : could not send stop signal: Operation not permitted
         pg_ctl kill  : could not send signal 2: Operation not permitted
       initdb 遇到同样问题会【自动降级】并成功；pg_ctl 不会。直接运行 postgres.exe 不受影响。

    2) 只有「长生命周期父进程」托管的 postgres 才能正常启动
         后台任务方式（直接子进程）  : 1 秒就绪   OK
         分离 / 脱离作业对象         : startup process exited with exit code 1  FAIL
       -> 沙箱内应以【前台 serve】方式运行，由外层（DSH 后台任务或终端窗口）托管。

    3) 只能在「已就绪」时结束进程
         在启动/恢复期间强杀会导致聚类损坏，之后恢复失败、必须 reinit。
         （实测日志：database system was interrupted while in recovery -> 卡在 starting）
       -> 本脚本的 stop 会先探测就绪，未就绪则拒绝执行。

    数据目录必须在项目内（工作区外写入被拒绝）。

.PARAMETER Action
    serve  前台运行 postgres（阻塞；由外部托管）
    start  先试 pg_ctl（普通机器可用），失败则提示改用 serve
    stop   先试干净停止，失败则回退结束进程（仅当已就绪）
    status 端口 + 版本自检
    psql   打开交互式会话
    reinit 删除并重建聚类（危险，需 -ForceConfirm）
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('serve', 'start', 'stop', 'status', 'psql', 'reinit')]
    [string]$Action = 'status',
    [string]$ProjectRoot,
    [int]$Port = 5432,
    [string]$DbName = 'etl_amazon',
    [string]$SuperUser = 'etl',
    [switch]$ForceConfirm
)

# 注意：本脚本大量调用原生命令（initdb/psql/pg_ctl），它们在 PS 5.1 下会往 stderr 写
# 正常信息（如 initdb 的 restricted token 警告），若用 'Stop' 会被当成终止错误而中断。
# 故这里用 'Continue'，关键步骤改为检查 $LASTEXITCODE 并显式 throw。
$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false

if (-not $ProjectRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
}

$pgBin   = Join-Path $ProjectRoot 'tools\pgsql\bin'
$pgData  = Join-Path $ProjectRoot 'data\pgdata'
$logDir  = Join-Path $ProjectRoot 'logs'
$pidFile = Join-Path $pgData 'postmaster.pid'
$psqlExe = Join-Path $pgBin 'psql.exe'

function Test-Listening([int]$P) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $P); $c.Close(); return $true
    } catch { return $false }
}

function Test-Ready([int]$P) {
    if (-not (Test-Listening $P)) { return $false }
    try {
        $null = & $psqlExe -h 127.0.0.1 -p $P -U $SuperUser -d postgres -tAc "select 1;" 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Get-PgPid {
    if (Test-Path -LiteralPath $pidFile) {
        $l = (Get-Content -LiteralPath $pidFile -TotalCount 1).Trim()
        if ($l -match '^\d+$') { return [int]$l }
    }
    return $null
}

switch ($Action) {

    'serve' {
        $exe = Join-Path $pgBin 'postgres.exe'
        if (-not (Test-Path -LiteralPath $exe)) { throw ("missing postgres.exe: " + $exe) }
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        Write-Host ("前台运行 PostgreSQL (port " + $Port + ")，Ctrl+C 结束")
        $pgArgs = @(
            '-D', $pgData,
            '-p', "$Port",
            '-c', 'listen_addresses=127.0.0.1',
            '-c', 'logging_collector=on',
            '-c', ("log_directory=" + $logDir),
            '-c', 'log_filename=pg-%Y-%m-%d.log'
        )
        & $exe @pgArgs
    }

    'start' {
        if (Test-Ready $Port) { Write-Host ("已在运行且已就绪 (port " + $Port + ")"); break }
        Write-Host '尝试 pg_ctl start（普通机器可用）...'
        $ctlArgs = @(
            '-D', $pgData,
            '-l', (Join-Path $logDir 'pg-ctl.log'),
            '-o', ("-p " + $Port + " -c listen_addresses=127.0.0.1"),
            '-w', 'start'
        )
        & (Join-Path $pgBin 'pg_ctl.exe') @ctlArgs 2>&1 | ForEach-Object { Write-Host ("  " + $_) }
        if (Test-Ready $Port) { Write-Host '已启动' -ForegroundColor Green; break }
        Write-Host ''
        Write-Host 'pg_ctl 在本沙箱不可用（restricted token 冲突）。请改用：' -ForegroundColor Yellow
        Write-Host '  scripts\pg.cmd serve    # 在一个终端窗口前台运行' -ForegroundColor Yellow
        Write-Host '  或由 DSH 以后台任务方式运行同一命令' -ForegroundColor Yellow
    }

    'stop' {
        if (-not (Test-Listening $Port)) { Write-Host '未在运行'; break }
        if (-not (Test-Ready $Port)) {
            throw '服务处于启动/恢复中，此时结束进程会损坏聚类。请等待就绪，或执行 reinit。'
        }
        Write-Host '尝试 pg_ctl stop -m fast ...'
        $ctlArgs = @('-D', $pgData, '-m', 'fast', 'stop')
        & (Join-Path $pgBin 'pg_ctl.exe') @ctlArgs 2>&1 | ForEach-Object { Write-Host ("  " + $_) }
        if (-not (Test-Listening $Port)) { Write-Host '已干净停止' -ForegroundColor Green; break }

        Write-Host 'pg_ctl stop 不可用，回退为结束进程（已就绪，崩溃恢复可正常处理）' -ForegroundColor Yellow
        $pgPid = Get-PgPid
        if ($pgPid) { Stop-Process -Id $pgPid -Force -ErrorAction SilentlyContinue }
        Get-Process postgres -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        Write-Host ("port " + $Port + " listening = " + (Test-Listening $Port))
    }

    'status' {
        Write-Host ("PGDATA = " + $pgData)
        Write-Host ("PORT   = " + $Port)
        Write-Host ("LISTEN = " + (Test-Listening $Port))
        Write-Host ("READY  = " + (Test-Ready $Port))
        Write-Host ("PID    = " + (Get-PgPid))
        if (Test-Ready $Port) {
            $v = & $psqlExe -h 127.0.0.1 -p $Port -U $SuperUser -d postgres -tAc "select version();" 2>&1
            Write-Host ("  " + (($v | Out-String).Trim()))
        }
    }

    'psql' {
        & $psqlExe -h 127.0.0.1 -p $Port -U $SuperUser -d $DbName
    }

    'reinit' {
        if (-not $ForceConfirm) { throw 'reinit 会删除整个聚类，需显式加 -ForceConfirm' }
        Get-Process postgres -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        Remove-Item -LiteralPath $pgData -Recurse -Force -ErrorAction SilentlyContinue
        & (Join-Path $pgBin 'initdb.exe') -D $pgData -U $SuperUser -E UTF8 --locale=C 2>&1 | ForEach-Object { Write-Host ("  " + $_) }
        if ($LASTEXITCODE -ne 0) { throw ("initdb 失败，exit=" + $LASTEXITCODE) }
        if (-not (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION'))) { throw 'initdb 后未找到 PG_VERSION' }
        Write-Host '聚类已重建；用 serve 启动后再建库' -ForegroundColor Green
    }
}
