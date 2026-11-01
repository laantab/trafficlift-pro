"""Opt-in HeyGen Video provider. Does not affect TrafficLift's existing renderer.

Paid calls are disabled unless both HEYGEN_API_KEY and
TRAFFICLIFT_HEYGEN_VIDEO_ENABLED=1 are configured server-side.
Only the authenticated video owner may use these endpoints.
"""
import os
import re
import requests
import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.photo_video_api import video_guard, prepare_video_plan, VideoRequest
from backend.sales_script import marketing_readiness_gate

router = APIRouter()
BASE = "https://api.heygen.com/v3/models/videos"


def configured():
    return os.getenv("TRAFFICLIFT_HEYGEN_VIDEO_ENABLED") == "1" and bool(os.getenv("HEYGEN_API_KEY"))


def headers():
    return {"x-api-key": os.environ["HEYGEN_API_KEY"], "Content-Type": "application/json"}


def provider_request(method, url, **kwargs):
    try:
        request_headers = headers()
        request_headers.update(kwargs.pop("extra_headers", {}))
        response = requests.request(method, url, headers=request_headers, timeout=(8, 35), **kwargs)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        # Avoid leaking provider response bodies, tokens or seller/customer data.
        raise HTTPException(502, "HeyGen provider request failed. Check API access and credits.") from exc
    except ValueError as exc:
        raise HTTPException(502, "HeyGen returned an unreadable response.") from exc
    if not isinstance(data, dict):
        raise HTTPException(502, "HeyGen returned an unexpected response.")
    return data


def _spoken_identity(name):
    """Remove retailer suffixes and catalog variants without losing model IDs."""
    from backend.sales_script import is_model_identifier
    raw = re.sub(r"\\s+[-–]\\s+(?:[\\w-]+\\.)?(?:com|net|org)\\s*$", "", str(name)).strip()
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    base = ", ".join(parts[:2]) if len(parts) > 1 else raw
    # Keep exact model codes even if the seller title puts them after color/size.
    models = [token for token in re.findall(r"[A-Za-z0-9-]+", raw) if is_model_identifier(token)]
    for model in models:
        if not re.search(r"\\b" + re.escape(model) + r"\\b", base, re.I):
            base += " " + model
    return base.strip(" ,")


def compose_heygen_narration(name, plan, max_words=34):
    """Create one short, fact-grounded ad with one CTA; never cut sentences.

    The marketing gate validates listing evidence before this is called. We
    choose complete facts from that evidence rather than truncating the older
    30-60 second multi-scene speech into a broken 15-second voiceover.
    """
    from backend.sales_script import clean, is_distinct_product_fact
    identity = _spoken_identity(name)
    opening = "Take a look at " + identity.rstrip(".!?") + "."
    cta = "Tap the product link to check current price, size, and options."
    if not identity:
        raise HTTPException(422, "A specific product name is required for the ad.")
    facts = []
    seen = []
    for item in plan.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        try:
            fact = clean(item.get("text") or "", 160).strip(" .!?")
        except ValueError:
            continue
        if not is_distinct_product_fact(fact, name):
            continue
        tokens = set(re.findall(r"[a-z0-9]+", fact.casefold()))
        if not tokens or any(tokens == older for older in seen):
            continue
        seen.append(tokens)
        # A complete fact is required: never clip at the word limit and
        # accidentally change what the seller actually claimed.
        facts.append(fact[0].upper() + fact[1:] + ".")
    budget = max_words - len((opening + " " + cta).split())
    if budget < 5:
        raise HTTPException(422, "Product title is too long for a 15-second narrated ad.")
    chosen = []
    # Prefer a concise fact; optionally add one different fact if it fits.
    for fact in sorted(facts, key=lambda f: len(f.split())):
        n = len(fact.split())
        if n <= budget and len(chosen) < 2:
            chosen.append(fact)
            budget -= n
    if not chosen:
        raise HTTPException(
            422, "The verified seller facts are too long for natural 15-second narration. "
                 "Supply a shorter, complete product fact; no paid video was started.")
    narration = " ".join([opening, *chosen, cta])
    words = narration.split()
    if len(words) > max_words or narration.casefold().count("tap the product link") != 1:
        raise HTTPException(422, "Narration failed the length or call-to-action review.")
    return narration


