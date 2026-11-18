"""Video Editor-in-Chief: deterministic, zero-provider-credit preflight.

Checks what can be known *before* rendering. Generated motion, scene
consistency and spoken audio must be reviewed *after* the provider renders;
this module never calls any paid provider or claims visual certification.
"""
import re
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from backend.photo_video_api import video_guard

router = APIRouter()
_VALID_PROVIDERS = {"heygen", "krea", "free"}
_PLACEHOLDER = re.compile(
    r"\b(?:lorem ipsum|insert (?:url|link|product))\b", re.I
)
_RISKY_CLAIMS = re.compile(
    r"\b(?:guaranteed results?|100% guaranteed|cures? [a-z ]+|risk[- ]free profits?)\b", re.I
)


class PreflightRequest(BaseModel):
    provider: str
    product_name: str = Field(default="", max_length=200)
    image_url: str = Field(default="", max_length=2048)
    image_confirmed: bool = False
    duration: int = Field(default=15, ge=1, le=60)
    aspect_ratio: str = Field(default="9:16", max_length=8)
    script: str = Field(default="", max_length=7000)


def review_preflight(*, provider, product_name, image_url, image_confirmed,
                     duration, aspect_ratio, script=""):
    """A mandatory pre-render quality gate, never a claim of video inspection.

    Return explicit warnings and safe text corrections, or raise 422 before
    any reserved capacity or paid submission. Never silently replace photos.
    """
    provider = str(provider or "").strip().lower()
    name = re.sub(r"\s+", " ", str(product_name or "")).strip()
    url = str(image_url or "").strip()
    content = re.sub(r"\s+", " ", str(script or "")).strip()
    problems, warnings, corrections = [], [], []
    if provider not in _VALID_PROVIDERS:
        problems.append("Unsupported video engine.")
    if not name or len(name) < 3 or name.lower() in ("product", "trending product", "untitled product"):
        problems.append("Choose one specific named product before making a video.")
    if not image_confirmed:
        problems.append("Confirm that the video-only photograph shows the exact selected product.")
    u = urlsplit(url)
    if u.scheme.lower() != "https" or not u.hostname or u.username or u.password:
        problems.append("Provide a public HTTPS video-photo URL for the exact product.")
    if not (5 <= int(duration) <= 30):
        problems.append("Choose a short video duration of 5 to 30 seconds.")
    if aspect_ratio not in {"9:16", "1:1", "16:9", "3:4", "4:3"}:
        problems.append("Choose a supported video aspect ratio.")
    if not content:
        warnings.append("No script supplied; the selected video engine must produce grounded narration.")
    if _PLACEHOLDER.search(content):
        problems.append("Video instructions contain a placeholder or fake call to action.")
    if _RISKY_CLAIMS.search(content):
        problems.append("Video script contains an unsupported guarantee or medical claim.")
    if provider == "heygen" and aspect_ratio != "9:16":
        warnings.append("HeyGen image-to-video inherits image proportions; TrafficLift must export a separate 9:16 social version.")
    if provider == "krea" and aspect_ratio != "9:16":
        warnings.append("Use the 9:16 version for Reels, TikTok and Shorts.")
    if not problems and content and "exact" not in content.casefold() and provider in {"krea", "heygen"}:
        content += " Preserve the exact reference product across every scene. Never replace its color, lid, shape or printed labels."
        corrections.append("Added a product-identity instruction to prevent lookalike substitutions.")
    if problems:
        raise HTTPException(status_code=422, detail="Editor-in-Chief: " + " ".join(problems))
    return {
        "approved_for_render": True,
        "provider": provider,
        "product_name": name,
        "corrected_script": content,
        "automatic_corrections": corrections,
        "warnings": warnings,
        "review_after_render_required": [
            "Same exact product, lid, color and markings in every scene",
            "Natural, physically possible movement with no clipping or morphing",
            "Understandable narration and accurate claims",
            "Correct aspect ratio, readable captions and approved CTA",
        ],
        "no_provider_credits_used": True,
        "note": "Preflight cannot inspect footage that has not been rendered. Post-render identity and spoken-narration review are mandatory.",
    }


@router.post("/api/v1/video-editor-in-chief/preflight")
def preflight(body: PreflightRequest, request: Request):
    video_guard(request)
    return review_preflight(
        provider=body.provider, product_name=body.product_name,
        image_url=body.image_url, image_confirmed=body.image_confirmed,
        duration=body.duration, aspect_ratio=body.aspect_ratio,
        script=body.script,
    )
