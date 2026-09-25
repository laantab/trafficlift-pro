<#
.SYNOPSIS
    Set the MINIMAX_API_KEY environment variable on the trafficlift-pro
    Render service and trigger a redeploy.

.DESCRIPTION
    Uses the Render REST API to PUT MINIMAX_API_KEY on the service whose
    name matches -ServiceName (default: "trafficlift-pro"). Then triggers
    a manual deploy so the new env var takes effect immediately.

    Authentication is via a Render API key. Get one from
    https://dashboard.render.com/account/api-keys

.PARAMETER MiniMaxApiKey
    The MiniMax API key to set. If omitted, the script reads
    $env:MINIMAX_API_KEY or prompts (SecureString).

.PARAMETER RenderApiKey
    The Render API key (rnd_...). If omitted, reads
    $env:RENDER_API_KEY or prompts.

.PARAMETER ServiceName
    Name of the Render service to update (default: trafficlift-pro).

.PARAMETER GroupId
    Optional. Sets MINIMAX_GROUP_ID in addition to MINIMAX_API_KEY.

.PARAMETER SkipDeploy
    If set, skip the manual deploy trigger (rely on Render autoDeploy).

.EXAMPLE
    .\scripts\set-minimax-key.ps1 -MiniMaxApiKey "eyJhbGc..." -RenderApiKey "rnd_..."

.EXAMPLE
    $env:RENDER_API_KEY = "rnd_..."
    .\scripts\set-minimax-key.ps1 -MiniMaxApiKey "eyJhbGc..."

.NOTES
    Requires PowerShell 5+ (uses Invoke-RestMethod + Basic auth).
#>

[CmdletBinding()]
param(
    [string]$MiniMaxApiKey,
    [string]$RenderApiKey,
    [string]$ServiceName = 'trafficlift-pro',
    [string]$GroupId,
    [switch]$SkipDeploy
)

$ErrorActionPreference = 'Stop'

# Check mark: [char]0x2713 = tick
$OK   = [char]0x2713
$WARN = [char]0x26A0
$ARROW = [char]0x2192

# Resolve Render API key
if (-not $RenderApiKey) {
    $RenderApiKey = $env:RENDER_API_KEY
}
if (-not $RenderApiKey) {
    Write-Host 'Render API key not set. Get one from https://dashboard.render.com/account/api-keys' -ForegroundColor Yellow
    $secure = Read-Host 'Paste your Render API key' -AsSecureString
    $bstr   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $RenderApiKey = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    if (-not $RenderApiKey) {
        throw 'No Render API key provided.'
    }
}

# Resolve MiniMax API key
if (-not $MiniMaxApiKey) {
    $MiniMaxApiKey = $env:MINIMAX_API_KEY
}
if (-not $MiniMaxApiKey) {
    Write-Host 'MiniMax API key not set. Get one from https://www.minimaxi.com/document/Guides' -ForegroundColor Yellow
    $secure = Read-Host 'Paste your MiniMax API key' -AsSecureString
    $bstr   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $MiniMaxApiKey = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    if (-not $MiniMaxApiKey) {
        throw 'No MiniMax API key provided.'
    }
}

$cred = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes(":$RenderApiKey"))
$headers = @{
    'Authorization' = "Basic $cred"
    'Accept'        = 'application/json'
    'Content-Type'  = 'application/json'
}

# Locate the service by name
Write-Host "Looking up Render service '$ServiceName'..." -ForegroundColor Cyan
$list = Invoke-RestMethod -Uri 'https://api.render.com/v1/services?limit=100' -Headers $headers -Method GET
$candidates = @($list | Where-Object { $_.service.name -eq $ServiceName })

