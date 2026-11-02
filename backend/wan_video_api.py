"""Disabled-by-default, owner-only Wan 2.2 5B trial via fal.ai.
One five-second, silent image-to-video clip per explicit paid approval.
No retries, no automatic multiple-scene spending, no customer release yet.
"""
import os
import re
import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.photo_video_api import video_guard, prepare_video_plan, VideoRequest
from backend.heygen_video_api import verified_listing_image
from backend.sales_script import marketing_readiness_gate

router = APIRouter()
BASE = "https://queue.fal.run/fal-ai/wan/v2.2-5b/image-to-video"
PRICE_USD = 0.15  # Published fal.ai price per 5-second clip; estimate only.


def configured():
    return (os.getenv("TRAFFICLIFT_WAN_VIDEO_ENABLED") == "1"
            and bool(os.getenv("FAL_KEY")))


def provider_request(method, url, **kwargs):
    try:
        response = requests.request(
            method, url,
            headers={"Authorization": "Key " + os.environ["FAL_KEY"],
                     "Content-Type": "application/json"},
            timeout=(8, 35), allow_redirects=False, **kwargs)
        # Never include provider response bodies: they can contain user prompts,
        # seller data, signed URLs and potentially credentials.
        code = response.status_code
        if code >= 400:
            if code in (401, 403):
                raise HTTPException(502, "fal.ai rejected the API key or account permissions (provider HTTP " + str(code) + "). Check FAL_KEY in Render.")
            if code in (402, 422):
                detail = ("fal.ai reports insufficient credits or billing access."
                          if code == 402 else
                          "fal.ai rejected the video input. The seller photo may be inaccessible or a video parameter invalid.")
                raise HTTPException(502, detail + " Provider HTTP " + str(code) + ". No automatic retry was started.")
            if code == 429:
                raise HTTPException(502, "fal.ai rate limit reached (provider HTTP 429). Do not retry immediately.")
            if 500 <= code <= 599:
                raise HTTPException(502, "fal.ai is temporarily unavailable (provider HTTP " + str(code) + "). Check fal.ai job history before retrying.")
            raise HTTPException(502, "fal.ai rejected the request (provider HTTP " + str(code) + "). No automatic retry was started.")
        data = response.json()
    except requests.RequestException as exc:
        raise HTTPException(502, "Cannot reach fal.ai; provider response was not received. Check network and fal.ai status before retrying.") from exc
    except ValueError as exc:
        raise HTTPException(502, "fal.ai returned a response that could not be read. Check provider jobs before retrying.") from exc
    if not isinstance(data, dict):
        raise HTTPException(502, "Wan provider returned an unexpected response.")
    return data


def wan_prompt(name, plan):
    # Visual-only test: voiceover and multi-scene assembly are NOT yet offered.
    facts = [str(x.get("text", "")).strip() for x in (plan.get("evidence") or [])
             if isinstance(x, dict) and x.get("text")]
    detail = (" Seller-verified visual features: " + "; ".join(facts[:2]) + ".") if facts else ""
    return (
        "Commercial product close-up of the exact " + name + " shown in the reference photograph. "
        "Preserve exact product silhouette, finish, controls, proportions, packaging and markings. "
        "Keep the entire real product centered with generous safe margins through all frames. "
        "Tasteful, realistic subtle studio camera motion and lighting. "
        "No product replacement, invented operation, morphing, hands, actors, "
        "fabricated text, cutaways, or cropped product. "
        "Show only the true product as photographed." + detail
    )


class WanSubmit(BaseModel):
    product: VideoRequest
    approve_paid_generation: bool = False


@router.get("/api/v1/wan-video/readiness")
def readiness(request: Request):
    video_guard(request)
    return {"provider": "wan-2.2-5b-fal", "enabled": configured(),
            "estimated_usd_per_clip": PRICE_USD, "duration_seconds": 5,
            "audio": False, "paid_generation_requires_approval": True,
            "mode": "visual_trial_only"}


@router.post("/api/v1/wan-video/submit", status_code=202)
def submit(body: WanSubmit, request: Request):
    video_guard(request)
    if not configured():
        raise HTTPException(503, "Wan 2.2 is not connected. Configure FAL_KEY and enable its trial before paying.")
    if not body.approve_paid_generation:
        raise HTTPException(409, "Explicit approval is required for the $0.15 estimated Wan trial.")
    try:
        plan, photos = prepare_video_plan(body.product, require_renderable=True)
        marketing_readiness_gate(plan)
        image_url = verified_listing_image(photos)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Explicitly choose 9:16, with product-lock framing prompt. Center cropping
    # is possible: outputs still require visual review before publishing.
    payload = {"image_url": image_url, "prompt": wan_prompt(body.product.name, plan),
               "num_frames": 121, "frames_per_second": 24,
               "resolution": "720p", "aspect_ratio": "9:16",
               "enable_safety_checker": True, "enable_prompt_expansion": False}
    data = provider_request("POST", BASE, json=payload)
    request_id = data.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{20,64}", request_id):
        raise HTTPException(502, "Wan submission status is uncertain. Check fal.ai jobs; do not resubmit.")
    return {"provider": "wan", "request_id": request_id, "status": "submitted",
            "estimated_usd": PRICE_USD, "visual_review_required": True}


@router.get("/api/v1/wan-video/status/{request_id}")
def status(request_id: str, request: Request):
    video_guard(request)
    if not configured():
        raise HTTPException(503, "Wan 2.2 trial is disabled.")
    if not re.fullmatch(r"[0-9a-fA-F-]{20,64}", request_id):
        raise HTTPException(404, "Unknown Wan job.")
    base = BASE + "/requests/" + request_id
    data = provider_request("GET", base + "/status")
    state = data.get("status", "UNKNOWN")
    if state != "COMPLETED":
        return {"provider": "wan", "status": state.lower(), "video_url": None}
    if data.get("error"):
        return {"provider": "wan", "status": "failed", "video_url": None}
    result = provider_request("GET", base)
    video_url = (result.get("video") or {}).get("url") if isinstance(result.get("video"), dict) else None
    if not isinstance(video_url, str) or not video_url.startswith("https://"):
        return {"provider": "wan", "status": "failed", "video_url": None}
    return {"provider": "wan", "status": "completed", "video_url": video_url,
            "visual_review_required": True, "audio": False}
