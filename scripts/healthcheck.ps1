<#
.SYNOPSIS
    项目环境自检（Python / PDI / PostgreSQL / Data / Git / Disk）。

.DESCRIPTION
    输出一张 OK/FAIL 表格与汇总，用于：
      - 提交材料中的「环境与资源需求」「端到端运行步骤」验证；
      - 每次开工前确认环境未被破坏。

    只做只读检查；Pan 冒烟测试会真实执行一次转换（可用 -SkipPan 跳过）。
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [switch]$SkipPan
)

$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false

if (-not $ProjectRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
}

$results = New-Object System.Collections.Generic.List[object]
function Add-Check([string]$Area, [string]$Item, [bool]$Ok, [string]$Detail) {
    $results.Add([pscustomobject]@{ Area = $Area; Item = $Item; Ok = $Ok; Detail = $Detail }) | Out-Null
}

# ---------- 1. Python ----------
$py = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$pyOk = Test-Path -LiteralPath $py
Add-Check 'Python' 'venv 解释器' $pyOk $(if ($pyOk) { $py } else { '缺失，运行 scripts\bootstrap_env.cmd' })

if ($pyOk) {
    $ver = (& $py -c "import sys;print('%d.%d.%d' % sys.version_info[:3])" 2>&1 | Out-String).Trim()
    Add-Check 'Python' '版本 >= 3.11' ([version]$ver -ge [version]'3.11.0') $ver

    $iso = (& $py -c "import sys;print(sys.prefix != sys.base_prefix)" 2>&1 | Out-String).Trim()
    Add-Check 'Python' 'venv 隔离生效' ($iso -eq 'True') ("sys.prefix 独立 = " + $iso)

    $shim = (& $py -c "import os,tempfile;d=tempfile.mkdtemp();open(os.path.join(d,'x'),'w').write('1');print('ok')" 2>&1 | Out-String).Trim()
    Add-Check 'Python' '沙箱兼容层(tempfile)' ($shim -eq 'ok') $(if ($shim -eq 'ok') { 'tempfile 可用' } else { 'tempfile 失败，检查 environment/sitecustomize.py' })

    $pkgs = (@(& $py -m pip list --format=freeze 2>&1 | Where-Object { $_ -match '==' })).Count
    Add-Check 'Python' '已装依赖数' ($pkgs -ge 4) ($pkgs.ToString() + ' 个包')
}

# ---------- 2. PDI ----------
$di = Join-Path $ProjectRoot 'tools\pdi-ce-9.4.0.0-343\data-integration'
$bats = @('Spoon.bat', 'Pan.bat', 'Kitchen.bat')
$missing = @($bats | Where-Object { -not (Test-Path -LiteralPath (Join-Path $di $_)) })
Add-Check 'PDI' '启动脚本齐全' ($missing.Count -eq 0) $(if ($missing.Count -eq 0) { 'Spoon/Pan/Kitchen 均存在' } else { '缺失: ' + ($missing -join ',') })

$jdk = Join-Path $ProjectRoot 'tools\jdk-11\bin\java.exe'
if (Test-Path -LiteralPath $jdk) {
    $jv = (& $jdk -version 2>&1 | Select-Object -First 1 | Out-String).Trim()
    Add-Check 'PDI' 'JDK 11 就绪' ($jv -match '11\.') $jv
} else {
    Add-Check 'PDI' 'JDK 11 就绪' $false '缺失 tools\jdk-11'
}

$lib = Join-Path $di 'lib'
$pgJars = @(Get-ChildItem -LiteralPath $lib -Filter 'postgresql-*.jar' -ErrorAction SilentlyContinue)
Add-Check 'PDI' 'JDBC 驱动唯一' ($pgJars.Count -eq 1) $(if ($pgJars.Count -eq 1) { $pgJars[0].Name } else { ($pgJars | ForEach-Object { $_.Name }) -join ',' })

if (-not $SkipPan) {
    $ktr = Join-Path $ProjectRoot 'etl\transformations\smoke_test.ktr'
    if (Test-Path -LiteralPath $ktr) {
        $pdiCmd = Join-Path $ProjectRoot 'scripts\pdi.cmd'
        $panOut = (& $pdiCmd pan ("-file:" + $ktr) -level:Basic 2>&1 | Out-String)
        $panOk = ($panOut -match 'Pan - 完成|Pan - Finished')
        $verLine = (($panOut -split [Environment]::NewLine) | Where-Object { $_ -match 'Kettle 版本|Kettle version' } | Select-Object -First 1)
        Add-Check 'PDI' 'Pan 实跑转换' $panOk $(if ($verLine) { $verLine.Trim() } else { '未捕获版本行' })
    } else {
        Add-Check 'PDI' 'Pan 实跑转换' $false '缺少 etl\transformations\smoke_test.ktr'
    }
}

