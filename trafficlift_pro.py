"""
trafficlift_pro.py
TrafficLift Pro — Autonomous Traffic & Growth Engine  v2.0

FastAPI backend orchestrator:
  GET  /api/v1/health              → service health + AI mode status
 POST  /api/v1/traffic/generate     → scrape + generate + persist campaign
 GET   /api/v1/campaigns/history    → list saved campaigns (paginated)
 GET   /api/v1/campaigns/<id>       → retrieve a single campaign
DELETE /api/v1/campaigns/<id>       → delete a campaign
DELETE /api/v1/campaigns/clear     → clear all history

Run:
  python trafficlift_pro.py
  # or: uvicorn trafficlift_pro:app --host 0.0.0.0 --port 8000 --reload

Environment variables (optional):
  OPENAI_API_KEY    — GPT-4o for AI content generation
  OPENAI_MODEL      — model name (default: gpt-4o)
  MINIMAX_API_KEY   — MiniMax API key for video/avatar generation
  MINIMAX_GROUP_ID  — MiniMax Group ID
  HOST              — bind host (default: 0.0.0.0)
  PORT              — bind port (default: 8000)
  LOG_LEVEL         — DEBUG | INFO | WARNING (default: INFO)
"""

from __future__ import annotations

import os
import sys
import logging
import traceback
from datetime import datetime, timezone
from typing import Annotated, Optional

from dotenv import load_dotenv

# Load .env (if present) BEFORE importing anything that reads os.getenv.
# On Render / production, env vars come from the platform and .env is absent
# — load_dotenv() is a no-op in that case, so this is safe everywhere.
load_dotenv()

from fastapi import FastAPI, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

# ── Local imports ────────────────────────────────────────────────────────────
# Ensure the project root is on sys.path so `from backend.X import Y` works
# even when launched via `uvicorn trafficlift_pro:app` from a different cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.scrape import ProductScraper, ScrapedProduct
from backend.db import CampaignDB
from backend.generate import (
    TrafficLiftGenerator,
    CompiledPackage,
    PinterestSEOEngine,
    ShortFormVideoBlueprint,
    YouTubeShortsBlueprint,
    TwitterViralThread,
    PaidAdPackage,
    AdCopyVariations,
)
from backend.video import (
    MiniMaxVideoClient,
    MiniMaxVideoError,
    build_trafficlift_short_prompt,
)
from backend.product_scout import router as product_scout_router

# ── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("trafficlift_pro")

# ── App config ──────────────────────────────────────────────────────────────

APP_NAME = "TrafficLift Pro"
APP_VERSION = "2.0.0"
DESCRIPTION = (
    "Autonomous AI traffic engine for online entrepreneurs. "
    "Paste a product URL — get Pinterest SEO, TikTok scripts, YouTube Shorts, "
    "Twitter/X Threads, and paid ad copy in seconds. "
    "Powered by OpenAI GPT-4o and MiniMax with SQLite campaign history."
)

