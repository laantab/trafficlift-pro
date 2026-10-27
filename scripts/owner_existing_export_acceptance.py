"""Verify existing owner MP4 and all three social ZIPs without creating a render.

Explicit, opt-in, authenticated GET-only acceptance. No uploads, publishing,
new video jobs, or additional paid services. Requires an existing 32-hex job ID
and a private TRAFFICLIFT_OWNER_ACCESS_CODE environment variable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

import requests

BASE = "https://trafficlift-pro.onrender.com"
JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
PLATFORMS = ("tiktok", "youtube-shorts", "instagram-reels")
MAX_MASTER_BYTES = 300 * 1024 * 1024
MAX_KIT_BYTES = MAX_MASTER_BYTES + 20 * 1024 * 1024


class AcceptanceError(RuntimeError):
    pass


def _private(response, label):
    if response.status_code != 200:
        raise AcceptanceError(f"{label}: expected HTTP 200, got {response.status_code}")
    cache = {token.strip().split("=", 1)[0].lower()
             for token in response.headers.get("Cache-Control", "").split(",")}
    if "no-store" not in cache or "private" not in cache or "public" in cache:
        raise AcceptanceError(f"{label}: unsafe private cache policy")


def _download(session, url, path, *, label, mime, limit):
    # All media responses are streamed and closed even on a rejected header.
    with session.get(url, timeout=(20, 180), allow_redirects=False, stream=True) as response:
        _private(response, label)
        if not response.headers.get("Content-Type", "").lower().startswith(mime):
            raise AcceptanceError(f"{label}: unexpected media type")
        size = 0
        digest = hashlib.sha256()
        with path.open("wb") as target:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise AcceptanceError(f"{label}: download exceeds safety cap")
                digest.update(chunk)
                target.write(chunk)
        if not size:
            raise AcceptanceError(f"{label}: empty download")
        return size, digest.hexdigest()


def _verify_kit(path, platform, job_id, master_size, master_digest):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            required = {"video.mp4", "manifest.json", "safe_zone_review.html", "post.txt"}
            if len(names) != len(set(names)) or not required.issubset(names):
                raise AcceptanceError(f"{platform}: missing or duplicate kit files")
            if set(names) - required - {"captions.srt"}:
                raise AcceptanceError(f"{platform}: unexpected kit files")
            media = archive.getinfo("video.mp4")
            if media.file_size != master_size:
                raise AcceptanceError(f"{platform}: MP4 size differs from master")
            digest = hashlib.sha256()
            with archive.open("video.mp4") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != master_digest:
                raise AcceptanceError(f"{platform}: copied MP4 differs from master")
            manifest_info = archive.getinfo("manifest.json")
            preview_info = archive.getinfo("safe_zone_review.html")
            if manifest_info.file_size > 64 * 1024 or preview_info.file_size > 128 * 1024:
                raise AcceptanceError(f"{platform}: oversized kit metadata")
            manifest = json.loads(archive.read("manifest.json"))
            if (not isinstance(manifest, dict)
                    or manifest.get("platform") != platform
                    or manifest.get("job_id") != job_id
                    or manifest.get("video_master_reused") is not True
                    or manifest.get("video_reencoded") is not False
                    or manifest.get("safe_zone_approval") != "MANUAL_REVIEW_REQUIRED"
                    or manifest.get("safe_zone_preview") != "safe_zone_review.html"):
                raise AcceptanceError(f"{platform}: manifest does not match finished master")
            preview = archive.read("safe_zone_review.html").decode("utf-8")
            if ('src="video.mp4"' not in preview
                    or "illustrative" not in preview.lower()
                    or "manual" not in preview.lower()):
                raise AcceptanceError(f"{platform}: safe-zone review is missing")
            if "captions.srt" in names and archive.getinfo("captions.srt").file_size > 1024 * 1024:
                raise AcceptanceError(f"{platform}: oversized caption transcript")
    except (zipfile.BadZipFile, EOFError, UnicodeError, ValueError, KeyError, OSError) as exc:
        raise AcceptanceError(f"{platform}: invalid ZIP or metadata") from exc



def inspect_master_media(path):
    """Fail closed unless ffprobe confirms the required shared master format.

    This reads the already-downloaded file only. It never transcodes, renders,
    publishes, or uploads media. Do not expose untrusted ffprobe metadata.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height:format=duration",
             "-of", "json", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30, check=False, text=True,
        )
        if result.returncode != 0:
            raise AcceptanceError("Master MP4 could not be inspected by ffprobe")
        payload = json.loads(result.stdout)
        streams = payload.get("streams")
        if not isinstance(streams, list):
            raise AcceptanceError("Master MP4 is missing media streams")
        video = any(
            isinstance(stream, dict) and stream.get("codec_type") == "video"
            and stream.get("codec_name") == "h264"
            and stream.get("width") == 1080 and stream.get("height") == 1920
            for stream in streams
        )
        audio = any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio"
            and stream.get("codec_name") == "aac"
            for stream in streams
        )
        duration = float(payload.get("format", {}).get("duration", 0))
        if not video or not audio or not math.isfinite(duration) or duration <= 0:
            raise AcceptanceError("Master must be 1080x1920 H.264/AAC with positive duration")
        return {"video": "h264", "audio": "aac", "width": 1080, "height": 1920}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError,
            ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("Master MP4 media inspection failed; ffprobe is required") from exc