# ---------- 3. PostgreSQL ----------
function Test-Port([int]$P) {
    try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $P); $c.Close(); return $true } catch { return $false }
}
$pgBin = Join-Path $ProjectRoot 'tools\pgsql\bin'
$port = 5432
$listen = Test-Port $port
Add-Check 'PostgreSQL' ('端口 ' + $port + ' 监听') $listen $(if ($listen) { 'listening' } else { '未运行，执行 scripts\pg.cmd serve' })

if ($listen -and (Test-Path -LiteralPath (Join-Path $pgBin 'psql.exe'))) {
    $v = (& (Join-Path $pgBin 'psql.exe') -h 127.0.0.1 -p $port -U etl -d postgres -tAc "select version();" 2>&1 | Out-String).Trim()
    Add-Check 'PostgreSQL' '可连接且已就绪' ($v -match 'PostgreSQL') $(if ($v -match 'PostgreSQL') { ($v -split ',')[0] } else { $v })

    $db = (& (Join-Path $pgBin 'psql.exe') -h 127.0.0.1 -p $port -U etl -d postgres -tAc "select 1 from pg_database where datname='etl_amazon';" 2>&1 | Out-String).Trim()
    Add-Check 'PostgreSQL' '目标库 etl_amazon 存在' ($db -eq '1') $(if ($db -eq '1') { 'exists' } else { '缺失，执行 createdb' })
}

# ---------- 4. 源数据 ----------
$src = Join-Path $ProjectRoot 'movies.txt\movies.txt'
if (Test-Path -LiteralPath $src) {
    $gb = [math]::Round((Get-Item -LiteralPath $src).Length / 1GB, 2)
    Add-Check 'Data' '源文件 movies.txt' $true ($gb.ToString() + ' GB（只读，不入库）')
} else {
    Add-Check 'Data' '源文件 movies.txt' $false '缺失'
}

# ---------- 5. Git / 磁盘 ----------
$git = 'E:\Git\cmd\git.exe'
if (Test-Path -LiteralPath $git) {
    Push-Location $ProjectRoot
    $br = (& $git branch --show-current 2>&1 | Out-String).Trim()
    $dirty = (& $git status --porcelain 2>&1 | Out-String).Trim()
    $cnt = (@(& $git ls-files 2>&1)).Count
    Pop-Location
    Add-Check 'Git' '分支与工作区' ([string]::IsNullOrWhiteSpace($dirty)) ($br + ' / ' + $(if ([string]::IsNullOrWhiteSpace($dirty)) { 'clean' } else { 'dirty' }) + ' / ' + $cnt + ' 文件')
}

$free = [math]::Round(([System.IO.DriveInfo]::new('E')).AvailableFreeSpace / 1GB, 2)
Add-Check 'Disk' 'E: 可用空间' ($free -gt 50) ($free.ToString() + ' GB')

# ---------- 输出 ----------
Write-Host ''
Write-Host '================ 环境自检 ================' -ForegroundColor Cyan
$results | Format-Table -AutoSize -Property @(
    @{ n = '区域'; e = { $_.Area } },
    @{ n = '检查项'; e = { $_.Item } },
    @{ n = '结果'; e = { if ($_.Ok) { 'OK' } else { 'FAIL' } } },
    @{ n = '详情'; e = { $_.Detail } }
) | Out-String -Width 200 | Write-Host

$fail = @($results | Where-Object { -not $_.Ok })
Write-Host ('汇总: ' + ($results.Count - $fail.Count) + '/' + $results.Count + ' 通过') -ForegroundColor $(if ($fail.Count -eq 0) { 'Green' } else { 'Yellow' })
if ($fail.Count -gt 0) {
    Write-Host '未通过项:' -ForegroundColor Red
    $fail | ForEach-Object { Write-Host ('  - [' + $_.Area + '] ' + $_.Item + ' : ' + $_.Detail) -ForegroundColor Red }
    exit 1
}
exit 0
