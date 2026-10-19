<#
.SYNOPSIS
    提交前守卫：阻止受保护路径与大文件进入 Git 索引。

.DESCRIPTION
    为什么不用 pre-commit 钩子：
      本机 Git 为残缺安装 —— E:\Git\usr\bin\sh.exe / bash.exe 缺失（bin\bash.exe 是坏 shim），
      且 mingw64/libexec/git-core 下没有任何 git-remote-*。
      因此 shell 钩子无法 spawn（反而会让所有提交失败），HTTPS 远端也不可用。
      在 Git 修复前，改用本脚本作为守卫；Git 修好后可再启用 scripts/git-hooks/pre-commit。

用法:
    scripts\git_guard.cmd [ -MaxMB 50 ]
退出码: 0=通过 1=拦截
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [int]$MaxMB = 50
)

$ErrorActionPreference = 'Continue'
if (-not $ProjectRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
}

$git = 'E:\Git\cmd\git.exe'
Push-Location $ProjectRoot
$staged = @(& $git diff --cached --name-only --diff-filter=ACM 2>&1)
Pop-Location

if ($staged.Count -eq 0) {
    Write-Host '[git-guard] 暂存区为空，无需检查'
    exit 0
}

$pathRules = @(
    '^data/', '^tools/', '^\.venv/', '^logs/', '^movies\.txt',
    '\.zip$', '\.gz$', '\.7z$', '\.tar$', '\.jar$',
    '\.duckdb$', '\.duckdb\.wal$', '\.sqlite3?$', '\.db$', '\.parquet$', '\.pkl$', '\.pickle$'
)

$blocked = New-Object System.Collections.Generic.List[object]
foreach ($f in $staged) {
    if (-not $f) { continue }

    $hitRule = $null
    foreach ($re in $pathRules) {
        if ($f -match $re) { $hitRule = $re; break }
    }
    if ($hitRule) {
        $blocked.Add([pscustomobject]@{ Path = $f; Reason = '受保护路径' }) | Out-Null
        continue
    }

    $full = Join-Path $ProjectRoot $f
    if (Test-Path -LiteralPath $full) {
        $mb = (Get-Item -LiteralPath $full).Length / 1MB
        if ($mb -gt $MaxMB) {
            $blocked.Add([pscustomobject]@{ Path = $f; Reason = ('体积 {0:N1} MB > {1} MB' -f $mb, $MaxMB) }) | Out-Null
        }
    }
}

if ($blocked.Count -eq 0) {
    Write-Host ('[git-guard] 通过：' + $staged.Count + ' 个暂存文件均合规')
    exit 0
}

Write-Host '[git-guard] 拦截以下文件：' -ForegroundColor Red
$blocked | Format-Table -AutoSize -Property Path, Reason | Out-String -Width 200 | Write-Host
Write-Host '规则见 environment/Git经验文档.md' -ForegroundColor Yellow
exit 1