def product_prompt(name, plan):
    narration = compose_heygen_narration(name, plan)
    return (
        "Vertical product advertisement. Use the supplied exact seller product photo "
        "as the opening frame; preserve product identity, design, markings and proportions. "
        "Stable camera, subtle gentle movement, no rapid cuts, no strobing or flicker. "
        "No invented features, labels, brands, badges, or on-screen text. "
        "Natural US English narration, warm professional delivery with a short pause "
        "between sentences, moderate pace, no repeated words or background music. "
        "Speak exactly the following once, including the final call to action: " + narration
    )

class HeyGenSubmit(BaseModel):
    product: VideoRequest
    duration: int = Field(default=15, ge=5, le=15)
    # Explicit opt-in per call; caller cannot accidentally incur provider charges.
    approve_paid_generation: bool = False


@router.get("/api/v1/heygen-video/readiness")
def heygen_readiness(request: Request):
    video_guard(request)
    return {"provider": "heygen-video-1", "enabled": configured(),
            "paid_generation_requires_approval": True,
            "note": "Your ChatGPT HeyGen connection is not the TrafficLift API key."}


@router.get("/api/v1/heygen-video/verify-key")
def heygen_verify_key(request: Request):
    """Non-billable key check. Never expose the API key or account profile."""
    video_guard(request)
    if not os.getenv("HEYGEN_API_KEY"):
        raise HTTPException(503, "HeyGen API key is not configured.")
    provider_request("GET", "https://api.heygen.com/v1/user/me")
    return {"provider": "heygen", "authenticated": True,
            "paid_generation_started": False}


@router.post("/api/v1/heygen-video/submit", status_code=202)
def heygen_submit(body: HeyGenSubmit, request: Request):
    video_guard(request)
    if not configured():
        raise HTTPException(503, "HeyGen integration needs a server-side API key and explicit enablement.")
    if not body.approve_paid_generation:
        raise HTTPException(409, "Paid HeyGen generation requires explicit approval.")
    product = body.product
    try:
        plan, _ = prepare_video_plan(product, require_renderable=True)
        marketing_readiness_gate(plan)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Image-to-video inherits the input image aspect ratio; vertical output requires
    # a genuine vertical source image. The UI must confirm format before using it.
    # The existing VideoRequest URL validator requires a public HTTPS listing image.
    # Mode image_to_video is deliberate: no unsupported text-only product lookalikes.
    payload = {
        "model": "heygen-video-1",
        "mode": "image_to_video",
        "prompt": product_prompt(product.name, plan),
        "duration": body.duration,
        "image": {"type": "url", "url": product.image_url},
        "resolution": "768p",
        "prompt_enhancement": "quality",
    }
    result = provider_request("POST", BASE, json=payload,
                              extra_headers={"Idempotency-Key": uuid.uuid4().hex})
    video_id = result.get("video_id") or (result.get("data") or {}).get("video_id")
    if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", video_id):
        raise HTTPException(502, "HeyGen did not return a valid job ID.")
    return {"provider": "heygen", "video_id": video_id, "status": "submitted",
            "message": "Paid generation submitted; inspect the completed video before publishing."}


@router.get("/api/v1/heygen-video/status/{video_id}")
def heygen_status(video_id: str, request: Request):
    video_guard(request)
    if not configured():
        raise HTTPException(503, "HeyGen integration is disabled.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", video_id):
        raise HTTPException(404, "Unknown HeyGen job.")
    result = provider_request("GET", BASE + "/" + video_id)
    status = result.get("status") or (result.get("data") or {}).get("status") or "unknown"
    video_url = result.get("video_url") or (result.get("data") or {}).get("video_url")
    # Do not return untrusted non-HTTPS destinations.
    if not isinstance(video_url, str) or not video_url.startswith("https://"):
        video_url = None
    return {"provider": "heygen", "video_id": video_id, "status": status,
            "video_url": video_url if status == "completed" else None,
            "quality_review_required": True}