def verify_existing_exports(session, access_code, job_id, *, temp_root=None):
    """Download and verify one existing master plus 3 kits; no POST except login."""
    if not access_code:
        raise AcceptanceError("TRAFFICLIFT_OWNER_ACCESS_CODE is required")
    if not JOB_ID.fullmatch(job_id or ""):
        raise AcceptanceError("A valid existing 32-character video job ID is required")
    base = BASE + "/api/v1/photo-videos/" + job_id
    with session.get(BASE + "/api/v1/photo-videos", timeout=20,
                     allow_redirects=False, stream=True) as anonymous:
        if anonymous.status_code != 401:
            raise AcceptanceError("Anonymous video history is not protected")
    with session.post(BASE + "/api/v1/video-owner-login",
                      json={"access_code": access_code}, headers={"Origin": BASE},
                      timeout=20, allow_redirects=False) as login:
        _private(login, "Owner login")
        cookie = login.headers.get("Set-Cookie", "").lower()
        if not all(flag in cookie for flag in ("httponly", "secure", "samesite=strict")):
            raise AcceptanceError("Owner login cookie is missing security flags")
    # A single temporary directory is removed on any error, including bad ZIPs.
    with tempfile.TemporaryDirectory(prefix="trafficlift-export-acceptance-",
                                     dir=temp_root) as directory:
        root = Path(directory)
        master_size, master_hash = _download(
            session, base + "/video", root / "master.mp4",
            label="Master MP4", mime="video/mp4", limit=MAX_MASTER_BYTES,
        )
        with (root / "master.mp4").open("rb") as master:
            master.seek(4)
            if master.read(4) != b"ftyp":
                raise AcceptanceError("Master download is not a plausible MP4")
        media_format = inspect_master_media(root / "master.mp4")
        for platform in PLATFORMS:
            path = root / (platform + ".zip")
            _download(session, base + "/exports/" + platform, path,
                      label=platform + " kit", mime="application/zip",
                      limit=MAX_KIT_BYTES)
            _verify_kit(path, platform, job_id, master_size, master_hash)
            path.unlink()
    return {"job_id": job_id, "master_bytes": master_size,
            "master_format": media_format,
            "platforms": list(PLATFORMS), "render_started": False,
            "visual_approval": "PENDING"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True, help="Existing completed 32-hex job ID")
    args = parser.parse_args()
    code = os.environ.get("TRAFFICLIFT_OWNER_ACCESS_CODE", "")
    try:
        with requests.Session() as session:
            result = verify_existing_exports(session, code, args.job_id)
    except (AcceptanceError, requests.RequestException, OSError) as exc:
        parser.exit(1, "Existing export acceptance failed: " + str(exc)[:200] + "\n")
    print("Existing master and all three platform kits match: "
          + ", ".join(result["platforms"])
          + "; no new render. Visual and platform in-app approval still required.")


if __name__ == "__main__":
    main()