if ($candidates.Count -eq 0) {
    Write-Host "No service named '$ServiceName' found. Available services:" -ForegroundColor Yellow
    foreach ($entry in $list) {
        $s = $entry.service
        Write-Host ("  - {0}  ({1})  type={2}" -f $s.name, $s.id, $s.type)
    }
    throw "Service '$ServiceName' not found."
}
if ($candidates.Count -gt 1) {
    Write-Host "Multiple services match '$ServiceName':" -ForegroundColor Yellow
    foreach ($entry in $candidates) {
        $s = $entry.service
        Write-Host ("  - {0}  ({1})" -f $s.name, $s.id)
    }
    throw 'Multiple matches -- pass -ServiceName to disambiguate.'
}

$svc      = $candidates[0].service
$serviceId = $svc.id
Write-Host ("Found: {0}  ({1})  type={2}  region={3}" -f $svc.name, $serviceId, $svc.type, $svc.region) -ForegroundColor Green

# Set MINIMAX_API_KEY
$putUrl  = "https://api.render.com/v1/services/$serviceId/env-vars/MINIMAX_API_KEY"
$putBody = @{ value = $MiniMaxApiKey } | ConvertTo-Json -Compress

Write-Host ''
Write-Host "Setting MINIMAX_API_KEY on $serviceId..." -ForegroundColor Cyan

$setResult = $null
try {
    Invoke-RestMethod -Uri $putUrl -Headers $headers -Method PUT -Body $putBody | Out-Null
    $setResult = 'ok'
} catch {
    $msg = $_.Exception.Message
    if ($_.Exception.Response) {
        try {
            $reader = [System.IO.StreamReader]::new($_.Exception.Response.GetResponseStream())
            $body   = $reader.ReadToEnd()
            $reader.Close()
            $msg    = "$msg -- $body"
        } catch {
            # ignore secondary error
        }
    }
    throw "Failed to set MINIMAX_API_KEY: $msg"
}

Write-Host ("  {0} MINIMAX_API_KEY set" -f $OK) -ForegroundColor Green

# Optional: MINIMAX_GROUP_ID
if ($GroupId) {
    $putUrl2  = "https://api.render.com/v1/services/$serviceId/env-vars/MINIMAX_GROUP_ID"
    $putBody2 = @{ value = $GroupId } | ConvertTo-Json -Compress
    Write-Host "Setting MINIMAX_GROUP_ID on $serviceId..." -ForegroundColor Cyan
    try {
        Invoke-RestMethod -Uri $putUrl2 -Headers $headers -Method PUT -Body $putBody2 | Out-Null
        Write-Host ("  {0} MINIMAX_GROUP_ID set" -f $OK) -ForegroundColor Green
    } catch {
        Write-Host ("  {0} Failed to set MINIMAX_GROUP_ID: {1}" -f $WARN, $_.Exception.Message) -ForegroundColor Yellow
    }
}

# Trigger a manual deploy
if (-not $SkipDeploy) {
    Write-Host ''
    Write-Host 'Triggering manual deploy...' -ForegroundColor Cyan
    $deployUrl  = "https://api.render.com/v1/services/$serviceId/deploys"
    $deployBody = @{ clearCache = 'do_not_clear' } | ConvertTo-Json -Compress
    try {
        $deploy   = Invoke-RestMethod -Uri $deployUrl -Headers $headers -Method POST -Body $deployBody
        $deployId = $deploy.id
        Write-Host ("  {0} Deploy triggered: {1}" -f $OK, $deployId) -ForegroundColor Green
        Write-Host ''
        Write-Host 'Track it at:' -ForegroundColor Cyan
        Write-Host ("  https://dashboard.render.com/web/{0}/deploys/{1}" -f $serviceId, $deployId)
    } catch {
        Write-Host ("  {0} Could not auto-trigger deploy. Render will pick up the env var on the next git push or auto-deploy." -f $WARN) -ForegroundColor Yellow
        Write-Host ("    Reason: {0}" -f $_.Exception.Message)
    }
}

Write-Host ''
Write-Host "Done. Once the deploy finishes (~2-3 min), /api/v1/video/* endpoints will be live." -ForegroundColor Green
