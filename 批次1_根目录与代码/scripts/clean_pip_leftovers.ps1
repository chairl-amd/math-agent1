<#
.SYNOPSIS
    清理 D:\math-agent 根目录下残留的空 pip-* 临时目录（工单5）。

.DESCRIPTION
    这些目录是早期 pip 在本目录以 TMP=. 运行时留下的。它们的 DACL 不允许
    普通用户读取或删除（Get-Acl 亦报“未经授权的操作”），因此必须在
    **管理员权限**下执行本脚本。

    脚本安全约束：
      1. 只处理 Root 直接子目录，且目录名必须匹配 pip-<类型>-<随机串>；
      2. 默认只删除“完全为空”的目录，非空目录跳过并列入报告；
      3. 逐条打印将删除的绝对路径，支持 -WhatIf 预演；
      4. 不触碰 Root 之外的任何路径。

.PARAMETER Root
    项目根目录，默认 D:\math-agent。

.PARAMETER Force
    非空目录也删除（会先打印其全部内容）。

.EXAMPLE
    管理员 PowerShell 中预演：
        pwsh -File D:\math-agent\scripts\clean_pip_leftovers.ps1 -WhatIf

.EXAMPLE
    管理员 PowerShell 中实际清理：
        pwsh -File D:\math-agent\scripts\clean_pip_leftovers.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Root = 'D:\math-agent',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    throw "根目录不存在：$Root"
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
$pattern = '^pip-(build-tracker|ephem-wheel-cache|install|metadata|unpack|unpack-|target)-[A-Za-z0-9_]+$'

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warning '当前不是管理员权限：若删除被拒绝，说明目录 DACL 仍不允许当前用户，请以管理员身份重跑本脚本。'
}

$candidates = @(Get-ChildItem -LiteralPath $resolvedRoot -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match $pattern })

Write-Host "根目录：$resolvedRoot"
Write-Host "匹配 pip-* 目录：$($candidates.Count) 个"

$deleted = 0
$skipped = @()
$failed = @()

foreach ($dir in $candidates) {
    $path = $dir.FullName
    # 双重校验：必须是根目录的直接子项。
    if ([System.IO.Path]::GetDirectoryName($path).TrimEnd('\') -ne $resolvedRoot) {
        $skipped += "$path（不是根目录直接子项）"
        continue
    }

    $children = @(Get-ChildItem -LiteralPath $path -Force -Recurse -ErrorAction SilentlyContinue)
    # 新增：pip 留下的 metadata 目录通常只含 METADATA 或 *.whl.metadata，
    # 这类纯临时文件视为可清理，不需要 -Force。
    $onlyPipScratch = $children.Count -gt 0 -and @($children | Where-Object {
            $_.PSIsContainer -or
            ($_.Name -ne 'METADATA' -and $_.Name -notlike '*.metadata')
        }).Count -eq 0
    if ($onlyPipScratch) {
        Write-Host "pip 元数据残留（视同空目录）：$path（$($children.Count) 个 metadata 文件）"
    }
    if ($children.Count -gt 0 -and -not $Force -and -not $onlyPipScratch) {
        $skipped += "$path（非空：$($children.Count) 项，加 -Force 可删除）"
        continue
    }
    if ($children.Count -gt 0) {
        Write-Host "非空目录内容（-Force）：$path"
        $children | Select-Object -First 20 | ForEach-Object { Write-Host "    $($_.FullName)" }
    }

    if ($PSCmdlet.ShouldProcess($path, '删除空 pip 临时目录')) {
        try {
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
            $deleted++
        } catch {
            $failed += "$path（$($_.Exception.Message)）"
        }
    }
}

Write-Host ''
Write-Host "已删除：$deleted 个"
if ($skipped.Count -gt 0) {
    Write-Host "已跳过：$($skipped.Count) 个"
    $skipped | ForEach-Object { Write-Host "  - $_" }
}
if ($failed.Count -gt 0) {
    Write-Host "删除失败：$($failed.Count) 个"
    $failed | ForEach-Object { Write-Host "  - $_" }
    exit 1
}

# 顺带提示：可用环境变量把 pip 临时目录固定到用户 Temp，避免再次污染项目根目录。
Write-Host ''
Write-Host '提示：建议在运行 pip 前设置 $env:TMP / $env:TEMP 指向用户 Temp 目录，'
Write-Host '      例如 $env:TMP = "$env:LOCALAPPDATA\Temp"，即可避免再次生成 pip-* 目录。'
exit 0
