"""Opt-in, owner-authenticated live website-to-MP4 acceptance probe.

No network requests occur on import. A new render is created ONLY with --run
and the required environment variables. --resume rechecks an existing job
without consuming another daily render slot. This is not a browser UI test.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

import requests

BASE = "https://trafficlift-pro.onrender.com"
JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_MP4_BYTES = 500 * 1024 * 1024


class AcceptanceError(RuntimeError):
    pass


def _check(response, expected, label):
    if response.status_code != expected:
        raise AcceptanceError(f"{label}: expected HTTP {expected}, got {response.status_code}")
    if "no-store" not in response.headers.get("Cache-Control", "").lower():
        raise AcceptanceError(f"{label}: private response is missing no-store")


def _job_id(value):
    if not JOB_ID.fullmatch(value or ""):
        raise AcceptanceError("Server returned an invalid video job ID")
    return value




def _verify_decoded_mp4(path):
    """Require actual decodable video and audio, not just an ftyp signature.

    Suppress tool output: downloaded media can contain untrusted metadata.
    The opt-in probe needs local ffprobe and ffmpeg, like browser acceptance.
    """
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height", "-of", "json",
             str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30, check=False,
        )
        if probe.returncode != 0:
            raise AcceptanceError("MP4 invalid: ffprobe could not inspect download")
        media = json.loads(probe.stdout)
        streams = media.get("streams", [])
        if (not isinstance(streams, list)
                or not any(s.get("codec_type") == "video"
                           and s.get("width", 0) > 0 and s.get("height", 0) > 0
                           for s in streams if isinstance(s, dict))
                or not any(s.get("codec_type") == "audio"
                           for s in streams if isinstance(s, dict))
                or float(media.get("format", {}).get("duration", 0)) <= 0):
            raise AcceptanceError("MP4 invalid: video, audio or duration is missing")
        decoded = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i",
             str(path), "-map", "0:v:0", "-map", "0:a:0",
             "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=180, check=False,
        )
        if decoded.returncode != 0:
            raise AcceptanceError("MP4 invalid: full video/audio decode failed")
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
        raise AcceptanceError("MP4 invalid: unable to decode downloaded media") from exc


def preflight(session):
    """Read-only hosted check: health is live and owner video history is private.

    Uses a fresh unauthenticated session; never logs in, renders, or downloads.
    """
    health = session.get(BASE + "/api/v1/health", timeout=20, allow_redirects=False)
    if health.status_code != 200:
        raise AcceptanceError(
            f"Hosted health check: expected HTTP 200, got {health.status_code}"
        )
    denied = session.get(BASE + "/api/v1/photo-videos", timeout=20, allow_redirects=False)
    if denied.status_code != 401:
        raise AcceptanceError("Unauthenticated video history did not return HTTP 401")

    # Use a syntactically valid but non-existent job ID to confirm the guard
    # runs before job lookup. Never start a render or send owner credentials.
    probe_url = BASE + "/api/v1/photo-videos/" + "a" * 32
    status = session.get(probe_url, timeout=20, allow_redirects=False)
    if status.status_code != 401:
        raise AcceptanceError("Unauthenticated video status did not return HTTP 401")

    # Stream so a mistakenly public MP4 response is never downloaded during
    # this zero-quota check; always close the connection, even on failure.
    mp4 = session.get(probe_url + "/video", timeout=20,
                      allow_redirects=False, stream=True)
    try:
        if mp4.status_code != 401:
            raise AcceptanceError("Unauthenticated MP4 download did not return HTTP 401")
    finally:
        mp4.close()

    # The platform posting ZIP is another private download surface. Keep
    # streaming disabled for body reads and close even on an unexpected 200.
    exported = session.get(probe_url + "/exports/tiktok", timeout=20,
                           allow_redirects=False, stream=True)
    try:
        if exported.status_code != 401:
            raise AcceptanceError("Unauthenticated video export did not return HTTP 401")
    finally:
        exported.close()

    # The genuine Kokoro sample is owner-only too. This is a status check,
    # not permission to publish the sample or render a new product video.
    sample = session.get(BASE + "/api/v1/video-owner-acceptance",
                         timeout=20, allow_redirects=False)
    if sample.status_code != 401:
        raise AcceptanceError("Unauthenticated owner sample status did not return HTTP 401")
    sample_mp4 = session.get(BASE + "/api/v1/video-owner-acceptance/mp4",
                             timeout=20, allow_redirects=False, stream=True)
    try:
        if sample_mp4.status_code != 401:
            raise AcceptanceError("Unauthenticated owner sample MP4 did not return HTTP 401")
    finally:
        sample_mp4.close()

    # Privacy alone does not prove the existing hosted engine is ready. A
    # healthy web process with missing voice assets would otherwise pass this
    # preflight and invite an owner to waste a scarce render slot.
    readiness = session.get(BASE + "/api/v1/video-readiness",
                            timeout=20, allow_redirects=False)
    if readiness.status_code != 200:
        raise AcceptanceError("Hosted video readiness did not return HTTP 200")
    try:
        details = readiness.json()
    except (ValueError, TypeError) as exc:
        raise AcceptanceError("Hosted video readiness was not valid JSON") from exc
    if not isinstance(details, dict) or details.get("state") != "ready":
        raise AcceptanceError("Hosted video prerequisites are not ready")
    # This existing field describes the operator-only hosted render switch;
    # it is NOT evidence that customer/public rendering is authorized.
    if details.get("customer_rendering_enabled") is not True:
        raise AcceptanceError("Hosted owner video rendering is disabled")
    return {"health": "ok", "owner_history": "protected",
            "owner_status": "protected", "owner_mp4": "protected",
            "owner_exports": "protected", "owner_sample_status": "protected",
            "owner_sample_mp4": "protected", "video_prerequisites": "ready"}


def verify(session, access_code, *, payload=None, resume=None, output=None,
           timeout=1800, interval=5, now=time.monotonic, sleep=time.sleep,
           on_created=None):
    """Verify auth, create/resume, poll, and stream a real MP4 to disk.

    session is a requests.Session-compatible object. Never log access_code.
    """
    if not access_code:
        raise AcceptanceError("TRAFFICLIFT_OWNER_ACCESS_CODE is required")
    if (payload is None) == (resume is None):
        raise AcceptanceError("Choose exactly one of a new payload or --resume")
    if resume is not None:
        _job_id(resume)
    output = Path(output or "trafficlift-owner-verified.mp4")
    if output.exists():
        raise AcceptanceError("Output already exists; choose a different --output path")

    denied = session.get(BASE + "/api/v1/photo-videos", timeout=20, allow_redirects=False)
    if denied.status_code != 401:
        raise AcceptanceError("Unauthenticated video history did not return HTTP 401")

    login = session.post(BASE + "/api/v1/video-owner-login",
                         json={"access_code": access_code}, headers={"Origin": BASE},
                         timeout=20, allow_redirects=False)
    _check(login, 200, "Owner login")
    cookie = login.headers.get("Set-Cookie", "").lower()
    if not all(flag in cookie for flag in ("httponly", "secure", "samesite=strict")):
        raise AcceptanceError("Owner login cookie is missing required security flags")

    if resume is None:
        created = session.post(BASE + "/api/v1/photo-videos", json=payload,
                               headers={"Origin": BASE}, timeout=40,
                               allow_redirects=False)
        _check(created, 202, "Video creation")
        job_id = _job_id(created.json().get("video", {}).get("id"))
        # Announce the non-secret job ID before polling. If the connection fails
        # later, the operator can --resume without starting a second render.
        if on_created is not None:
            on_created(job_id)
    else:
        job_id = resume

    deadline = now() + timeout
    while True:
        response = session.get(BASE + "/api/v1/photo-videos/" + job_id,
                               timeout=30, allow_redirects=False)
        _check(response, 200, "Video status")
        video = response.json().get("video", {})
        state = video.get("status")
        if state == "succeeded":
            break
        if state == "failed":
            raise AcceptanceError("Video render failed: " + str(video.get("message", ""))[:200])
        if state not in ("queued", "running"):
            raise AcceptanceError("Unexpected video state: " + str(state)[:80])
        if now() >= deadline:
            raise AcceptanceError("Video did not finish before timeout; resume job " + job_id)
        sleep(min(interval, max(0, deadline - now())))

    downloaded = session.get(BASE + "/api/v1/photo-videos/" + job_id + "/video",
                             timeout=(20, 180), stream=True, allow_redirects=False)
    # A streamed response must be closed even when headers are rejected or
    # the output directory cannot be created. Otherwise failed acceptance
    # retries can retain open connections to the private MP4 endpoint.
    temp_path = None
    try:
        _check(downloaded, 200, "MP4 download")
        cache_directives = {
            directive.strip().split("=", 1)[0].lower()
            for directive in downloaded.headers.get("Cache-Control", "").split(",")
        }
        if "private" not in cache_directives or "public" in cache_directives:
            raise AcceptanceError("MP4 download requires a private cache policy")
        if not downloaded.headers.get("Content-Type", "").lower().startswith("video/mp4"):
            raise AcceptanceError("Download is not an MP4 response")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".partial", delete=False) as file:
            temp_path = Path(file.name)
            size = 0
            header = b""
            for chunk in downloaded.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_MP4_BYTES:
                    raise AcceptanceError("MP4 exceeds the 500 MB safety cap")
                if len(header) < 12:
                    header = (header + chunk)[:12]
                file.write(chunk)
            if size < 1024 or len(header) < 12 or header[4:8] != b"ftyp":
                raise AcceptanceError("Downloaded bytes are not a plausible MP4")
        _verify_decoded_mp4(temp_path)
        # The output may have appeared while streaming. Path.replace would
        # silently overwrite another operator's MP4 on POSIX and Windows.
        # The partial file is in the same directory, so a hard link publishes
        # it atomically only if the destination does not already exist.
        try:
            os.link(temp_path, output)
        except FileExistsError as exc:
            raise AcceptanceError("Output already exists; choose a different --output path") from exc
        return {"job_id": job_id, "bytes": size, "file": str(output)}
    finally:
        downloaded.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true", help="Read-only hosted health and owner privacy check; no access code or render")
    group.add_argument("--run", action="store_true", help="Create ONE new owner-only video; consumes one quota slot")
    group.add_argument("--resume", metavar="JOB_ID", help="Check/download an existing video; no new render")
    parser.add_argument("--output", default="trafficlift-owner-verified.mp4")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.timeout < 1 or args.timeout > 3600:
        parser.error("--timeout must be between 1 and 3600 seconds")
    if args.preflight:
        try:
            with requests.Session() as session:
                preflight(session)
        except (AcceptanceError, requests.RequestException, ValueError) as exc:
            parser.exit(1, "Owner video preflight failed: " + str(exc)[:240] + "\n")
        print("Owner video preflight passed: hosted health OK; private video history, status, MP4, platform exports and owner sample endpoints denied (HTTP 401); no render started")
        return
    code = os.environ.get("TRAFFICLIFT_OWNER_ACCESS_CODE", "")
    payload = None
    if args.run:
        names = ("TRAFFICLIFT_ACCEPTANCE_PRODUCT_NAME",
                 "TRAFFICLIFT_ACCEPTANCE_IMAGE_URL",
                 "TRAFFICLIFT_ACCEPTANCE_PRODUCT_URL")
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            parser.error("Missing environment variables: " + ", ".join(missing))
        payload = {
            "name": os.environ[names[0]],
            "image_url": os.environ[names[1]],
            "product_url": os.environ[names[2]],
            "benefit": os.environ.get("TRAFFICLIFT_ACCEPTANCE_BENEFIT", ""),
            "seconds": 15,
            "style": "clean",
        }
        if not all(payload[key].startswith("https://") for key in ("image_url", "product_url")):
            parser.error("Product and image URLs must use HTTPS")
    def announce_created(job_id):
        print(f"Owner video created: job={job_id}; if interrupted, use --resume {job_id}", flush=True)

    try:
        with requests.Session() as session:
            result = verify(session, code, payload=payload, resume=args.resume,
                            output=args.output, timeout=args.timeout,
                            on_created=announce_created if args.run else None)
    except (AcceptanceError, requests.RequestException, ValueError) as exc:
        # Never print HTTP bodies, request headers, cookies, or access codes.
        parser.exit(1, "Owner MP4 acceptance failed: " + str(exc)[:240] + "\n")
    print(f"Owner MP4 acceptance passed: job={result['job_id']} bytes={result['bytes']} file={result['file']}")


if __name__ == "__main__":
    main()
