<#
.SYNOPSIS
    Install the Revayat skill into one or more coding agents.

.DESCRIPTION
    Copies skills/revayat/ into each agent's real skill directory. Copies, never
    links: several agents do not follow junctions or symlinks when discovering
    skills, so a real directory is the only reliable form.

.PARAMETER Agent
    claude, kiro, codex, cursor, cline, hermes, opencode, antigravity,
    or all (default). OpenCode and Antigravity also get an AGENTS.md pointer,
    because that is how they discover instructions.

.PARAMETER Scope
    user (default) installs for every project; project installs into -Path only.

.PARAMETER Path
    Project root for -Scope project. Defaults to the current directory.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Agent claude -Scope project -Path C:\work\my-book
#>
[CmdletBinding()]
param(
    [ValidateSet('claude', 'kiro', 'codex', 'cursor', 'cline', 'hermes',
                 'opencode', 'antigravity', 'all')]
    [string] $Agent = 'all',

    [ValidateSet('user', 'project')]
    [string] $Scope = 'user',

    [string] $Path = (Get-Location).Path,

    [switch] $Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Resolve relative to this script, so the installer works from any directory.
$RepoRoot  = Split-Path -Parent $PSScriptRoot
$SkillName = 'revayat'
$Source    = Join-Path $RepoRoot "skills\$SkillName"

if (-not (Test-Path -LiteralPath (Join-Path $Source 'SKILL.md'))) {
    throw "Cannot find the skill at '$Source'. Run this script from inside a clone of the repository."
}

function Get-AgentRoot {
    param([string] $Name, [string] $InstallScope, [string] $ProjectPath)

    $folder = switch ($Name) {
        'claude'      { '.claude' }
        'kiro'        { '.kiro' }
        'codex'       { '.codex' }
        'cursor'      { '.cursor' }
        'cline'       { '.cline' }
        'hermes'      { '.hermes' }
        'opencode'    { '.opencode' }
        'antigravity' { '.agents' }
    }
    $base = if ($InstallScope -eq 'user') { $HOME } else { $ProjectPath }
    return (Join-Path (Join-Path $base $folder) 'skills')
}

$targets = if ($Agent -eq 'all') {
    @('claude', 'kiro', 'codex', 'cursor', 'cline', 'hermes', 'opencode',
      'antigravity')
} else {
    @($Agent)
}

if ($Scope -eq 'project') {
    if (-not (Test-Path -LiteralPath $Path)) { throw "Project path not found: $Path" }
    $Path = (Resolve-Path -LiteralPath $Path).Path
}

Write-Host ''
Write-Host "Revayat installer" -ForegroundColor Cyan
Write-Host "  source : $Source"
Write-Host "  scope  : $Scope$(if ($Scope -eq 'project') { " ($Path)" })"
Write-Host ''

# OpenCode and Antigravity read instructions from AGENTS.md rather than by
# scanning a skills directory. The markers make the section replaceable without
# disturbing anything else the user keeps in that file.
$BeginMark = '<!-- BEGIN revayat -->'
$EndMark   = '<!-- END revayat -->'

function Write-AgentsPointer {
    param([string] $File, [string] $SkillDir)

    $kept = @()
    if (Test-Path -LiteralPath $File) {
        $skip = $false
        foreach ($line in Get-Content -LiteralPath $File -Encoding utf8) {
            if ($line -eq $BeginMark) { $skip = $true; continue }
            if ($line -eq $EndMark)   { $skip = $false; continue }
            if (-not $skip) { $kept += $line }
        }
    }

    $section = @(
        '',
        $BeginMark,
        '## Revayat — Persian book translation',
        '',
        'To translate a book into Persian and produce a Word file, follow',
        ('`' + $SkillDir + '/SKILL.md`. Wherever it says `{SKILL_DIR}`, read that as'),
        ('`' + $SkillDir + '`.'),
        $EndMark
    )
    New-Item -ItemType Directory -Path (Split-Path -Parent $File) -Force | Out-Null
    Set-Content -LiteralPath $File -Value ($kept + $section) -Encoding utf8
}

$installed = @()
$skipped   = @()

foreach ($name in $targets) {
    $root        = Get-AgentRoot -Name $name -InstallScope $Scope -ProjectPath $Path
    $destination = Join-Path $root $SkillName
    $parent      = Split-Path -Parent $root

    # For 'all' at user scope, only install where the agent is actually present,
    # so we do not create config directories for tools that are not installed.
    if ($Agent -eq 'all' -and -not (Test-Path -LiteralPath $parent)) {
        $skipped += "$name (not installed)"
        continue
    }

    if ((Test-Path -LiteralPath $destination) -and -not $Force) {
        $answer = Read-Host "  $name already has '$SkillName'. Replace it? [Y/n]"
        if ($answer -and $answer -notmatch '^(y|yes)$') {
            $skipped += "$name (kept existing)"
            continue
        }
    }

    if (Test-Path -LiteralPath $destination) {
        Remove-Item -LiteralPath $destination -Recurse -Force
    }
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $destination -Recurse -Force

    # Never ship caches or a local virtualenv into an agent's skill directory.
    Get-ChildItem -LiteralPath $destination -Recurse -Force -Directory `
        -Include '__pycache__', '.pytest_cache', '.venv', 'venv' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    if ($name -in @('opencode', 'antigravity')) {
        $pointerBase = if ($Scope -eq 'user') { $HOME } else { $Path }
        Write-AgentsPointer -File (Join-Path $pointerBase 'AGENTS.md') -SkillDir $destination
        Write-Host "  wrote AGENTS.md pointer for $name" -ForegroundColor DarkGray
    }

    Write-Host "  installed -> $destination" -ForegroundColor Green
    $installed += $name
}

Write-Host ''
if ($installed.Count -gt 0) {
    Write-Host "Installed for: $($installed -join ', ')" -ForegroundColor Green
} else {
    Write-Host 'Nothing was installed.' -ForegroundColor Yellow
}
if ($skipped.Count -gt 0) {
    Write-Host "Skipped: $($skipped -join ', ')" -ForegroundColor DarkGray
}

Write-Host ''
Write-Host 'Python dependencies:' -ForegroundColor Cyan
Write-Host "  pip install -r `"$(Join-Path $Source 'requirements.txt')`""
Write-Host ''
Write-Host 'Then check the install with:' -ForegroundColor Cyan
Write-Host "  python `"$(Join-Path $Source 'scripts\revayat.py')`" doctor"
Write-Host ''
Write-Host 'Activate the skill by its name, which is "revayat" — the value of'
Write-Host 'name: in SKILL.md, not the folder name.'
Write-Host ''

if ($installed.Count -eq 0) { exit 1 }
exit 0
