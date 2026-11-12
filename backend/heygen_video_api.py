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
    """Create a short spoken identity without changing the stored product title.

    Amazon titles often append long keyword strings that cannot fit a 15-second
    ad. Prefer the first meaningful catalog phrase, but preserve genuine model
    identifiers wherever they appear in the full title.
    """
    from backend.sales_script import is_model_identifier
    raw = re.sub(r"\\s+[-–]\\s+(?:[\\w-]+\\.)?(?:com|net|org)\\s*$", "", str(name)).strip()
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) > 1:
        # "Smart+, Desk Drawer Organizer" needs the second phrase, while
        # "Generic Silicone Baking Mat, Food Grade ..." is already specific.
        first_words = re.findall(r"[A-Za-z0-9+&'-]+", parts[0])
        base = parts[0] if len(first_words) >= 3 else ", ".join(parts[:2])
    else:
        base = raw

    # Long no-comma catalog titles still need a compact voice identity.
    words = base.split()
    if len(words) > 9:
        words = words[:9]
        while words and words[-1].casefold().strip(".,;:") in {
            "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with"
        }:
            words.pop()
        base = " ".join(words)

    # Keep exact model codes even if the seller title puts them after color/size.
    models = [token for token in re.findall(r"[A-Za-z0-9-]+", raw) if is_model_identifier(token)]
    for model in models:
        if not re.search(r"\\b" + re.escape(model) + r"\\b", base, re.I):
            base += " " + model
    return base.strip(" ,")


def _natural_product_reference(identity):
    """Make the spoken product reference conversational without changing the record."""
    value = str(identity or "").strip()
    value = re.sub(r"^(?:generic)\s+", "", value, flags=re.I)
    low = value.casefold()
    if "silicone" in low and "mat" in low and ("baking" in low or "bake" in low):
        return "silicone baking mat"
    return value or str(identity or "").strip()


def _supported_problem_hook(name, identity, plan):
    """Return a problem/emotion/solution hook only when seller text supports it.

    Evidence is limited to exact-listing facts plus a small whitelist of explicit
    attributes carried in the verified seller title. We do not infer broad
    performance promises from a photograph or generic product category.
    """
    evidence = " ".join(
        str(item.get("text") or "")
        for item in (plan.get("evidence") or [])
        if isinstance(item, dict)
    )
    source = " ".join([str(name or ""), evidence]).casefold()
    product = _natural_product_reference(identity)

    if (re.search(r"\bnon[ -]?stick\b", source)
            and re.search(r"\b(?:baking|bake|pastry|dough|silicone|mat|pan|sheet)\b", source)):
        return (
            "Tired of scraping your baking pan after every batch?",
            "Kiss those days goodbye.",
            f"Take a look at this {product}.",
        )

    if (re.search(r"\b(?:organizer|storage|divider|drawer)\b", source)
            and re.search(r"\b(?:adjustable|expandable|removable|storage|divider)\b", source)):
        return (
            "Tired of clutter taking over your space?",
            "Get things back under control.",
            f"Take a look at this {product}.",
        )

    if re.search(r"\b(?:wireless|cordless)\b", source):
        return (
            "Tired of cords getting in the way?",
            "Cut the clutter.",
            f"Take a look at this {product}.",
        )

    if re.search(r"\b(?:dishwasher safe|easy to clean)\b", source):
        return (
            "Tired of cleanup taking longer than the job itself?",
            "Make cleanup easier.",
            f"Take a look at this {product}.",
        )

    return None


def _short_seller_facts(text, *, max_words=11):
    """Return intact, source-backed clauses; never chop words off a claim."""
    if not isinstance(text, str):
        return []
    # Catalog descriptions often contain multiple independent phrases.
    chunks = re.split(r"[.;!?]|\s+[—–]\s+|\s*\|\s*|,\s*", text)
    result = []
    for chunk in chunks:
        clause = re.sub(r"\s+", " ", chunk).strip(" -:,.")
        words = clause.split()
        if not 3 <= len(words) <= max_words:
            continue
        if re.search(r"\b(?:for|and|with|of|to|in|on|the|a|an|by|or)$", clause, re.I):
            continue
        if re.match(r"^(?:shop|buy|click|visit|see price)\b", clause, re.I):
            continue
        if len(clause) > 100 or re.search(r"https?://|www\.", clause, re.I):
            continue
        result.append(clause[0].upper() + clause[1:] + ".")
    return result