app = FastAPI(
    title=APP_NAME,
    description=DESCRIPTION,
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    contact={"name": APP_NAME, "url": "https://github.com/trafficlift/pro"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Singleton services ──────────────────────────────────────────────────────

scraper = ProductScraper()
generator = TrafficLiftGenerator()
db = CampaignDB()

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    """Payload for the generate endpoint."""

    input_url: Annotated[str, Field(
        description="Product or landing page URL.",
        min_length=7,
        max_length=2000,
    )]
    mode: Annotated[str, Field(
        description="Traffic strategy: 'organic' ($0) or 'paid' (ad scaling).",
        pattern="^(organic|paid)$",
    )]
    target_channels: Annotated[list[str], Field(
        description="Channels to generate assets for.",
        min_length=1,
    )]
    daily_budget: Annotated[float, Field(
        ge=5.0,
        le=10000.0,
        default=25.0,
    )]

    @field_validator("input_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    @field_validator("target_channels")
    @classmethod
    def _validate_channels(cls, v: list[str]) -> list[str]:
        valid = {
            # organic
            "pinterest", "tiktok_organic", "youtube_shorts", "twitter_threads",
            # paid
            "meta_ads", "tiktok_ads",
        }
        invalid = [ch for ch in v if ch not in valid]
        if invalid:
            raise ValueError(f"Unknown channels: {invalid}. Valid: {sorted(valid)}")
        return v


class MiniMaxVideoParamsRequest(BaseModel):
    """Request MiniMax video generation parameters."""
    input_url: Annotated[str, Field(min_length=7, max_length=2000)]
    platform: Annotated[str, Field(
        description="Target platform: 'tiktok' | 'youtube' | 'instagram' | 'meta'",
        default="tiktok",
    )]

    @field_validator("input_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


# ── Video rendering models (actual MiniMax t2v, not just params) ────────────

class VideoSubmitRequest(BaseModel):
    """Payload for POST /api/v1/video/generate (async submit + poll)."""
    prompt: Annotated[str, Field(
        description="Full video prompt (1-7000 chars). Drives both visuals and native audio on H3/H3-Max.",
        min_length=1, max_length=7000,
    )]
    model: Annotated[str, Field(default="MiniMax-H3")] = "MiniMax-H3"
    duration: Annotated[int, Field(ge=4, le=15)] = 15
    ratio: Annotated[str, Field(default="9:16")] = "9:16"
    resolution: Annotated[str, Field(default="768P")] = "768P"
    reference_image_url: Annotated[Optional[str], Field(default=None)] = None


class VideoRenderRequest(VideoSubmitRequest):
    """Payload for POST /api/v1/video/render (submit + block until done)."""
    poll_timeout: Annotated[int, Field(ge=30, le=3600, default=2400)] = 2400


# ─────────────────────────────────────────────────────────────────────────────
# Response Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pinterest_to_dict(p: PinterestSEOEngine) -> dict:
    return {
        "board_title": p.board_title,
        "pin_title": p.pin_title,
        "optimized_description": p.optimized_description,
        "recommended_keywords": p.recommended_keywords,
        "suggested_board_names": p.suggested_board_names,
        "optimal_pin_dimensions": p.optimal_pin_dimensions,
        "pin_cta_suggestion": p.pin_cta_suggestion,
    }

def _tiktok_to_dict(v: ShortFormVideoBlueprint) -> dict:
    return {
        "hook_0_3s": v.hook_0_3s,
        "problem_3_10s": v.problem_3_10s,
        "solution_10_25s": v.solution_10_25s,
        "call_to_action": v.call_to_action,
        "suggested_hashtags": v.suggested_hashtags,
        "estimated_duration": v.estimated_duration,
        "hook_type": v.hook_type,
    }

def _youtube_to_dict(y: YouTubeShortsBlueprint) -> dict:
    return {
        "video_title": y.video_title,
        "title_tags": y.title_tags,
        "hook_segment": y.hook_segment,
        "body_segment": y.body_segment,
        "call_to_action": y.call_to_action,
        "description_template": y.description_template,
        "suggested_thumbnails": y.suggested_thumbnails,
        "estimated_duration": y.estimated_duration,
        "shorts_hashtags": y.shorts_hashtags,
        "seo_tips": y.seo_tips,
    }

def _twitter_to_dict(t: TwitterViralThread) -> dict:
    return {
        "thread_theme": t.thread_theme,
        "tweets": t.tweets,
        "suggested_hashtags": t.suggested_hashtags,
        "cta_final_tweet": t.cta_final_tweet,
        "optimal_posting_time": t.optimal_posting_time,
    }

def _ad_copy_to_dict(ac: AdCopyVariations) -> dict:
    return {
        "headline": ac.headline_v1,
        "headline_v2": ac.headline_v2,
        "primary_text": ac.primary_text,
        "description": ac.description,
        "cta_label": ac.cta_label,
    }

def _paid_to_dict(p: PaidAdPackage) -> dict:
    return {
        "allocated_daily_budget": p.allocated_daily_budget,
        "ad_copy": {k: _ad_copy_to_dict(v) for k, v in p.ad_copy.items()},
        "audience_suggestions": p.audience_suggestions,
        "platform_recommendations": p.platform_recommendations,
        "creative_specs": p.creative_specs,
    }

def _compile_package_response(pkg: CompiledPackage, mode: str) -> dict:
    compiled = {}

    if pkg.pinterest_seo_engine:
        compiled["pinterest_seo_engine"] = _pinterest_to_dict(pkg.pinterest_seo_engine)

    if pkg.short_form_video_blueprint:
        compiled["short_form_video_blueprint"] = _tiktok_to_dict(pkg.short_form_video_blueprint)

    if pkg.youtube_shorts_blueprint:
        compiled["youtube_shorts_blueprint"] = _youtube_to_dict(pkg.youtube_shorts_blueprint)

    if pkg.twitter_viral_thread:
        compiled["twitter_viral_thread"] = _twitter_to_dict(pkg.twitter_viral_thread)

    if pkg.paid_ad_package:
        compiled = _paid_to_dict(pkg.paid_ad_package)   # paid mode: flat structure

    return compiled

def _get_ai_mode() -> str:
    """Highest-priority AI mode available."""
    if generator._has_openai:
        return "openai"
    if generator._minimax:
        return "minimax"
    return "template_fallback"


# ─────────────────────────────────────────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
async def health_check():
    """Service health + AI backend status."""
    ai_modes = generator.ai_modes
    return JSONResponse({
        "status": "healthy",
        "service": APP_NAME,
        "version": APP_VERSION,
        "ai_backend_active": _get_ai_mode(),
        "ai_backends": {
            "openai":  ai_modes["openai"],
            "minimax": ai_modes["minimax"],
        },
        "video_rendering": {
            "configured": MiniMaxVideoClient.is_configured(),
            "models":     MiniMaxVideoClient.supported_models(),
        },
        "campaigns_stored": db.count(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.post("/api/v1/traffic/generate", response_model=None)
async def generate_traffic(request: GenerateRequest) -> JSONResponse:
    """
    Full pipeline: scrape URL → generate assets → persist → return.

    Organic channels: pinterest, tiktok_organic, youtube_shorts, twitter_threads
    Paid channels:    meta_ads, tiktok_ads
    """
    log.info("Request: mode=%s channels=%s url=%s",
             request.mode, request.target_channels, request.input_url)

    start_ts = datetime.now(timezone.utc)

    # ── Scrape ────────────────────────────────────────────────────────────
    try:
        product: ScrapedProduct = scraper.scrape(request.input_url)
    except Exception as exc:
        log.error("Scrape failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not reach URL. Verify it is publicly accessible: {request.input_url}",
        )

    # ── Generate ──────────────────────────────────────────────────────────
    try:
        pkg: CompiledPackage = generator.generate(
            product=product,
            mode=request.mode,
            target_channels=request.target_channels,
            daily_budget=request.daily_budget,
        )
        elapsed_ms = (datetime.now(timezone.utc) - start_ts).total_seconds() * 1000
    except Exception as exc:
        log.error("Generation failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Content generation failed: {exc}",
        )

    # ── Build compiled response ────────────────────────────────────────────
    compiled = _compile_package_response(pkg, request.mode)
    ai_mode = _get_ai_mode()

    # ── Persist to SQLite ──────────────────────────────────────────────────
    try:
        record = db.save(
            input_url=request.input_url,
            mode=request.mode,
            channels=request.target_channels,
            budget=request.daily_budget,
            product_title=product.title,
            product_image=product.primary_image,
            assets=compiled,
            ai_mode=ai_mode,
        )
        campaign_id = record.id
    except Exception as exc:
        log.warning("DB save failed (non-fatal): %s", exc)
        campaign_id = None

    return JSONResponse({
        "success": True,
        "campaign_id": campaign_id,
        "input_url": request.input_url,
        "mode": request.mode,
        "scraped_product": product.to_dict(),
        "compiled_package": compiled,
        "meta": {
            "ai_mode": ai_mode,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "processing_time_ms": round(elapsed_ms),
        },
    })


# ── MiniMax video params endpoint ──────────────────────────────────────────

@app.post("/api/v1/minimax/video-params")
async def minimax_video_params(request: MiniMaxVideoParamsRequest) -> JSONResponse:
    """
    Generate MiniMax-compatible video generation parameters for a product URL.
    Returns seed prompt, aspect ratio, and style hints ready to paste into
    MiniMax Hailuo AI or any compatible t2v pipeline.

    Requires MINIMAX_API_KEY and MINIMAX_GROUP_ID in environment.
    """
    try:
        product: ScrapedProduct = scraper.scrape(request.input_url)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not scrape URL: {exc}",
        )

    result = generator.minimax_video_params(product, request.platform)
    return JSONResponse({
        "success": True,
        "input_url": request.input_url,
        "platform": request.platform,
        "product_title": product.title,
        **result,
    })


# ── MiniMax actual video rendering endpoints ──────────────────────────────
# These call MiniMax t2v (H3 / H3-Max / Hailuo-2.3) and return a real MP4 URL,
# unlike /minimax/video-params which only returns seed parameters.

def _video_client() -> MiniMaxVideoClient:
    """Build a fresh MiniMaxVideoClient (cheap to construct)."""
    if not MiniMaxVideoClient.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "MINIMAX_API_KEY is not configured on this deployment. "
                "Add it in the Render dashboard (Environment → Environment Variables) "
                "to enable video rendering."
            ),
        )
    return MiniMaxVideoClient()


@app.get("/api/v1/video/models")
async def list_video_models() -> JSONResponse:
    """List supported MiniMax video models and their constraints."""
    return JSONResponse({
        "configured": MiniMaxVideoClient.is_configured(),
        "models": [
            {
                "id":                model_id,
                "supports_native_audio": model_id in ("MiniMax-H3", "MiniMax-H3-Max"),
                "duration_seconds":  [spec["min_dur"], spec["max_dur"]],
                "ratios":            spec["ratios"],
                "resolutions":       spec["resolutions"],
            }
            for model_id, spec in MiniMaxVideoClient.SUPPORTED_MODELS.items()
        ],
    })


@app.post("/api/v1/video/generate")
async def submit_video(request: VideoSubmitRequest) -> JSONResponse:
    """
    Submit an async video generation task. Returns immediately with a
    `task_id`. Frontend should poll `/api/v1/video/status/{task_id}` until
    `status == 'succeeded'` to obtain `video_url`.
    """
    client = _video_client()
    try:
        task = client.submit(
            prompt=request.prompt,
            model=request.model,
            duration=request.duration,
            ratio=request.ratio,
            resolution=request.resolution,
            reference_image_url=request.reference_image_url,
        )
    except MiniMaxVideoError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return JSONResponse({"success": True, "task": task.to_dict()})


@app.get("/api/v1/video/status/{task_id}")
async def video_status(
    task_id: str,
    model: Annotated[str, Query(description="MiniMax model used to submit the task")] = "MiniMax-H3",
) -> JSONResponse:
    """Poll a previously submitted video task for current status + video_url."""
    client = _video_client()
    try:
        task = client.query(task_id, model=model)
    except MiniMaxVideoError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return JSONResponse({"success": True, "task": task.to_dict()})


@app.post("/api/v1/video/render")
async def render_video(request: VideoRenderRequest) -> JSONResponse:
    """
    Submit a video task AND block until it reaches a terminal status, then
    return the final `video_url`. Suitable for synchronous frontends where
    the user is waiting on a single result.

    Note: H3 typically takes 15-30 minutes. Use the async `/video/generate`
    endpoint for a better UX, or call this with `poll_timeout >= 1800`.
    """
    client = _video_client()
    try:
        task = client.render(
            prompt=request.prompt,
            model=request.model,
            duration=request.duration,
            ratio=request.ratio,
            resolution=request.resolution,
            reference_image_url=request.reference_image_url,
            poll_timeout=request.poll_timeout,
        )
    except MiniMaxVideoError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return JSONResponse({"success": True, "task": task.to_dict()})


# ── Campaign history endpoints ─────────────────────────────────────────────

@app.get("/api/v1/campaigns/history")
async def list_campaigns(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JSONResponse:
    """
    Retrieve saved campaigns, newest first.

    Returns a lightweight summary (id, url, mode, title, image, timestamp)
    for the history list in the frontend.
    """
    records = db.list_history(limit=limit, offset=offset)
    return JSONResponse({
        "campaigns": [
            {
                "id": r["id"],
                "input_url": r["input_url"],
                "mode": r["mode"],
                "channels": r["channels"],
                "product_title": r["product_title"],
                "product_image": r["product_image"],
                "ai_mode": r["ai_mode"],
                "created_at": r["created_at"],
            }
            for r in records
        ],
        "total": db.count(),
        "limit": limit,
        "offset": offset,
    })


@app.get("/api/v1/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str) -> JSONResponse:
    """
    Retrieve a single full campaign (including all generated assets).
    Use this to reload a past campaign into the dashboard without re-scraping.
    """
    record = db.get_by_id(campaign_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign not found: {campaign_id}",
        )
    # Rename 'assets' → 'compiled_package' for frontend compatibility
    record["compiled_package"] = record.pop("assets")
    return JSONResponse(record)


@app.delete("/api/v1/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str) -> JSONResponse:
    """Delete a specific campaign by ID."""
    deleted = db.delete(campaign_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign not found: {campaign_id}",
        )
    return JSONResponse({"success": True, "deleted": campaign_id})


@app.delete("/api/v1/campaigns/clear")
async def clear_all_campaigns() -> JSONResponse:
    """Delete ALL saved campaigns. Use with caution."""
    count = db.clear_all()
    return JSONResponse({
        "success": True,
        "deleted": count,
        "message": f"Cleared {count} campaign(s)",
    })


# ── Root ───────────────────────────────────────────────────────────────────

# Mount the product-scout router (trending/evergreen product suggestions).
app.include_router(product_scout_router)

@app.get("/", include_in_schema=False)
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "true").lower() in ("true", "1", "yes")

    log.info("Starting %s v%s on http://%s:%d", APP_NAME, APP_VERSION, host, port)

    if not generator._has_openai:
        log.warning("OPENAI_API_KEY not set — running in template fallback mode")

    if not generator._minimax:
        log.info("MINIMAX_API_KEY not set — MiniMax video params unavailable")

    uvicorn.run(
        "trafficlift_pro:app",
        host=host,
        port=port,
        reload=reload,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
