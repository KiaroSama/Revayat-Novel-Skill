<#
.SYNOPSIS
    Install Revayat Novel through its shared Python 3.10+ preservation engine.
#>
[CmdletBinding()]
param(
    [ValidateSet('claude','kiro','codex','cursor','cline','hermes','opencode','antigravity','all')]
    [string] $Agent = 'all',
    [ValidateSet('user','project')]
    [string] $Scope = 'user',
    [string] $Path = (Get-Location).Path,
    [switch] $Force,
    [switch] $Status
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$engine = Join-Path $PSScriptRoot 'installer.py'
foreach ($name in 'python3','python','py') {
    $command = Get-Command -Name $name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $command) { continue }
    $prefix = if ($name -eq 'py') { @('-3') } else { @() }
    try {
        & $command.Source @prefix -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>$null
        if ($LASTEXITCODE -ne 0) { continue }
    } catch { continue }
    try {
        $arguments = @($prefix) + @($engine, '--agent', $Agent, '--scope', $Scope, '--path', $Path)
        if ($Force) { $arguments += '--force' }
        if ($Status) { $arguments += '--status' }
        & $command.Source @arguments
        $code = $LASTEXITCODE
        exit $code
    } catch {
        Write-Error 'Python launch failed; no replacement consent was granted.' -ErrorAction Continue
        exit 1
    }
}
Write-Error 'Python 3.10+ is required before installation; no files were replaced.' -ErrorAction Continue
exit 1
