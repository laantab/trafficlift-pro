"""Loopback-only job bridge from TrafficLift Pro to the installed LTX runner.

This service is intended for one Windows desktop. It never binds to a LAN
interface, accepts requests only from TrafficLift Pro's GitHub Pages origin,
requires a per-launch bearer token, and runs one local render at a time.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = "127.0.0.1"
PORT = int(os.environ.get("TRAFFICLIFT_BRIDGE_PORT", "8765"))
ALLOWED_ORIGINS = {"https://laantab.github.io"}
MAX_REQUEST_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_PROMPT_CHARS = 7000
MAX_FRAMES = 145
FPS = 24
WIDTH = 384
HEIGHT = 672
TOKEN = secrets.token_urlsafe(32)

BASE = Path.home() / "Documents" / "TrafficLift-Local-Video"
PYTHON = BASE / ".venv" / "Scripts" / "python.exe"
LTX_REPO = BASE / "LTX-Video"
CONFIG = LTX_REPO / "configs" / "trafficlift-rtx3060-2b.yaml"
RUNNER = Path(__file__).resolve().parent / "trafficlift_local_video.py"
OUTPUT_DIR = BASE / "outputs"
JOBS_DIR = BASE / "bridge_jobs"


@dataclass
class Job:
    job_id: str
    status: str
    created_at: float
    updated_at: float
    prompt: str
    frames: int
    message: str
    progress: int = 0
    error: str | None = None
    output_path: str | None = None


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
ACTIVE_LOCK = threading.Lock()
ACTIVE_JOB: str | None = None


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirects are handled manually so every destination is revalidated."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _validate_public_https_url(raw_url: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(raw_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Product image must use a public HTTPS URL.")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("Local image URLs are not allowed.")
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
            }
        except OSError as exc:
            raise ValueError("Could not resolve the product image host.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Product image host must resolve only to public internet addresses.")
    return parsed


def _download_product_image(raw_url: str, job_dir: Path) -> Path:
    opener = urllib.request.build_opener(SafeRedirectHandler())
    current = raw_url
    response = None
    for _ in range(5):
        parsed = _validate_public_https_url(current)
        request = urllib.request.Request(
            current,
            headers={"User-Agent": "TrafficLift-Pro-Local-Video/1.0", "Accept": "image/png,image/jpeg,image/webp"},
        )
        try:
            response = opener.open(request, timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise ValueError(f"Image server returned HTTP {exc.code}.") from exc
            location = exc.headers.get("Location")
            if not location:
                raise ValueError("Image server returned an invalid redirect.") from exc
            current = urllib.parse.urljoin(current, location)
            continue
        break
    if response is None:
        raise ValueError("Product image redirected too many times.")
    with response:
        content_type = response.headers.get_content_type().lower()
        if content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("Product image must be PNG, JPEG, or WebP.")
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_IMAGE_BYTES:
            raise ValueError("Product image is larger than 12 MB.")
        payload = response.read(MAX_IMAGE_BYTES + 1)
        if not payload or len(payload) > MAX_IMAGE_BYTES:
            raise ValueError("Product image is empty or larger than 12 MB.")
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[content_type]
        path = job_dir / ("product" + suffix)
        path.write_bytes(payload)
        return path


def _job_snapshot(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "video_url": f"/api/jobs/{job.job_id}/video" if job.status == "succeeded" else None,
        "frames": job.frames,
        "fps": FPS,
        "width": WIDTH,
        "height": HEIGHT,
    }


def _set_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = time.time()


def _run_job(job_id: str, image_url: str) -> None:
    global ACTIVE_JOB
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "render.log"
    image_path: Path | None = None
    try:
        _set_job(job_id, status="downloading", message="Downloading the selected product image.")
        image_path = _download_product_image(image_url, job_dir)
        _set_job(job_id, status="running", message="Generating on the RTX 3060. This may take several minutes.")
        before = {item.resolve() for item in OUTPUT_DIR.glob("*.mp4")}
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            command = [
                str(PYTHON), str(RUNNER), "--repo", str(LTX_REPO), "--image", str(image_path),
                "--prompt", JOBS[job_id].prompt, "--output", str(OUTPUT_DIR),
                "--width", str(WIDTH), "--height", str(HEIGHT), "--frames", str(JOBS[job_id].frames),
                "--fps", str(FPS),
            ]
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                cwd=str(LTX_REPO),
            )
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
                match = re.search(r"\b(\d{1,3})%\|", line)
                if match:
                    pct = max(0, min(99, int(match.group(1))))
                    _set_job(job_id, progress=pct)
            return_code = process.wait()
        if return_code:
            details = log_path.read_text(encoding="utf-8", errors="replace")[-1800:]
            raise RuntimeError(details.strip() or f"LTX exited with code {return_code}.")
        candidates = [item for item in OUTPUT_DIR.glob("*.mp4") if item.resolve() not in before]
        if not candidates:
            raise RuntimeError("LTX completed but did not create a new MP4 file.")
        video_path = max(candidates, key=lambda item: item.stat().st_mtime).resolve()
        if not video_path.is_relative_to(OUTPUT_DIR.resolve()) or video_path.stat().st_size < 1000:
            raise RuntimeError("LTX output failed the basic file check.")
        _set_job(job_id, status="succeeded", progress=100, output_path=str(video_path), message="Video ready.")
    except Exception as exc:  # surfaced as a short status and full local log
        _set_job(job_id, status="failed", error=str(exc)[-1800:], message="Video generation failed. See the local render log.")
    finally:
        if image_path:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass
        with ACTIVE_LOCK:
            if ACTIVE_JOB == job_id:
                ACTIVE_JOB = None


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "TrafficLiftLocalBridge/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[bridge] " + (fmt % args))

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin in ALLOWED_ORIGINS

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition")

    def _json(self, code: int, value: dict[str, Any]) -> None:
        data = json.dumps(value).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        if not supplied.startswith("Bearer "):
            return False
        return hmac.compare_digest(supplied[7:], TOKEN)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        if not raw_length.isdigit() or int(raw_length) > MAX_REQUEST_BYTES:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(int(raw_length))
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Request must be a JSON object.")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._origin_ok():
            self.send_error(403, "Origin is not allowed")
            return
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._origin_ok():
            self.send_error(403, "Origin is not allowed")
            return
        if not self._authorized():
            self._json(401, {"detail": "Pairing code is missing or incorrect."})
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            self._json(200, {"ok": True, "service": "TrafficLift local video", "engine": "LTX 0.9.8 2B", "gpu_target": "RTX 3060"})
            return
        match = re.fullmatch(r"/api/jobs/([0-9a-f-]{36})(/video)?", path)
        if not match:
            self._json(404, {"detail": "Route not found."})
            return
        job_id, video_suffix = match.groups()
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            snapshot = _job_snapshot(job) if job else None
            output_path = job.output_path if job else None
        if not snapshot:
            self._json(404, {"detail": "Job not found."})
            return
        if not video_suffix:
            self._json(200, {"job": snapshot})
            return
        if snapshot["status"] != "succeeded" or not output_path:
            self._json(409, {"detail": "Video is not ready."})
            return
        file_path = Path(output_path).resolve()
        if not file_path.is_relative_to(OUTPUT_DIR.resolve()) or not file_path.is_file():
            self._json(404, {"detail": "Video file is unavailable."})
            return
        size = file_path.stat().st_size
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="trafficlift-{job_id[:8]}.mp4"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with file_path.open("rb") as video:
            while chunk := video.read(256 * 1024):
                self.wfile.write(chunk)

    def do_POST(self) -> None:  # noqa: N802
        global ACTIVE_JOB
        if not self._origin_ok():
            self.send_error(403, "Origin is not allowed")
            return
        if not self._authorized():
            self._json(401, {"detail": "Pairing code is missing or incorrect."})
            return
        if urllib.parse.urlsplit(self.path).path != "/api/jobs":
            self._json(404, {"detail": "Route not found."})
            return
        try:
            data = self._read_json()
            prompt = str(data.get("prompt", "")).strip()
            image_url = str(data.get("image_url", "")).strip()
            frames = int(data.get("frames", 49))
            if not prompt or len(prompt) > MAX_PROMPT_CHARS:
                raise ValueError("Prompt must contain 1 to 7000 characters.")
            if frames not in (49, 97, 121, 145):
                raise ValueError("Choose a supported 2, 4, 5, or 6 second duration.")
            _validate_public_https_url(image_url)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"detail": str(exc)})
            return
        with ACTIVE_LOCK:
            if ACTIVE_JOB is not None:
                self._json(409, {"detail": "A local video is already running. Wait for it to finish."})
                return
            job_id = str(uuid.uuid4())
            ACTIVE_JOB = job_id
        now = time.time()
        job = Job(job_id, "queued", now, now, prompt, frames, "Job queued.")
        with JOBS_LOCK:
            JOBS[job_id] = job
        threading.Thread(target=_run_job, args=(job_id, image_url), daemon=True).start()
        self._json(202, {"success": True, "job": _job_snapshot(job)})


def main() -> int:
    required = (PYTHON, LTX_REPO, CONFIG, RUNNER)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("TrafficLift local video setup is incomplete:")
        for path in missing:
            print("  Missing: " + path)
        return 2
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    server.daemon_threads = True
    print("TrafficLift local video bridge is ready.")
    print(f"Address: http://{HOST}:{PORT}")
    print("Pairing code (enter it in TrafficLift Pro; do not share it):")
    print(TOKEN)
    print("This window must remain open while local video generation is in use.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping local video bridge...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
