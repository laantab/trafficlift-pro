$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
$runtime = $repo
$runtimeReference = Join-Path $repo '.runtime-root'
if (Test-Path $runtimeReference) { $runtime = [IO.File]::ReadAllText($runtimeReference).Trim() }
Add-Type -AssemblyName PresentationFramework
function Open-Studio {
    $url = 'http://127.0.0.1:8005/studio?api=http://127.0.0.1:8005'
    $edge = @((Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'), (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($edge) { Start-Process $edge -ArgumentList ('--app=' + $url) }
    else { Start-Process $url }
}
try {
    $python = Join-Path $runtime '.video-venv\Scripts\python.exe'
    $model = Join-Path $runtime 'models\kokoro-v1.0.int8.onnx'
    $voices = Join-Path $runtime 'models\voices-v1.0.bin'
    foreach ($file in @($python,$model,$voices)) {
        if (!(Test-Path -LiteralPath $file)) { throw ('Video setup is incomplete: ' + (Split-Path $file -Leaf) + '. Run local_video\setup_free_studio.ps1 once to finish setup.') }
    }
    # A named mutex prevents two clicks racing to launch two servers.
    $mutex = New-Object Threading.Mutex($false, 'Local\TrafficLiftStudioLaunch8005')
    if (!$mutex.WaitOne(0)) { exit 0 }
    try {
        $listener = @(Get-NetTCPConnection -LocalPort 8005 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
        if ($listener.Count) {
            if ($listener.Count -ne 1) { throw 'Port 8005 belongs to another application.' }
            $server = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $listener[0])
            $belongs = $false; $ancestor = $server
            for ($i=0; $i -lt 4 -and $ancestor; $i++) {
                if ($ancestor.ExecutablePath -eq $python) { $belongs = $true; break }
                $ancestor = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $ancestor.ParentProcessId) -ErrorAction SilentlyContinue
            }
            if (!$belongs -or $server.CommandLine -notmatch 'trafficlift_pro:app') { throw 'Port 8005 belongs to another application.' }
            $health = Invoke-RestMethod 'http://127.0.0.1:8005/api/v1/health' -TimeoutSec 5
            if ($health.version -ne '5.2.2') { throw 'A different TrafficLift version is running on port 8005.' }
            Open-Studio
            exit 0
        }
        if (!(Get-Command ffmpeg -ErrorAction SilentlyContinue) -or !(Get-Command ffprobe -ErrorAction SilentlyContinue)) {
            $bin = Get-ChildItem (Join-Path $runtime '.video-tools') -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if (!$bin -or !(Test-Path (Join-Path $bin.Directory.FullName 'ffprobe.exe'))) { throw 'Video tools are missing. Run local_video\setup_free_studio.ps1 once.' }
            $env:PATH = $bin.Directory.FullName + ';' + $env:PATH
        }
        $env:TRAFFICLIFT_FREE_VIDEO = '1'
        $env:TRAFFICLIFT_VOICE_MODEL = $model
        $env:TRAFFICLIFT_VOICES = $voices
        $env:TRAFFICLIFT_LEGACY_VIDEO_DIR = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'TrafficLift\Videos'
        $env:TRAFFICLIFT_VIDEO_DIR = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'TrafficLift\Videos-v5'
        $dataRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'TrafficLift\Studio-v5'
        New-Item -ItemType Directory -Force $dataRoot | Out-Null
        $campaignDb = Join-Path $dataRoot 'campaigns.db'
        $oldDb = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'TrafficLift\campaigns.db'
        if (!(Test-Path $oldDb)) { $oldDb = Join-Path $runtime 'backend\campaigns.db' }
        if (!(Test-Path $campaignDb) -and (Test-Path $oldDb)) {
            & $python -c "import sqlite3,sys; source=sqlite3.connect(sys.argv[1]); target=sqlite3.connect(sys.argv[2]); source.backup(target); target.close(); source.close()" $oldDb $campaignDb
            if ($LASTEXITCODE -ne 0) { throw 'Could not preserve the previous campaign history. No history was deleted.' }
        }
        $env:CAMPAIGNS_DB_PATH = $campaignDb
        $env:TRAFFICLIFT_WORKSPACE_DB = Join-Path $dataRoot 'workspace-v5.sqlite3'
        $env:RELOAD = 'false'
        $logs = Join-Path $repo 'logs'
        New-Item -ItemType Directory -Force $logs | Out-Null
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $errorLog = Join-Path $logs ('studio-' + $stamp + '.error.log')
        $outLog = Join-Path $logs ('studio-' + $stamp + '.log')
        $process = Start-Process -FilePath $python -ArgumentList '-m uvicorn trafficlift_pro:app --host 127.0.0.1 --port 8005' -WorkingDirectory $repo -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errorLog -PassThru
        $ready = $false
        for ($i=0; $i -lt 60; $i++) {
            $process.Refresh()
            if ($process.HasExited) { break }
            try { $health = Invoke-RestMethod 'http://127.0.0.1:8005/api/v1/health' -TimeoutSec 1; if ($health.version -eq '5.2.2') { $ready = $true; break } } catch {}
            Start-Sleep -Milliseconds 500
        }
        if (!$ready) { throw ('TrafficLift could not start. The diagnostic log is ' + $errorLog) }
        Open-Studio
    } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
} catch {
    [Windows.MessageBox]::Show($_.Exception.Message, 'TrafficLift Studio', 'OK', 'Error') | Out-Null
    exit 1
}
