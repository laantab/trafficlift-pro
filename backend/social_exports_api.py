"""Owner-only social-format exports from an already completed HeyGen job.

One provider generation; three local FFmpeg conversions; no paid generation.
Never accepts an arbitrary media URL from the caller.
"""
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend.photo_video_api import video_guard
from backend.heygen_video_api import configured, provider_request, BASE

router = APIRouter()
_ID = re.compile(r"[A-Za-z0-9_-]{6,128}")
_MAX_DOWNLOAD = 90 * 1024 * 1024
_FORMATS = (
    ("TikTok-Reels-Shorts-9x16.mp4", 720, 1280),
    ("Instagram-Facebook-Feed-4x5.mp4", 864, 1080),
    ("Square-Feed-1x1.mp4", 768, 768),
)


def _public_https(url):
    """Reject private/reserved destinations; URL comes only from HeyGen's API."""
    if not isinstance(url, str) or len(url) > 2048:
        raise HTTPException(502, "Completed video link is unavailable.")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(502, "Provider returned an invalid video link.")
    if parsed.port not in (None, 443):
        raise HTTPException(502, "Provider returned an unsupported video port.")
    try:
        records = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not records or any(not ipaddress.ip_address(record[4][0]).is_global for record in records):
            raise ValueError("Non-public media host")
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "Provider video host could not be verified.") from exc
    return url


def _download(url, target):
    # Provider link is fetched only on the server; never allow redirects to
    # arbitrary destinations or user-supplied URLs (SSRF protection).
    try:
        with requests.get(_public_https(url), stream=True, timeout=(10, 45),
                          allow_redirects=False) as response:
            if response.status_code != 200:
                raise HTTPException(502, "Completed video could not be downloaded.")
            size = 0
            with target.open("wb") as fh:
                for part in response.iter_content(chunk_size=256 * 1024):
                    if not part:
                        continue
                    size += len(part)
                    if size > _MAX_DOWNLOAD:
                        raise HTTPException(413, "Source video exceeds the 90 MB export limit.")
                    fh.write(part)
            if size < 2048:
                raise HTTPException(502, "Completed video download was incomplete.")
    except requests.RequestException as exc:
        raise HTTPException(502, "Could not retrieve the completed video for export.") from exc


def _ffmpeg():
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
    except Exception:
        pass
    binary = shutil.which("ffmpeg")
    if not binary:
        raise HTTPException(503, "Video conversion tools are unavailable.")
    return binary


def _convert(source, destination, width, height):
    # Blurred, full-bleed background + whole uncropped foreground.
    filter_graph = (
        f"[0:v]split=2[base][front];"
        f"[base]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=16:1[bg];"
        f"[front]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]"
    )
    command = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin",
               "-y", "-i", str(source), "-filter_complex", filter_graph,
               "-map", "[v]", "-map", "0:a?", "-c:v", "libx264",
               "-preset", "veryfast", "-crf", "20", "-r", "30",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
               "-t", "45", str(destination)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=140)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise HTTPException(502, "Video conversion failed; original HeyGen video is unchanged.") from exc
    if not destination.is_file() or destination.stat().st_size < 2048:
        raise HTTPException(502, "Exported video was incomplete.")


@router.post("/api/v1/heygen-video/social-exports/{video_id}")
def social_exports(video_id: str, request: Request):
    video_guard(request)
    if not _ID.fullmatch(video_id):
        raise HTTPException(404, "Unknown completed video.")
    if not configured():
        raise HTTPException(503, "HeyGen is not configured for owner exports.")
    result = provider_request("GET", BASE + "/" + video_id)
    status = result.get("status") or (result.get("data") or {}).get("status")
    url = result.get("video_url") or (result.get("data") or {}).get("video_url")
    if status != "completed" or not url:
        raise HTTPException(409, "This HeyGen video has not completed yet.")
    if shutil.disk_usage(tempfile.gettempdir()).free < 500 * 1024 * 1024:
        raise HTTPException(507, "Not enough temporary space for social exports.")
    work = Path(tempfile.mkdtemp(prefix="trafficlift-social-"))
    try:
        source = work / "source.mp4"
        _download(url, source)
        destination = work / "TrafficLift-Social-Formats.zip"
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_STORED) as bundle:
            for filename, width, height in _FORMATS:
                rendered = work / filename
                _convert(source, rendered, width, height)
                bundle.write(rendered, arcname=filename)
        return FileResponse(destination, media_type="application/zip",
                            filename="TrafficLift-Social-Formats.zip",
                            background=BackgroundTask(shutil.rmtree, work, ignore_errors=True))
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
