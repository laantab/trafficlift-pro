<#
.SYNOPSIS
    Defensive audit: ensure the frontend rendering uses the correct
    backend field names (product name and image_url), so generated
    cards never show "untitled" or blank images.

.DESCRIPTION
    Scans the project for two classes of bugs:
      1. Stale field references (e.g. item.title, product.title) instead
         of the canonical name field returned by the API.
      2. Wrong image src binding (e.g. src="${product.image}") instead
         of the canonical image_url field.

    Currently the project is single-file (index.html with inline JS),
    but the script also iterates any standalone .js files just in case.

    Exits 0 if no issues found, 1 if any matches needed patching
    (or could not be verified), so it can be wired into CI later.

.EXAMPLE
    .\scripts\audit-frontend-rendering.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = (Get-Location).Path
$problems = 0

function Check-File {
    param([string]$Path)

    $content = Get-Content -LiteralPath $Path -Raw
    $rel     = $Path.Substring($root.Length).TrimStart('\', '/')

    # 1. Stale field references that would render as "untitled"
    $stalePatterns = @(
        @{ Pattern = 'item\.title';     Fix = 'item.name' },
        @{ Pattern = 'product\.title';  Fix = 'product.name' },
    )
    foreach ($p in $stalePatterns) {
        $matches = [regex]::Matches($content, $p.Pattern)
        if ($matches.Count -gt 0) {
            Write-Host ("  [FIX] {0}: {1} occurrence(s) of {2} -> {3}" -f $rel, $matches.Count, $p.Pattern, $p.Fix) -ForegroundColor Yellow
            $content = $content -replace $p.Pattern, $p.Fix
            $script:problems += $matches.Count
        }
    }

    # 2. Wrong image src binding
    $badImagePatterns = @(
        @{ Pattern = 'src="\$\{product\.image\}"';  Fix = 'src="${product.image_url}"'  },
        @{ Pattern = 'src="\$\{item\.image\}"';     Fix = 'src="${item.image_url}"'     },
    )
    foreach ($p in $badImagePatterns) {
        $matches = [regex]::Matches($content, $p.Pattern)
        if ($matches.Count -gt 0) {
            Write-Host ("  [FIX] {0}: {1} occurrence(s) of {2} -> {3}" -f $rel, $matches.Count, $p.Pattern, $p.Fix) -ForegroundColor Yellow
            $content = $content -replace $p.Pattern, $p.Fix
            $script:problems += $matches.Count
        }
    }

    if ($content -ne (Get-Content -LiteralPath $Path -Raw)) {
        [System.IO.File]::WriteAllText($Path, $content, [System.Text.UTF8Encoding]::new($false))
    }
}

# Iterate index.html (always) and any standalone .js files (defensive)
Write-Host "Auditing frontend rendering in $root ..." -ForegroundColor Cyan

$indexFile = Get-ChildItem -Path $root -Recurse -Filter 'index.html' -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($indexFile) {
    Check-File -Path $indexFile.FullName
} else {
    Write-Host "  [WARN] no index.html found" -ForegroundColor Yellow
    $script:problems += 1
}

$jsFiles = Get-ChildItem -Path $root -Recurse -Include *.js -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '(\.git|node_modules|__pycache__|\\scripts\\)' }
foreach ($f in $jsFiles) {
    Check-File -Path $f.FullName
}

Write-Host ""
if ($script:problems -eq 0) {
    Write-Host "OK — frontend field references are clean." -ForegroundColor Green
    exit 0
} else {
    Write-Host ("Fixed {0} issue(s). Review the diff and commit." -f $script:problems) -ForegroundColor Yellow
    exit 1
}