def compose_heygen_narration(name, plan, max_words=34):
    """Create a short, conversational, fact-grounded 15-second product ad."""
    from backend.sales_script import is_distinct_product_fact
    identity = _spoken_identity(name)
    if not identity:
        raise HTTPException(422, "A specific product name is required for the ad.")

    cta = "Tap the product link to learn more."
    hook = _supported_problem_hook(name, identity, plan)
    if hook:
        narration = " ".join([*hook, cta])
        if len(narration.split()) <= max_words:
            return narration

    facts = []
    seen = []
    for item in plan.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        source_text = item.get("text") or ""
        if not isinstance(source_text, str) or not source_text.strip():
            continue
        for fact in _short_seller_facts(source_text):
            if not is_distinct_product_fact(fact, name):
                continue
            tokens = set(re.findall(r"[a-z0-9]+", fact.casefold()))
            if tokens and not any(tokens == older for older in seen):
                seen.append(tokens)
                facts.append(fact)

    product = _natural_product_reference(identity)
    opening = f"Looking for a simpler solution? Take a look at this {product}."
    budget = max_words - len((opening + " " + cta).split())

    chosen = []
    for fact in sorted(facts, key=lambda value: len(value.split())):
        n = len(fact.split())
        if n <= budget and len(chosen) < 1:
            chosen.append(fact)
            budget -= n

    if not chosen and plan.get("mode") != "product_overview" and not (plan.get("evidence") or []):
        raise HTTPException(422, "Verified seller facts are required; no paid video was started.")

    narration = " ".join([opening, *chosen, cta])
    if len(narration.split()) > max_words or narration.casefold().count("tap the product link") != 1:
        raise HTTPException(422, "Narration failed the length or call-to-action review.")
    return narration


def verified_listing_image(photos, trusted_image=None):
    """Use exact-listing imagery, including a previously verified winner image.

    A browser-selected image is accepted only when the caller proves that
    Analyze Product verified its identity and that its source is the same seller
    listing now being rendered.
    """
    from local_video.trafficlift_video_bridge import _validate_public_https_url
    for photo in [*(photos or []), trusted_image]:
        if not isinstance(photo, str) or not photo:
            continue
        try:
            _validate_public_https_url(photo)
        except (ValueError, TypeError):
            continue
        return photo
    raise HTTPException(
        422,
        "Could not verify a product photo from the exact seller listing. "
        "Select a listing with an accessible product image; no paid video was started."
    )


def trusted_winner_image(product):
    """Return Analyze Product's verified image only for the same seller listing."""
    if not getattr(product, "product_identity_verified", False):
        return None
    source = str(getattr(product, "image_source", "") or "")
    destination = str(getattr(product, "product_url", "") or "")
    image = str(getattr(product, "image_url", "") or "")
    if not source or not destination or not image:
        return None
    from backend.product_facts import canonical
    from backend.product_url_finder_v2 import _asin_from_url
    try:
        if canonical(source) != canonical(destination):
            # Product-title URLs and canonical /dp/ URLs can describe the
            # exact same Amazon ASIN. Preserve identity only if BOTH URLs
            # individually carry that ASIN; no redirect/search inference.
            source_asin = _asin_from_url(source)
            destination_asin = _asin_from_url(destination)
            if not source_asin or source_asin != destination_asin:
                return None
    except Exception:
        return None
    from local_video.trafficlift_video_bridge import _validate_public_https_url
    try:
        _validate_public_https_url(image)
    except (ValueError, TypeError):
        return None
    return image


def exact_seller_photo_fallback(product):
    """One bounded, unpaid re-read of the user's exact Amazon ASIN listing.

    Discovery photos can be reachable without being proven to belong to the
    seller listing. Never approve those on image-host checks alone. An Amazon
    ASIN embedded in the destination anchors the single listing to inspect.
    """
    from backend.product_url_finder_v2 import _asin_from_url, resolve_product_identity
    from local_video.trafficlift_video_bridge import _validate_public_https_url
    destination = str(getattr(product, "product_url", "") or "")
    expected_asin = _asin_from_url(destination)
    if not expected_asin:
        return None
    try:
        record = resolve_product_identity(destination)
        if record.get("asin") != expected_asin:
            return None
        image = str(record.get("image_url") or "")
        if not image:
            return None
        _validate_public_https_url(image)
        return image
    except Exception:
        # A failed seller lookup must never start a paid generation.
        return None


