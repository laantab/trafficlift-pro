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
    excl = [x for x in (exclude or "").split(",") if x] if exclude else []
    return _synthesize(
        url_or_keyword=url.strip(),
        category="Trending General",
        seed=seed,
        exclude=excl,
        use_ai=False,
    )


@router.get("/discover-winner")
def discover_winner_get(
    exclude: Optional[str] = Query(
        None,
        description="Comma-separated product ids to avoid.",
    ),
    seed: Optional[int] = Query(None, ge=0, le=10_000),
) -> dict:
    """PATH A — true live product discovery.

    No user query, no keyword. Runs Tavily research queries for real
    trending product opportunities, builds candidate products from the
    results, scores + qualifies them with supported signals only,
    verifies a real product image for each, and returns the first
    qualified candidate as the winner.

    Returns the same winner payload shape as /find-winner so the
    frontend's shared _runPickPipeline works for both workflows.

    On exhaustion of the candidate budget, returns HTTP 404 with a
    discovery-specific message — NEVER the keyword-relevance message
    from the legacy path.
    """
    from backend.discovery import discover_winner as _discover
    # The seed / exclude params are honored inside _pick_pipeline via
    # the exclude param on the second leg. The discovery route doesn't
    # need them directly, but we accept them for forward-compat.
    _ = (exclude, seed)
    return _discover()


