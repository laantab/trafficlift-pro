"""Opt-in Krea image-to-video API; isolated from HeyGen and Pinterest.

No API request is made without owner authorization, server configuration, a
confirmed video-only image and explicit per-request paid approval.
"""
import os
import re
import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from backend.photo_video_api import video_guard
from local_video.trafficlift_video_bridge import _validate_public_https_url

router = APIRouter()
API = "https://api.krea.ai"
MODEL = "seedance-2-fast"
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{6,128}$")


class KreaSubmit(BaseModel):
    image_url: str = Field(min_length=12, max_length=2048)
    confirm_video_photo: bool = False
    prompt: str = Field(min_length=20, max_length=5000)
    duration: int = Field(default=5, ge=4, le=15)
    resolution: str = "720p"
    aspect_ratio: str = "9:16"
    approve_paid_generation: bool = False


def configured():
    return os.getenv("TRAFFICLIFT_KREA_VIDEO_ENABLED") == "1" and bool(os.getenv("KREA_API_KEY"))


def provider_request(method, path, **kwargs):
    """Do not expose tokens/provider bodies or automatically retry paid POSTs."""
    try:
        r = requests.request(method, API + path, headers={
            "Authorization": "Bearer " + os.environ["KREA_API_KEY"],
            "Content-Type": "application/json",
        }, timeout=(8, 35), **kwargs)
        r.raise_for_status()
        result = r.json()
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        code = {400: 422, 401: 503, 402: 402, 403: 403, 422: 422, 429: 429}.get(status, 502)
        raise HTTPException(code, "Krea API rejected request (HTTP %s). Check API key, balance or settings; do not automatically retry." % (status or "unknown")) from exc
    except requests.Timeout as exc:
        raise HTTPException(504, "Krea timed out. A paid job may exist; check Krea jobs before resubmitting.") from exc
    except requests.RequestException as exc:
        raise HTTPException(502, "Unable to reach Krea; no automatic retry.") from exc
    except ValueError as exc:
        raise HTTPException(502, "Krea returned an unreadable response.") from exc
    if not isinstance(result, dict):
        raise HTTPException(502, "Unexpected Krea API response.")
    return result


@router.get("/api/v1/krea-video/readiness")
def readiness(request: Request):
    video_guard(request)
    return {"provider": "krea", "enabled": configured(),
            "model": MODEL, "paid_generation_requires_approval": True,
            "api_balance_separate_from_subscription": True}


@router.post("/api/v1/krea-video/submit", status_code=202)
def submit(body: KreaSubmit, request: Request):
    video_guard(request)
    if not configured():
        raise HTTPException(503, "Krea is disabled until the server API key and enable flag are configured.")
    if not body.approve_paid_generation:
        raise HTTPException(409, "Explicit approval is required for each paid Krea generation.")
    if not body.confirm_video_photo:
        raise HTTPException(422, "Confirm that the separate Video Studio photo depicts the exact product.")
    if body.resolution not in ("480p", "720p"):
        raise HTTPException(422, "Krea Fast supports 480p or 720p in this integration.")
    if body.aspect_ratio not in ("9:16", "1:1", "16:9", "3:4", "4:3"):
        raise HTTPException(422, "Unsupported Krea video aspect ratio.")
    try:
        _validate_public_https_url(body.image_url)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Provide an accessible public HTTPS video image URL; no paid video was started.") from exc
    payload = {
        "prompt": body.prompt + " Preserve the exact photographed product. No warping, melting, object clipping, invented text or URL.",
        "start_image": body.image_url,
        "duration": body.duration,
        "resolution": body.resolution,
        "aspect_ratio": body.aspect_ratio,
        "generate_audio": False,
    }
    result = provider_request("POST", "/generate/video/bytedance/" + MODEL, json=payload)
    job_id = result.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise HTTPException(502, "Krea returned no valid job ID; check jobs before attempting another paid request.")
    return {"provider": "krea", "job_id": job_id, "status": "submitted",
            "quality_review_required": True}


@router.get("/api/v1/krea-video/status/{job_id}")
def status(job_id: str, request: Request):
    video_guard(request)
    if not configured():
        raise HTTPException(503, "Krea integration is disabled.")
    if not JOB_ID.fullmatch(job_id):
        raise HTTPException(404, "Invalid Krea job ID.")
    job = provider_request("GET", "/jobs/" + job_id)
    if job.get("job_id") != job_id:
        raise HTTPException(502, "Krea returned a different job.")
    state = job.get("status", "unknown")
    urls = (job.get("result") or {}).get("urls") or []
    url = urls[0] if state == "completed" and isinstance(urls, list) and urls else None
    if not isinstance(url, str) or not url.startswith("https://"):
        url = None
    return {"provider": "krea", "job_id": job_id, "status": state,
            "video_url": url, "quality_review_required": True}
