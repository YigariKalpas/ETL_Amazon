<#
.SYNOPSIS
    下载并配置 Pentaho Data Integration (PDI/Kettle) CE + 项目内便携 JDK。

.DESCRIPTION
    三个已核实的事实决定了本方案：
      1) PDI 9.x 认证 Java 11（官方 9.3 文档：认证 Oracle Java 11.0.13，支持 Java 8/11）。
         本机 PATH 上是 Java 25，PDI 不支持。故在项目内放置便携 JDK 11，用
         PENTAHO_JAVA_HOME 指定（PDI 启动脚本优先读它，其次才读 JAVA_HOME），
         从而不污染系统 Java 25。
      2) Pentaho 已把较新的 CE 从 SourceForge 下架（"Older versions of pentaho have
         been removed from sourceforge.net/projects/pentaho"）。官方 CE 目前只提供
         9.4 / 9.3 / 9.2 / 8.3。自 10.x 起改称 Developer Edition（BSL 1.1，需授权申请），
         不再是可自由下载的 CE。故本项目固定为可用的最新 CE：9.4.0.0-343。
      3) 全部解压到无空格的项目内路径，且不写系统环境变量——保证可复现、可整体删除。

.PARAMETER PdiVersion
    PDI CE 版本号，默认 9.4.0.0-343。

.PARAMETER JdkMajor
    Temurin JDK 主版本，默认 11（PDI 9.x 认证版本）。

.PARAMETER Force
    即使目标已存在也重新下载与解压。
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [string]$PdiVersion = '9.4.0.0-343',
    [int]$JdkMajor = 11,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = 'SilentlyContinue'

if (-not $ProjectRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
}

$tools     = Join-Path $ProjectRoot 'tools'
$downloads = Join-Path $tools 'downloads'
$jdkDir    = Join-Path $tools ("jdk-" + $JdkMajor)
$pdiRoot   = Join-Path $tools ("pdi-ce-" + $PdiVersion)
$diDir     = Join-Path $pdiRoot 'data-integration'
$kettleHome = Join-Path $ProjectRoot 'etl\kettle'

function Ensure-Dir([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

function Get-RemoteFile([string]$Url, [string]$Dest, [string]$Label) {
    if ((Test-Path -LiteralPath $Dest) -and -not $Force) {
        Write-Host ("[skip] " + $Label + " 已存在: " + $Dest)
    } else {
        Write-Host ("[get ] " + $Label)
        Write-Host ("       " + $Url)
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        try {
            $wc = New-Object System.Net.WebClient
            try { $wc.DownloadFile($Url, $Dest) } finally { $wc.Dispose() }
        } catch {
            Write-Host ("       WebClient 失败，回退 Invoke-WebRequest: " + $_.Exception.Message)
            Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
        }
    }
    $len = (Get-Item -LiteralPath $Dest).Length
    $sha = (Get-FileHash -LiteralPath $Dest -Algorithm SHA256).Hash
    Write-Host ("       " + [math]::Round($len / 1MB, 1) + " MB  SHA256=" + $sha)
}

function Expand-Strip([string]$Zip, [string]$Dest) {
    # 解压并剥掉压缩包内的单一顶层目录
    $tmp = $Dest + '.extract'
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    Expand-Archive -LiteralPath $Zip -DestinationPath $tmp -Force
    $tops = @(Get-ChildItem -LiteralPath $tmp -Force)
    if (Test-Path -LiteralPath $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force }
    if ($tops.Count -eq 1 -and $tops[0].PSIsContainer) {
        Move-Item -LiteralPath $tops[0].FullName -Destination $Dest
        Remove-Item -LiteralPath $tmp -Recurse -Force
    } else {
        Move-Item -LiteralPath $tmp -Destination $Dest
    }
}

Ensure-Dir $tools
Ensure-Dir $downloads

# ---- 1. 便携 JDK ----
Write-Host "[1/4] JDK"
$javaExe = Join-Path $jdkDir 'bin\java.exe'
if ((Test-Path -LiteralPath $javaExe) -and -not $Force) {
    Write-Host ("[skip] JDK 已就绪: " + $jdkDir)
} else {
    $jdkZip = Join-Path $downloads ("temurin-jdk-" + $JdkMajor + ".zip")
    $api = "https://api.adoptium.net/v3/binary/latest/$JdkMajor/ga/windows/x64/jdk/hotspot/normal/eclipse"
    Get-RemoteFile $api $jdkZip ("Temurin JDK " + $JdkMajor)
    Write-Host ("[unzip] -> " + $jdkDir)
    Expand-Strip $jdkZip $jdkDir
}

# ---- 2. PDI CE ----
Write-Host "[2/4] PDI CE"
if ((Test-Path -LiteralPath (Join-Path $diDir 'Spoon.bat')) -and -not $Force) {
    Write-Host ("[skip] PDI 已就绪: " + $diDir)
} else {
    $pdiZip = Join-Path $downloads ("pdi-ce-" + $PdiVersion + ".zip")
    $url = "https://github.com/ambientelivre/legacy-pentaho-ce/releases/download/pdi-ce-$PdiVersion/pdi-ce-$PdiVersion.zip"
    Get-RemoteFile $url $pdiZip ("PDI CE " + $PdiVersion)
    Write-Host ("[unzip] -> " + $pdiRoot)
    Expand-Strip $pdiZip $pdiRoot
}

# ---- 3. KETTLE_HOME ----
Write-Host "[3/4] KETTLE_HOME"
Ensure-Dir (Join-Path $kettleHome '.kettle')
Write-Host ("       " + $kettleHome + "\.kettle 已就绪（KETTLE_HOME 只填到 .kettle 的父目录）")

# ---- 4. 验证 ----
Write-Host "[4/4] 验证"
if (-not (Test-Path -LiteralPath $javaExe)) { throw "JDK 缺失: $javaExe" }
& $javaExe -version 2>&1 | ForEach-Object { Write-Host ("       java: " + $_) }
foreach ($f in 'Spoon.bat', 'Pan.bat', 'Kitchen.bat') {
    $p = Join-Path $diDir $f
    Write-Host ("       " + $f.PadRight(12) + " : " + (Test-Path -LiteralPath $p))
}
Write-Host ""
Write-Host "完成。下一步：" -ForegroundColor Green
Write-Host "  scripts\pdi.cmd status    # 查看注入后的环境" -ForegroundColor Green
Write-Host "  scripts\pdi.cmd spoon     # 启动 Spoon 图形化设计器" -ForegroundColor Green
