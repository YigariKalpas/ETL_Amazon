<#
.SYNOPSIS
    下载并配置 Pentaho Data Integration (PDI/Kettle) CE + 项目内便携 JDK。

.DESCRIPTION
    三个已核实的事实决定了本方案：
      1) PDI 9.x 认证 Java 11（官方 9.3 文档：认证 Oracle Java 11.0.13，支持 Java 8/11）。
         本机 PATH 上是 Java 25，PDI 不支持。故在项目内放置便携 JDK 11，用
         PENTAHO_JAVA_HOME 指定（PDI 启动脚本优先读它，其次才读 JAVA_HOME），
         从而不污染系统 Java 25。
      2) Pentaho 已把较新的 CE 从 SourceForge 下架。官方 CE 目前只提供
         9.4 / 9.3 / 9.2 / 8.3。自 10.x 起改称 Developer Edition（BSL 1.1，需授权申请），
         不再是可自由下载的 CE。故本项目固定为可用的最新 CE：9.4.0.0-343。
      3) 全部解压到无空格的项目内路径，且不写系统环境变量——保证可复现、可整体删除。

    压缩包布局差异（本脚本已处理）：
      - Temurin JDK zip 顶层是 jdk-11.x.y+N/，需剥掉该层 -> tools\jdk-11\
      - PDI CE zip 顶层本身就是 data-integration/，**不能剥离**
        -> tools\pdi-ce-<ver>\data-integration\

.PARAMETER PdiVersion
    PDI CE 版本号，默认 9.4.0.0-343。

.PARAMETER JdkMajor
    Temurin JDK 主版本，默认 11（PDI 9.x 认证版本）。

.PARAMETER Force
    即使目标已存在也重新下载与解压。

.PARAMETER ProxyUrl
    下载 PDI 时使用的 HTTP 代理，例如 http://127.0.0.1:7897。
    留空则自动从系统 Internet 设置读取。
    （本机实测：GitHub 直连超时，必须经系统代理；国内镜像则可直连）
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [string]$PdiVersion = '9.4.0.0-343',
    [int]$JdkMajor = 11,
    [string]$ProxyUrl = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = 'SilentlyContinue'

if (-not $ProjectRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
}

# 自动探测系统代理。Python/.NET 默认会读注册表代理，但下载器需要显式开关；
# 且不同来源的最优通道不同（GitHub 需代理，国内镜像直连更快）。
if (-not $ProxyUrl) {
    try {
        $ie = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction Stop
        if ($ie.ProxyEnable -eq 1 -and $ie.ProxyServer) { $ProxyUrl = 'http://' + $ie.ProxyServer }
    } catch { }
}
if ($ProxyUrl) { Write-Host ("系统代理: " + $ProxyUrl) } else { Write-Host "未检测到系统代理，全部直连" }

$tools      = Join-Path $ProjectRoot 'tools'
$downloads  = Join-Path $tools 'downloads'
$jdkDir     = Join-Path $tools ("jdk-" + $JdkMajor)
$pdiRoot    = Join-Path $tools ("pdi-ce-" + $PdiVersion)
$kettleHome = Join-Path $ProjectRoot 'etl\kettle'

function Ensure-Dir([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

function Resolve-Python {
    # 本机裸 python 是 2.7.9（Unity 残留），必须显式校验版本
    $cands = @()
    $vpy = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $vpy) { $cands += $vpy }
    $cands += 'python3'
    foreach ($c in $cands) {
        try {
            $ver = & $c -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and [version]$ver -ge [version]'3.8') { return $c }
        } catch { }
    }
    return $null
}

function Get-RemoteFile([string]$Url, [string]$Dest, [string]$Label, [string[]]$Channels = @('')) {
    if ((Test-Path -LiteralPath $Dest) -and -not $Force) {
        Write-Host ("[skip] " + $Label + " 已存在: " + $Dest)
    } else {
        Write-Host ("[get ] " + $Label)
        Write-Host ("       " + $Url)
        $dl = Join-Path $PSScriptRoot 'fetch_url.py'
        $py = Resolve-Python
        if (-not $py) { throw "未找到可用的 Python 3（下载器依赖它）" }
        if (-not (Test-Path -LiteralPath $dl)) { throw ("缺少下载器: " + $dl) }

        $ok = $false
        foreach ($ch in $Channels) {
            $tag = if ($ch) { $ch } else { '直连' }
            Write-Host ("       channel: " + $tag)
            $a = @($dl, $Url, $Dest, '--sha256', '--retries', '2', '--timeout', '45')
            if ($ch) { $a += @('--proxy', $ch) }
            & $py @a
            if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $Dest) -and (Get-Item -LiteralPath $Dest).Length -gt 0) {
                $ok = $true; break
            }
            Write-Host "       通道失败，换下一个（保留 .part 以便断点续传）"
        }
        if (-not $ok) { throw ("下载失败: " + $Url) }
    }
    $len = (Get-Item -LiteralPath $Dest).Length
    $sha = (Get-FileHash -LiteralPath $Dest -Algorithm SHA256).Hash
    Write-Host ("       " + [math]::Round($len / 1MB, 1) + " MB  SHA256=" + $sha)
}

function Expand-Zip([string]$Zip, [string]$Dest) {
    # 优先用 bsdtar（Windows 内置，处理大量小文件远快于 Expand-Archive）
    if (Test-Path -LiteralPath $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force }
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    $tar = Join-Path $env:SystemRoot 'System32\tar.exe'
    if (Test-Path -LiteralPath $tar) {
        & $tar -xf $Zip -C $Dest
        if ($LASTEXITCODE -ne 0) { throw ("tar 解压失败(exit=" + $LASTEXITCODE + "): " + $Zip) }
    } else {
        Expand-Archive -LiteralPath $Zip -DestinationPath $Dest -Force
    }
}

