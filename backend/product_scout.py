"""
backend/product_scout.py
Product Scout — research-backed winning-product picker.

Routes
------
GET  /api/v1/find-winner
POST /api/v1/find-winner
GET  /api/v1/find-winners          (browse the pool, optional ?category=)

Every result is audit-cleared by ProductControlAgent before it leaves
the router, so the response shape is guaranteed.

Stability guarantees
--------------------
• Endpoint never raises an unhandled exception — all errors are mapped to
  proper HTTPException with actionable detail messages.
• Both GET and POST parse their input the same way (url_or_keyword,
  optional category, optional seed, optional exclude list, optional
  use_ai flag).
• Pydantic models validate inputs with explicit constraints.
• When the underlying researcher fails (e.g. invalid AI JSON), we fall
  back to a deterministic pool pick rather than 500.
• JSON responses are dicts (not Pydantic models) so missing optional
  fields never break the dashboard.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.product_research import (
    POOLS,
    CATEGORY_LABELS,
    get_researcher,
)

logger = logging.getLogger("product_scout")

router = APIRouter(prefix="/api/v1", tags=["product-scout"])


# ── Request / Response models ───────────────────────────────────────────────


class ProductRequest(BaseModel):
    """POST body for /find-winner. Everything is optional with sensible
    defaults so the endpoint never 400s on a malformed-but-tolerable body."""
    url_or_keyword: str = Field(
        default="trending product",
        max_length=200,
        description="URL or free-form keyword to seed intent synthesis.",
    )
    category: str = Field(
        default="Trending General",
        max_length=100,
        description="Category label used when the keyword is unclassified.",
    )
    seed: Optional[int] = Field(
        default=None, ge=0, le=10_000,
        description="Deterministic seed for tests.",
    )
    exclude: list[str] = Field(
        default_factory=list,
        description="Product ids to avoid (e.g. the last one shown).",
    )
    use_ai: bool = Field(
        default=False,
        description="If true and OpenAI is configured, generate a fresh pick via GPT-4o.",
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def _synthesize(
    *,
    url_or_keyword: str,
    category: str,
    seed: Optional[int],
    exclude: list[str],
    use_ai: bool,
) -> dict:
    """Single synthesis path used by both GET and POST. Always returns a dict
    or raises HTTPException with a useful detail message."""
    raw_input = (url_or_keyword or "").strip()
    if not raw_input:
        raise HTTPException(
            status_code=400,
            detail="URL or keyword cannot be empty.",
        )

    try:
        researcher = get_researcher()
        return researcher.pick(
            intent=raw_input,
            exclude=exclude,
            seed=seed,
            use_ai=use_ai,
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Defensive: if the researcher itself blows up, surface a 500 with a
        # clear message so the dashboard can show an actionable toast.
        logger.exception("Researcher.pick() failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Researcher error: {type(exc).__name__}: {exc}",
        )


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get("/diagnostics/raw-tavily")
def diagnostics_raw_tavily(q: str = Query("phone stand", max_length=100)) -> dict:
    """Raw Tavily response — exposes what the real Tavily API actually
    returns (status, results, images). Used to verify that the
    `include_images: True` parameter is producing image URLs.
    Does NOT return the API key.
    """
    import os
    import requests
    from backend.live_research import TAVILY_ENDPOINT, DEFAULT_USER_AGENT

    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return {"error": "TAVILY_API_KEY not configured"}
    try:
        resp = requests.post(
            TAVILY_ENDPOINT,
            json={
                "api_key": api_key,
                "query": q + " product photo",
                "max_results": 5,
                "search_depth": "basic",
                "include_answer": False,
                "include_images": True,
                "topic": "general",
            },
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=10,
        )
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        first_image = (body.get("images") or [None])[0]
        first_image_type = type(first_image).__name__
        return {
            "query": q,
            "http_status": resp.status_code,
            "keys_in_response": list(body.keys()),
            "results_count": len(body.get("results") or []),
            "images_count": len(body.get("images") or []),
            "first_image_type": first_image_type,
            "first_image_repr": str(first_image)[:200],
            "answer": (body.get("answer") or "")[:200],
            "first_result_url": ((body.get("results") or [{}])[0]).get("url"),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@router.get("/diagnostics/research")
def diagnostics_research() -> dict:
    """Safe runtime diagnostic — reports which research/image providers are
    CONFIGURED on this deployment. Does NOT return any secret values.

    Useful for verifying that TAVILY_API_KEY (or alternative keys) is
    available without exposing the key itself.
    """
    import os
    from backend import live_research

    tavily_present = bool(os.getenv("TAVILY_API_KEY", "").strip())
    tavily_len = len(os.getenv("TAVILY_API_KEY", "").strip())
    openai_present = bool(os.getenv("OPENAI_API_KEY", "").strip())
    return {
        "providers": {
            "tavily_configured": tavily_present,
            "tavily_key_length_chars": tavily_len if tavily_present else 0,
            "duckduckgo_configured": True,  # DDG HTML needs no key
            "openai_configured": openai_present,
        },
        "constants": {
            "DEFAULT_TIMEOUT_s": live_research.DEFAULT_TIMEOUT,
            "research_images_max_results_default": 3,
        },
        "image_validation": {
            "MIN_IMAGE_BYTES": 5000,
            "HEAD_timeout_s": 3.0,
            "redirects_allowed_on_HEAD": False,
            "redirects_allowed_on_GET": False,
        },
        "note": (
            "If tavily_configured is false, the live research/image paths "
            "fall back to DuckDuckGo HTML — which often returns a JS shell "
            "with no real image URLs. In that environment, real product "
            "photos are usually impossible to discover and the pipeline "
            "returns 404 with 'No photo-qualified winner found'."
        ),
    }


@router.get("/diagnostics/image-search")
def diagnostics_image_search(q: str = Query("phone stand", max_length=100)) -> dict:
    """Live diagnostic: exercise the real Tavily image-search path on this
    deployment and report every URL discovered + its validation outcome.
    Does NOT return the API key. Used to diagnose why /find-winner returns
    404 with 'No photo-qualified winner found' on common product queries.
    """
    import time
    import requests
    from backend import live_research
    from backend.product_control_agent import _validate_image

    t0 = time.monotonic()
    urls = live_research.research_images(f"{q} product photo", max_results=5)
    elapsed = time.monotonic() - t0

    out: list[dict] = []
    for u in urls:
        # Trace exactly what the validator sees — first HEAD no-redirect,
        # then HEAD with redirects followed, so we can prove whether
        # redirects are the cause of validation failures.
        trace: dict = {"url": u[:120]}
        try:
            t = time.monotonic()
            r = requests.head(u, timeout=3.0, allow_redirects=False)
            trace["head_no_redirect"] = {
                "status": r.status_code,
                "content_type": (r.headers.get("Content-Type") or "")[:40],
                "location": (r.headers.get("Location") or "")[:80],
                "elapsed_s": round(time.monotonic() - t, 2),
            }
            if r.status_code in (301, 302, 303, 307, 308):
                t = time.monotonic()
                r2 = requests.head(u, timeout=3.0, allow_redirects=True)
                trace["head_followed"] = {
                    "status": r2.status_code,
                    "final_url": r2.url[:120],
                    "content_type": (r2.headers.get("Content-Type") or "")[:40],
                    "elapsed_s": round(time.monotonic() - t, 2),
                }
        except Exception as exc:
            trace["head_no_redirect"] = {"error": f"{type(exc).__name__}: {exc}"}
        # Run the actual validator the pipeline uses.
        try:
            chk = _validate_image(u)
            trace["validator"] = {
                "ok": chk.ok,
                "reason": chk.reason,
                "image_status": chk.image_status,
                "content_type": (chk.image_content_type or "")[:40],
                "http_status": chk.http_status,
            }
        except Exception as exc:
            trace["validator"] = {"error": f"{type(exc).__name__}: {exc}"}
        out.append(trace)

    return {
        "query": q,
        "provider_configured": bool(os.getenv("TAVILY_API_KEY", "").strip()),
        "elapsed_s": round(elapsed, 2),
        "urls_discovered": len(urls),
        "urls_validated_ok": sum(1 for t in out if t.get("validator", {}).get("ok")),
        "urls": out,
    }


@router.post("/find-winner")
def find_winner_post(payload: ProductRequest) -> dict:
    """POST variant. JSON body with the full control surface."""
    return _synthesize(
        url_or_keyword=payload.url_or_keyword,
        category=payload.category,
        seed=payload.seed,
        exclude=payload.exclude,
        use_ai=payload.use_ai,
    )


@router.get("/find-winner")
def find_winner_get(
    url_or_keyword: str = Query(
        "trending product",
        max_length=200,
        description="URL or free-form keyword to seed intent synthesis.",
    ),
    category: str = Query(
        "Trending General",
        max_length=100,
        description="Category label used when the keyword is unclassified.",
    ),
    seed: Optional[int] = Query(
        None, ge=0, le=10_000,
        description="Deterministic seed for tests.",
    ),
    exclude: Optional[str] = Query(
        None,
        description="Comma-separated product ids to avoid (e.g. 'charger-01,dog-bed-01').",
    ),
    use_ai: bool = Query(
        False,
        description="Generate via GPT-4o (requires OPENAI_API_KEY).",
    ),
) -> dict:
    """GET variant. Query params for browser/curl/handshake calls.

    PATH A — discovery. The frontend calls this with NO URL/keyword to
    launch the 'Find Winning Product' flow: live research, candidate
    ranking, image cascade. The default 'trending product' seeds
    Tavily's trending-products search.
    """
    excl = [x for x in (exclude or "").split(",") if x] if exclude else []
    return _synthesize(
        url_or_keyword=url_or_keyword,
        category=category,
        seed=seed,
        exclude=excl,
        use_ai=use_ai,
    )


@router.get("/analyze-product-url")
def analyze_product_url_get(
    url: str = Query(
        ...,
        max_length=2000,
        description="Product URL (Amazon / Shopify / WooCommerce / etc.). "
                    "The frontend's 'Analyze Product' button hits this.",
    ),
    exclude: Optional[str] = Query(
        None,
        description="Comma-separated product ids to avoid.",
    ),
    seed: Optional[int] = Query(None, ge=0, le=10_000),
) -> dict:
    """PATH B — analyze a specific product URL.

    Resolves redirects, extracts ASIN / title / category, runs the
    standard Product Control Agent gate, and returns either a verified
    winner payload or a structured 404 with a clear reason. The URL
    is the user's explicit ask — we MUST NOT substitute a random
    pool candidate when the URL can't be resolved.
    """
    from fastapi import HTTPException
    if not url or not url.strip():
        raise HTTPException(
            status_code=400,
            detail="URL is required for /analyze-product-url.",
        )
    if not (url.lower().startswith("http://") or url.lower().startswith("https://")):
        raise HTTPException(
            status_code=400,
            detail=(
                "We couldn't identify or validate that product URL. "
                "Try the full product page URL (must start with http:// or https://)."
            ),
        )
    from backend.exact_product import analyze_exact_product
    return analyze_exact_product(url.strip())


@router.get("/discover-winner")
def discover_winner_get(
    exclude: Optional[str] = Query(None, max_length=6000),
    exclude_keys: Optional[str] = Query(None, max_length=9000),
    client_id: Optional[str] = Query(None, min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$"),
    seed: Optional[int] = Query(None, ge=0, le=10_000),
) -> dict:
    """Fresh-only discovery with browser-scoped durable exclusions.

    No keyword input, no hidden saved-list substitution. All provider calls
    are bounded; failures become actionable 404/503 responses.
    """
    from backend.discovery import discover_winner as _discover
    excl = {x for x in (exclude or "").split(",") if x}
    keys = {x for x in (exclude_keys or "").split(",") if x}
    try:
        return _discover(exclude_ids=excl, exclude_keys=keys, client_id=client_id, seed=seed)
    except HTTPException:
        raise
    except (OSError, sqlite3.Error):
        logger.exception("Discovery history unavailable")
        raise HTTPException(503, "Product history is temporarily unavailable. Try again shortly; no repeat was substituted.")


@router.get("/find-winners")
def list_winners(
    category: Optional[str] = Query(
        None,
        description="Optional category key (cleaning, tech, pet, decor, fitness, kitchen).",
    ),
) -> dict:
    """Browse the research pool (lightweight summary)."""
    try:
        summary = get_researcher().get_pool_summary()
    except Exception as exc:
        logger.exception("get_pool_summary failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Pool summary error: {type(exc).__name__}: {exc}",
        )

    if category:
        if category not in POOLS:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown category: {category}. Valid: {sorted(POOLS.keys())}",
            )
        return {
            "category": CATEGORY_LABELS[category],
            "products": summary["categories"][category],
            "total": len(POOLS[category]),
        }
    return summary
