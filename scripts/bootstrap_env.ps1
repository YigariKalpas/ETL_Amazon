<#
.SYNOPSIS
    项目 Python 3 环境引导脚本（P0-1）。

.DESCRIPTION
    幂等地准备项目虚拟环境 .venv，使 ETL 项目具备可复现的 Python 3 运行时。

    本机特殊背景（已实测定位）：
      1) PATH 上的裸 python 指向 Python 2.7.9（E:\Unity_Project_Cache\python.exe），
         必须使用 python3，且项目内一律以 .venv\Scripts\python.exe 的绝对路径调用。
      2) DSH 沙箱下 os.mkdir(path, 0o700) 会产生不继承的收紧 DACL，使 tempfile.mkdtemp
         建立的目录连创建者都无法写入，导致 ensurepip / pip 失败。因此建 venv 时使用
         --without-pip，先注入 environment/sitecustomize.py 兼容层，再引导 pip。

.PARAMETER Recreate
    删除并重建 .venv（用于验证引导流程或修复损坏环境）。

.EXAMPLE
    pwsh -File scripts\bootstrap_env.ps1
.EXAMPLE
    pwsh -File scripts\bootstrap_env.ps1 -Recreate
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [string]$Python      = 'python3',
    [switch]$Recreate
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = 'SilentlyContinue'

# PS 5.1 的 param() 默认值中 $PSScriptRoot 可能为空，故在脚本体内解析脚本所在目录
if (-not $ProjectRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
}

$venv = Join-Path $ProjectRoot '.venv'
$py   = Join-Path $venv 'Scripts\python.exe'
$shim = Join-Path $ProjectRoot 'environment\sitecustomize.py'
$sp   = Join-Path $venv 'Lib\site-packages'

function Assert-Ok([int]$Code, [string]$What) {
    if ($Code -ne 0) { throw "$What 失败 (exit=$Code)" }
}

# ---- 0. 解释器门禁：必须是 Python >= 3.11 ----------------------------------
$verRaw = (& $Python -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>&1 | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) {
    throw "无法执行 '$Python'。裸 python 可能是 Python 2.7（Unity 残留），请改用 python3。"
}
if ([version]$verRaw -lt [version]'3.11') {
    throw "'$Python' 解析为 Python $verRaw，低于 3.11。请检查 PATH 优先级。"
}
Write-Host "[1/5] 解释器门禁通过: $Python = Python $verRaw"

# ---- 1. 创建 venv（--without-pip，规避沙箱 tempfile 问题）------------------
if ($Recreate -and (Test-Path -LiteralPath $venv)) {
    Write-Host "[2/5] -Recreate: 删除旧 venv"
    Remove-Item -LiteralPath $venv -Recurse -Force
}
if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "[2/5] 创建 venv -> $venv"
    & $Python -m venv --without-pip $venv
    Assert-Ok $LASTEXITCODE 'venv 创建'
} else {
    Write-Host "[2/5] 复用已有 venv"
}

# ---- 2. 注入沙箱兼容层（必须先于任何 pip/ensurepip）------------------------
New-Item -ItemType Directory -Force -Path $sp | Out-Null
Copy-Item -LiteralPath $shim -Destination (Join-Path $sp 'sitecustomize.py') -Force
Write-Host "[3/5] 已注入 environment/sitecustomize.py"

# ---- 3. 引导并升级 pip 工具链 ----------------------------------------------
& $py -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[4/5] 引导 pip (ensurepip)"
    & $py -m ensurepip --upgrade --default-pip
    Assert-Ok $LASTEXITCODE 'ensurepip'
}
& $py -m pip install --upgrade pip setuptools wheel
Assert-Ok $LASTEXITCODE 'pip 工具链升级'

# ---- 4. 验证 ---------------------------------------------------------------
Write-Host "[5/5] 验证"
& $py -c "import sys; assert sys.prefix != sys.base_prefix, 'venv 未生效'; print('  python  =', sys.version.split()[0]); print('  exe     =', sys.executable); print('  prefix  =', sys.prefix)"
Assert-Ok $LASTEXITCODE 'venv 隔离性验证'
& $py -m pip list
Write-Host ""
Write-Host "环境就绪。项目内统一使用: $py" -ForegroundColor Green
