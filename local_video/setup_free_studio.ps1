$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
$python = Join-Path $repo '.video-venv\Scripts\python.exe'
if (!(Test-Path $python)) {
    & py -3.11 -m venv (Join-Path $repo '.video-venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is needed. Install Python 3.11 from python.org, then run this file again.' }
}
$marker = Join-Path $repo '.video-venv\studio-installed.txt'
$requirementsHash = (Get-FileHash 'requirements-video.txt').Hash + (Get-FileHash 'requirements.txt').Hash
if (!(Test-Path $marker) -or (Get-Content $marker -Raw).Trim() -ne $requirementsHash) {
    & $python -m pip install -r requirements-video.txt
    if ($LASTEXITCODE -ne 0) { throw 'Video dependency installation failed.' }
    Set-Content $marker $requirementsHash
}
$models = Join-Path $repo 'models'
New-Item -ItemType Directory -Force $models | Out-Null
function Get-VerifiedFile($url, $path, $hash) {
    if ((Test-Path $path) -and (Get-FileHash $path -Algorithm SHA256).Hash -eq $hash) { return }
    $temp = "$path.partial"
    Invoke-WebRequest -UseBasicParsing $url -OutFile $temp
    if ((Get-FileHash $temp -Algorithm SHA256).Hash -ne $hash) {
        Remove-Item $temp -Force
        throw 'Downloaded voice file failed its integrity check. Try again.'
    }
    Move-Item $temp $path -Force
}
Get-VerifiedFile 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx' (Join-Path $models 'kokoro-v1.0.int8.onnx') 'ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c'
Get-VerifiedFile 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin' (Join-Path $models 'voices-v1.0.bin') 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d'
if (!(Get-Command ffmpeg -ErrorAction SilentlyContinue) -or !(Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    $tools = Join-Path $repo '.video-tools'
    New-Item -ItemType Directory -Force $tools | Out-Null
    $bin = Get-ChildItem $tools -Filter ffmpeg.exe -Recurse | Select-Object -First 1
    if (!$bin) {
        $zip = Join-Path $tools 'ffmpeg.zip'
        $url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
        $checksum = (Invoke-WebRequest -UseBasicParsing "$url.sha256").Content.Trim().Split(' ')[0]
        Get-VerifiedFile $url $zip $checksum
        Expand-Archive $zip -DestinationPath $tools -Force
        Remove-Item $zip
        $bin = Get-ChildItem $tools -Filter ffmpeg.exe -Recurse | Select-Object -First 1
    }
    if (!$bin) { throw 'FFmpeg could not be found after setup.' }
    $env:PATH = $bin.Directory.FullName + ';' + $env:PATH
}
$env:TRAFFICLIFT_FREE_VIDEO = '1'
$env:TRAFFICLIFT_VOICE_MODEL = Join-Path $models 'kokoro-v1.0.int8.onnx'
$env:TRAFFICLIFT_VOICES = Join-Path $models 'voices-v1.0.bin'
$env:TRAFFICLIFT_VIDEO_DIR = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'TrafficLift-Free-Videos'
$env:RELOAD = 'false'
# Refuse an occupied port instead of opening an unrelated service.
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8000 is already in use. Close the previous TrafficLift Studio window and retry.' }
Start-Process powershell -ArgumentList '-NoProfile', '-Command', 'Start-Sleep -Seconds 5; Start-Process "http://127.0.0.1:8000/studio?api=http://127.0.0.1:8000"'
Write-Host 'Free TrafficLift Studio is starting. Keep this window open.'
& $python -m uvicorn trafficlift_pro:app --host 127.0.0.1 --port 8000
if ($LASTEXITCODE -ne 0) { throw 'The studio server stopped with an error.' }