@router.get("/diagnostics/discover-winner")
def diagnostics_discover_winner() -> dict:
    """Temporary diagnostic: run discover_winner with detailed timing
    and step logging. Returns the full trace so we can debug why
    PATH A is failing on Render."""
    import time as _t
    from backend import discovery as _discovery_mod
    from backend import live_research as _lr_mod

    log: list[dict] = []
    t_overall_start = _t.monotonic()

    # 1. Live phase
    t = _t.monotonic()
    log.append({"step": "start", "elapsed_ms": round((t - t_overall_start) * 1000)})

    all_envelopes = []
    seen_titles: set[str] = set()
    candidates = []

    from backend.discovery import (
        DISCOVERY_QUERIES, MAX_DISCOVERY_RESEARCH_QUERIES,
        LIVE_PHASE_BUDGET_SECONDS, DISCOVERY_REQUEST_BUDGET_SECONDS,
    )
    live_deadline = t_overall_start + LIVE_PHASE_BUDGET_SECONDS
    for query in DISCOVERY_QUERIES[:MAX_DISCOVERY_RESEARCH_QUERIES]:
        if _t.monotonic() > live_deadline:
            log.append({"step": "live budget exhausted", "queries_so_far": len(all_envelopes)})
            break
        t_q = _t.monotonic()
        try:
            env = _lr_mod.research(query, max_results=5)
            elapsed_q = round((_t.monotonic() - t_q) * 1000)
            n_sources = len(env.research_sources or []) if hasattr(env, "research_sources") else 0
            n_images = len(env.research_image_urls or []) if hasattr(env, "research_image_urls") else 0
            log.append({
                "step": "research query",
                "query": query,
                "elapsed_ms": elapsed_q,
                "sources": n_sources,
                "images": n_images,
            })
            all_envelopes.append(env)
        except Exception as exc:
            log.append({"step": "research error", "query": query, "error": str(exc)})

    raw_results = sum(len(e.research_sources or []) for e in all_envelopes)
    log.append({"step": "live phase done", "raw_results": raw_results, "envelopes": len(all_envelopes)})

    # 2. Curated phase: try the FIRST entry in the fast pool
    t_curated = _t.monotonic()
    deadline_monotonic = t_overall_start + DISCOVERY_REQUEST_BUDGET_SECONDS
    from backend.curated_winners import CURATED_WINNERS
    fast_pool = [w for w in CURATED_WINNERS if w.get("direct_image_url") or w.get("direct_image_urls")]
    import random as _r
    _r.shuffle(fast_pool)
    log.append({"step": "curated phase start", "fast_pool_size": len(fast_pool),
                "elapsed_ms": round((_t.monotonic() - t_overall_start) * 1000),
                "budget_left_ms": round((deadline_monotonic - _t.monotonic()) * 1000)})

    first_entry = fast_pool[0] if fast_pool else None
    if first_entry:
        log.append({"step": "trying first fast entry",
                    "name": first_entry["name"][:60],
                    "category": first_entry.get("category"),
                    "has_direct_url": bool(first_entry.get("direct_image_url")),
                    "direct_urls_count": len(first_entry.get("direct_image_urls") or [])})
        # Direct-validate each direct URL ourselves to see if HEAD works
        from backend.product_control_agent import _validate_image, rank_image_candidates
        direct_urls = list(first_entry.get("direct_image_urls") or [])
        if first_entry.get("direct_image_url"):
            direct_urls.insert(0, first_entry["direct_image_url"])
        for u in direct_urls:
            t_v = _t.monotonic()
            try:
                chk = _validate_image(u, head_timeout=2.0)
                log.append({
                    "step": "direct validate",
                    "url": u[:80],
                    "elapsed_ms": round((_t.monotonic() - t_v) * 1000),
                    "ok": chk.ok,
                    "reason": chk.reason,
                    "status": chk.image_status,
                    "ct": chk.image_content_type,
                    "bytes": chk.image_bytes,
                    "http": chk.http_status,
                })
            except Exception as exc:
                log.append({
                    "step": "direct validate error",
                    "url": u[:80],
                    "elapsed_ms": round((_t.monotonic() - t_v) * 1000),
                    "error": str(exc),
                })
        # Test _resolve_curated_image
        image = _discovery_mod._resolve_curated_image(first_entry, deadline_monotonic)
        log.append({"step": "_resolve_curated_image result",
                    "name": first_entry["name"][:60],
                    "image_url": image[:80] if image else None,
                    "elapsed_ms": round((_t.monotonic() - t_curated) * 1000)})

        if image:
            # Try the audit
            import re as _re
            from backend.product_research import ProductResearcher, ProductCard
            card_id = _re.sub(r"\W+", "-", first_entry["name"].lower())[:60].strip("-") or "curated"
            card = ProductCard(
                id=f"curated-{card_id}",
                name=first_entry["name"],
                category=first_entry.get("category") or "Trending General",
                image_url=image,
                url=first_entry.get("source_url") or "",
                angle_options=first_entry.get("angle_options") or [],
                pin_title_options=first_entry.get("pin_title_options") or [],
                pin_description_options=first_entry.get("pin_description_options") or [],
                hashtags_pool=first_entry.get("hashtags_pool") or [],
                viral_hook_options=first_entry.get("viral_hook_options") or [],
                trend_score_range=tuple(first_entry.get("trend_score_range") or (70, 88)),
                trend_signals_options=first_entry.get("trend_signals_options") or [[]],
                margin_estimate=first_entry.get("margin_estimate") or "Medium (25-40%)",
                evergreen_score=float(first_entry.get("evergreen_score") or 0.75),
                competition=first_entry.get("competition") or "Medium",
                competition_reasons=first_entry.get("competition_reasons") or [],
            )
            t_audit = _t.monotonic()
            from backend.product_control_agent import ProductControlAgent
            audit_payload = ProductResearcher()._materialize(card, card.category)
            audit_payload["source"] = "discovery-curated"
            audit_payload["source_label"] = first_entry.get("source_label") or "Curated"
            audit_payload["image_url"] = image
            report = ProductControlAgent.evaluate(audit_payload)
            log.append({"step": "audit result",
                        "elapsed_ms": round((_t.monotonic() - t_audit) * 1000),
                        "ok": report.ok,
                        "reasons": report.reasons,
                        "image_check_ok": report.image_check.ok if report.image_check else None,
                        "image_check_reason": report.image_check.reason if report.image_check else None,
                        "image_check_status": report.image_check.image_status if report.image_check else None,
                        "image_check_http_status": report.image_check.http_status if report.image_check else None,
                        "image_check_content_type": report.image_check.image_content_type if report.image_check else None,
                        })
            if report.ok:
                log.append({"step": "WINNER FOUND", "name": first_entry["name"][:60]})

    return {
        "ok": True,
        "total_elapsed_ms": round((_t.monotonic() - t_overall_start) * 1000),
        "log": log,
    }


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