Ensure-Dir $tools
Ensure-Dir $downloads

# ---- 1. 便携 JDK（剥掉顶层 jdk-* 目录）----
Write-Host "[1/4] JDK"
$javaExe = Join-Path $jdkDir 'bin\java.exe'
if ((Test-Path -LiteralPath $javaExe) -and -not $Force) {
    Write-Host ("[skip] JDK 已就绪: " + $jdkDir)
} else {
    $jdkZip = Join-Path $downloads ("temurin-jdk-" + $JdkMajor + ".zip")
    $api = "https://api.adoptium.net/v3/binary/latest/$JdkMajor/ga/windows/x64/jdk/hotspot/normal/eclipse"
    $jdkChannels = if ($ProxyUrl) { @('', $ProxyUrl) } else { @('') }
    Get-RemoteFile $api $jdkZip ("Temurin JDK " + $JdkMajor) $jdkChannels
    $staging = Join-Path $downloads '_jdk_staging'
    Write-Host ("[unzip] -> " + $jdkDir)
    Expand-Zip $jdkZip $staging
    $tops = @(Get-ChildItem -LiteralPath $staging -Force)
    if (Test-Path -LiteralPath $jdkDir) { Remove-Item -LiteralPath $jdkDir -Recurse -Force }
    if ($tops.Count -eq 1 -and $tops[0].PSIsContainer) {
        Move-Item -LiteralPath $tops[0].FullName -Destination $jdkDir
        Remove-Item -LiteralPath $staging -Recurse -Force
    } else {
        Move-Item -LiteralPath $staging -Destination $jdkDir
    }
}

# ---- 2. PDI CE（顶层就是 data-integration，不能剥离）----
Write-Host "[2/4] PDI CE"
$diDir = Join-Path $pdiRoot 'data-integration'
if ((Test-Path -LiteralPath (Join-Path $diDir 'Spoon.bat')) -and -not $Force) {
    Write-Host ("[skip] PDI 已就绪: " + $diDir)
} else {
    $pdiZip = Join-Path $downloads ("pdi-ce-" + $PdiVersion + ".zip")
    $url = "https://github.com/ambientelivre/legacy-pentaho-ce/releases/download/pdi-ce-$PdiVersion/pdi-ce-$PdiVersion.zip"
    # GitHub 直连在本机超时，故代理优先
    $pdiChannels = if ($ProxyUrl) { @($ProxyUrl, '') } else { @('') }
    Get-RemoteFile $url $pdiZip ("PDI CE " + $PdiVersion) $pdiChannels
    Write-Host ("[unzip] -> " + $pdiRoot)
    Expand-Zip $pdiZip $pdiRoot

    # 归一化：保证最终存在 <pdiRoot>\data-integration\Spoon.bat
    if (-not (Test-Path -LiteralPath (Join-Path $diDir 'Spoon.bat'))) {
        $spoon = Get-ChildItem -LiteralPath $pdiRoot -Recurse -Filter 'Spoon.bat' -Force -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $spoon) { throw ("PDI 解压后未找到 Spoon.bat: " + $pdiRoot) }
        $found = $spoon.Directory.FullName
        Write-Host ("[norm ] 实际路径 " + $found + " -> " + $diDir)
        if (Test-Path -LiteralPath $diDir) { Remove-Item -LiteralPath $diDir -Recurse -Force }
        Move-Item -LiteralPath $found -Destination $diDir
        # 清理残留的空包装目录
        Get-ChildItem -LiteralPath $pdiRoot -Force -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne 'data-integration' } | ForEach-Object {
                $any = Get-ChildItem -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue | Select-Object -First 1
                if (-not $any) { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
            }
    }
}

# ---- 3. KETTLE_HOME ----
Write-Host "[3/4] KETTLE_HOME"
Ensure-Dir (Join-Path $kettleHome '.kettle')
Ensure-Dir (Join-Path $kettleHome '.pentaho\metastore')
Write-Host ("       " + $kettleHome + "\.kettle 与 .pentaho\metastore 已就绪")
Write-Host "       KETTLE_HOME 只填到 .kettle 的父目录；metastore 由 PENTAHO_METASTORE_FOLDER 重定向"

# ---- 4. 验证 ----
Write-Host "[4/4] 验证"
if (-not (Test-Path -LiteralPath $javaExe)) { throw ("JDK 缺失: " + $javaExe) }
# PS 5.1 下原生命令写 stderr 会被 $ErrorActionPreference='Stop' 当成终止错误，故局部放宽
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$javaOut = (& $javaExe -version 2>&1 | Out-String).Trim()
$ErrorActionPreference = $prevEap
Write-Host ("       java: " + $javaOut)
foreach ($f in 'Spoon.bat', 'Pan.bat', 'Kitchen.bat') {
    $p = Join-Path $diDir $f
    Write-Host ("       " + $f.PadRight(12) + " : " + (Test-Path -LiteralPath $p))
}
Write-Host ""
Write-Host "完成。下一步：" -ForegroundColor Green
Write-Host "  scripts\pdi.cmd status    # 查看注入后的环境" -ForegroundColor Green
Write-Host "  scripts\pdi.cmd spoon     # 启动 Spoon 图形化设计器" -ForegroundColor Green
