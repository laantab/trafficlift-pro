"""Opt-in, owner-authenticated live website-to-MP4 acceptance probe.

No network requests occur on import. A new render is created ONLY with --run
and the required environment variables. --resume rechecks an existing job
without consuming another daily render slot. This is not a browser UI test.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
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
        raise AcceptanceError(
            "Unauthenticated video history did not return HTTP 401"
        )
    return {"health": "ok", "owner_history": "protected"}


def verify(session, access_code, *, payload=None, resume=None, output=None,
           timeout=1800, interval=5, now=time.monotonic, sleep=time.sleep):
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
        temp_path.replace(output)
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
            parser.exit(1, "Owner video preflight failed: " + str(exc)[:240] + "\\n")
        print("Owner video preflight passed: hosted health OK; private video history denied (HTTP 401); no render started")
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
    try:
        with requests.Session() as session:
            result = verify(session, code, payload=payload, resume=args.resume,
                            output=args.output, timeout=args.timeout)
    except (AcceptanceError, requests.RequestException, ValueError) as exc:
        # Never print HTTP bodies, request headers, cookies, or access codes.
        parser.exit(1, "Owner MP4 acceptance failed: " + str(exc)[:240] + "\n")
    print(f"Owner MP4 acceptance passed: job={result['job_id']} bytes={result['bytes']} file={result['file']}")


if __name__ == "__main__":
    main()
