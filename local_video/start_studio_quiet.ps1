$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
Add-Type -AssemblyName PresentationFramework
function Open-Studio {
    $url = 'http://127.0.0.1:8000/studio?api=http://127.0.0.1:8000'
    $edge = @((Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'), (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($edge) { Start-Process $edge -ArgumentList ('--app=' + $url) }
    else { Start-Process $url }
}
try {
    $python = Join-Path $repo '.video-venv\Scripts\python.exe'
    $model = Join-Path $repo 'models\kokoro-v1.0.int8.onnx'
    $voices = Join-Path $repo 'models\voices-v1.0.bin'
    foreach ($file in @($python,$model,$voices)) {
        if (!(Test-Path -LiteralPath $file)) { throw ('Video setup is incomplete: ' + (Split-Path $file -Leaf) + '. Run local_video\setup_free_studio.ps1 once to finish setup.') }
    }
    # A named mutex prevents two clicks racing to launch two servers.
    $mutex = New-Object Threading.Mutex($false, 'Local\TrafficLiftStudioLaunch8000')
    if (!$mutex.WaitOne(0)) { exit 0 }
    try {
        $listener = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
        if ($listener.Count) {
            if ($listener.Count -ne 1) { throw 'Port 8000 belongs to another application.' }
            $server = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $listener[0])
            $belongs = $false; $ancestor = $server
            for ($i=0; $i -lt 4 -and $ancestor; $i++) {
                if ($ancestor.ExecutablePath -eq $python) { $belongs = $true; break }
                $ancestor = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $ancestor.ParentProcessId) -ErrorAction SilentlyContinue
            }
            if (!$belongs -or $server.CommandLine -notmatch 'trafficlift_pro:app') { throw 'Port 8000 belongs to another application.' }
            $null = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 5
            Open-Studio
            exit 0
        }
        if (!(Get-Command ffmpeg -ErrorAction SilentlyContinue) -or !(Get-Command ffprobe -ErrorAction SilentlyContinue)) {
            $bin = Get-ChildItem (Join-Path $repo '.video-tools') -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if (!$bin -or !(Test-Path (Join-Path $bin.Directory.FullName 'ffprobe.exe'))) { throw 'Video tools are missing. Run local_video\setup_free_studio.ps1 once.' }
            $env:PATH = $bin.Directory.FullName + ';' + $env:PATH
        }
        $env:TRAFFICLIFT_FREE_VIDEO = '1'
        $env:TRAFFICLIFT_VOICE_MODEL = $model
        $env:TRAFFICLIFT_VOICES = $voices
        $env:TRAFFICLIFT_VIDEO_DIR = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'TrafficLift-Free-Videos'
        $env:RELOAD = 'false'
        $logs = Join-Path $repo 'logs'
        New-Item -ItemType Directory -Force $logs | Out-Null
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $errorLog = Join-Path $logs ('studio-' + $stamp + '.error.log')
        $outLog = Join-Path $logs ('studio-' + $stamp + '.log')
        $process = Start-Process -FilePath $python -ArgumentList '-m uvicorn trafficlift_pro:app --host 127.0.0.1 --port 8000' -WorkingDirectory $repo -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errorLog -PassThru
        $ready = $false
        for ($i=0; $i -lt 60; $i++) {
            $process.Refresh()
            if ($process.HasExited) { break }
            try { $null = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 1; $ready = $true; break } catch {}
            Start-Sleep -Milliseconds 500
        }
        if (!$ready) { throw ('TrafficLift could not start. The diagnostic log is ' + $errorLog) }
        Open-Studio
    } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
} catch {
    [Windows.MessageBox]::Show($_.Exception.Message, 'TrafficLift Studio', 'OK', 'Error') | Out-Null
    exit 1
}
