"""Owner-only LongCat Distilled image-to-video trial via fal.ai.
Disabled by default. One 162-frame silent clip per approved request; no retries.
Schema and pricing checked 2026-11-04:
https://fal.ai/models/fal-ai/longcat-video/distilled/image-to-video/720p/api
https://fal.ai/models/fal-ai/longcat-video/distilled/image-to-video/720p
"""
import os
import re
import fal_client
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from backend.photo_video_api import video_guard, prepare_video_plan, VideoRequest
from backend.heygen_video_api import verified_listing_image
from backend.sales_script import marketing_readiness_gate
from backend.wan_video_api import wan_prompt

router = APIRouter()
MODEL = "fal-ai/longcat-video/distilled/image-to-video/720p"
PRICE_USD = 0.054  # 162 / 30 seconds at $0.01/second; estimate, not a billing cap.


def configured():
    return (os.getenv("TRAFFICLIFT_LONGCAT_VIDEO_ENABLED") == "1"
            and bool(os.getenv("FAL_KEY")))


class LongCatSubmit(BaseModel):
    product: VideoRequest
    approve_paid_generation: bool = False


@router.get("/api/v1/longcat-video/readiness")
def readiness(request: Request):
    video_guard(request)
    return {"provider": "longcat-distilled-720p-fal", "enabled": configured(),
            "estimated_usd_per_clip": PRICE_USD, "duration_seconds": 162 / 30, "fps": 30, "resolution": "720p",
            "audio": False, "paid_generation_requires_approval": True,
            "mode": "visual_trial_only"}


@router.post("/api/v1/longcat-video/submit", status_code=202)
def submit(body: LongCatSubmit, request: Request):
    video_guard(request)
    if not configured():
        raise HTTPException(503, "LongCat Distilled is not connected. Configure FAL_KEY and set TRAFFICLIFT_LONGCAT_VIDEO_ENABLED=1 before paying.")
    if not body.approve_paid_generation:
        raise HTTPException(409, "Explicit approval is required for the $0.054 estimated LongCat trial.")
    try:
        plan, photos = prepare_video_plan(body.product, require_renderable=True)
        marketing_readiness_gate(plan)
        image_url = verified_listing_image(photos)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # LongCat derives framing from the source image; do not promise 9:16.
    # The documented default is 162 frames at 30 fps: 5.4 seconds.
    payload = {"image_url": image_url, "prompt": wan_prompt(body.product.name, plan),
               "num_frames": 162, "fps": 30,
               "enable_safety_checker": True, "enable_prompt_expansion": False,
               "video_output_type": "X264 (.mp4)"}
    # Use the official SDK for endpoint-path queue routing and status handling.
    # Avoid hand-assembling nested fal queue URLs (which produced HTTP 405).
    try:
        handle = fal_client.submit(MODEL, arguments=payload)
    except fal_client.FalClientHTTPError as exc:
        code = getattr(exc, "status_code", None)
        reason = ("billing or credits" if code == 402 else
                  "invalid input" if code == 422 else
                  "credentials or permissions" if code in (401, 403) else
                  "model routing or method" if code == 405 else
                  "provider or network availability")
        raise HTTPException(502, "fal.ai rejected the LongCat trial: " + reason +
                            " (HTTP " + str(code) + "). No automatic retry was started.") from exc
    except Exception as exc:
        raise HTTPException(502, "LongCat provider could not accept the request. Check fal.ai history before retrying.") from exc
    request_id = handle.request_id
    if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{20,64}", request_id):
        raise HTTPException(502, "LongCat submission status is uncertain. Check fal.ai jobs; do not resubmit.")
    return {"provider": "longcat", "request_id": request_id, "status": "submitted",
            "estimated_usd": PRICE_USD, "visual_review_required": True}


@router.get("/api/v1/longcat-video/status/{request_id}")
def status(request_id: str, request: Request):
    video_guard(request)
    if not configured():
        raise HTTPException(503, "LongCat Distilled trial is disabled.")
    if not re.fullmatch(r"[0-9a-fA-F-]{20,64}", request_id):
        raise HTTPException(404, "Unknown LongCat job.")
    # The SDK stores the canonical queue endpoint for nested model paths.
    try:
        handle = fal_client.SyncClient().get_handle(MODEL, request_id)
        provider_state = handle.status()
    except Exception as exc:
        raise HTTPException(502, "Unable to check existing LongCat job. Do not start another paid request.") from exc
    if isinstance(provider_state, fal_client.Queued):
        return {"provider": "longcat", "status": "in_queue", "video_url": None}
    if isinstance(provider_state, fal_client.InProgress):
        return {"provider": "longcat", "status": "in_progress", "video_url": None}
    if not isinstance(provider_state, fal_client.Completed):
        return {"provider": "longcat", "status": "unknown", "video_url": None}
    if provider_state.error:
        return {"provider": "longcat", "status": "failed", "video_url": None}
    try:
        result = handle.get()
    except Exception as exc:
        raise HTTPException(502, "LongCat completed but its result could not be retrieved. Check provider jobs.") from exc
    video = result.get("video") if isinstance(result, dict) else None
    video_url = video.get("url") if isinstance(video, dict) else None
    if not isinstance(video_url, str) or not video_url.startswith("https://"):
        return {"provider": "longcat", "status": "failed", "video_url": None}
    return {"provider": "longcat", "status": "completed", "video_url": video_url,
            "visual_review_required": True, "audio": False}