def product_prompt(name, plan):
    narration = compose_heygen_narration(name, plan)
    lower_name = str(name or "").casefold()
    flexible_mat_rule = ""
    if (("mat" in lower_name or "sheet" in lower_name)
            and any(word in lower_name for word in ("silicone", "baking", "pastry"))):
        flexible_mat_rule = (
            "BAKING MAT DEMONSTRATION SEQUENCE: Show how the product works, not just before/after states. "
            "Begin with cooked chicken resting naturally on the flat silicone mat. Keep the mat fully flat "
            "on the counter or baking tray. In one continuous, clearly visible removal shot, use an appropriate "
            "food-safe utensil such as a wide spatula or tongs to lift one piece of cooked chicken upward and "
            "completely clear of the mat. The chicken must stay intact and separate from the mat, and the mat "
            "must stay flat and stationary during the lift. Show a small visible gap between the chicken and mat "
            "after release, then move the chicken to a plate or serving area. Do NOT jump directly from food-on-mat "
            "to an empty mat; the viewer must see the actual removal action. "
            "FLEXIBLE MAT CONTACT LOCK: Every baked item must remain a solid, separate object resting on top of "
            "the mat until deliberately lifted away. Never lift, fold, bend, curl, or pull the mat through food. "
            "No food may clip through, sink into, merge with, melt through, teleport through, or disappear behind "
            "the mat. Only after the food-removal demonstration is complete may there be a clean cut to the same "
            "mat EMPTY; then the empty mat may be lifted or flexed. Never show food floating unsupported. "
        )
    return (
        "Vertical product advertisement. Use the supplied exact seller product photo "
        "as the opening frame; preserve product identity, design, markings and proportions. "
        "PRODUCT IDENTITY LOCK: Preserve the exact organizer design, proportions "
        "and identifying features across scenes; never invent a different model, "
        "feature, label, brand, or product mechanic. "
        "SCENE SEPARATION: The provided seller product/packaging photograph is "
        "REFERENCE INPUT and may appear only in the initial 0-2 second opening "
        "hero shot. Then make a clean, complete CUT to a full-frame product "
        "demonstration for the middle of the ad. No crossfade of the box into "
        "the action and NO packaging photo visible over the demonstration. "
        "Do not composite, overlay, float, ghost, pin, picture-in-picture, "
        "or paste the still photograph onto moving footage. Do not keep the "
        "packaging stationary while the background moves. "
        "During the demonstration show the complete actual product, not its "
        "retail box, and keep the organizer and hands unobstructed. "
        "PRODUCTION CAMERA LOCK: tripod-stable camera, locked horizon, fixed "
        "perspective, no handheld shake, wobble, frame jitter, micro-zoom, "
        "camera drift, rolling-shutter warping, or fluctuating scale. "
        "All movement must be intentional, slow, physically plausible, and "
        "confined to an authentic product interaction. If an accurate "
        "demonstration cannot be preserved, use the static product hero image "
        "with stable lighting rather than fabricating product mechanics. "
        "PHYSICAL CONTACT LOCK: Keep every object solid and spatially separate. "
        "No object may pass through, clip into, merge with, melt into, teleport through, "
        "or disappear through another object. Maintain continuous believable support, "
        "gravity, contact, and occlusion from frame to frame. "
        + flexible_mat_rule +
        "PACKAGING TEXT LOCK: preserve existing printed lettering exactly "
        "as photographed, without re-drawing, translating, inventing, "
        "morphing, blurring, or animating individual letters. "
        "Avoid close-up shots of tiny printed packaging text; display the "
        "complete packaging in a steady, well-lit hero frame instead. "
        "Do not claim small package print is legible when its source is not. "
        "No aggressive zoom, panning off-frame, cutaways, "
        "lifestyle substitution, rapid cuts, no strobing or flicker. "
        "For the final call to action, continue a clean full-frame view of "
        "the demonstrated product; do not bring back the large retail package "
        "or obscure the product with a still-photo overlay. "
        "At the end show one crisp, high-contrast, readable call to action: "
        "\'VISIT PRODUCT LINK\'. Keep it away from the product silhouette; "
        "no other new text; no invented features, labels, brands, or badges. "
        "Natural US English narration, warm professional delivery with a short pause "
        "between sentences, moderate pace, no repeated words or background music. "
        "Speak exactly the following once, including the final call to action: " + narration
    )

class HeyGenSubmit(BaseModel):
    product: VideoRequest
    duration: int = Field(default=15, ge=5, le=15)
    # Explicit opt-in per call; caller cannot accidentally incur provider charges.
    approve_paid_generation: bool = False
    # Video Studio owns this image. Pinterest still uses winner.image_url.
    video_photo_url: str = Field(default="", max_length=2048)
    confirm_video_photo: bool = False


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
        plan, listing_photos = prepare_video_plan(product, require_renderable=True)
        marketing_readiness_gate(plan)
        # Video Studio is deliberately independent from Pinterest's image.
        # The authenticated owner must explicitly confirm any manually
        # selected video photograph as depicting this exact product.
        # No seller-listing claim is made for owner-confirmed photos.
        if body.video_photo_url:
            if not body.confirm_video_photo:
                raise HTTPException(422, "Confirm that the Video Studio photo shows this exact product; no paid video was started.")
            from local_video.trafficlift_video_bridge import _validate_public_https_url
            try:
                _validate_public_https_url(body.video_photo_url)
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, "The independent video photo must be an accessible public HTTPS image URL; no paid video was started.") from exc
            listing_image = body.video_photo_url
        else:
            # Legacy requests retain the strict exact-listing gate and its
            # safe zero-paid-call failure behavior.
            trusted_photo = trusted_winner_image(product)
            if not listing_photos and not trusted_photo:
                trusted_photo = exact_seller_photo_fallback(product)
            listing_image = verified_listing_image(
                listing_photos,
                trusted_image=trusted_photo,
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Image-to-video inherits the input image aspect ratio; vertical output requires
    # a genuine vertical source image. The UI must confirm format before using it.
    # The existing VideoRequest URL validator requires a public HTTPS listing image.
    # Mode image_to_video is deliberate: no unsupported text-only product lookalikes.
    payload = {
        "model": "heygen-video-1",
        "mode": "image_to_video",
        "prompt": product_prompt(plan.get("product") or product.name, plan),
        "duration": body.duration,
        "image": {"type": "url", "url": listing_image},
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
