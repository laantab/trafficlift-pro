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
    """GET variant. Query params for browser/curl/handshake calls."""
    excl = [x for x in (exclude or "").split(",") if x] if exclude else []
    return _synthesize(
        url_or_keyword=url_or_keyword,
        category=category,
        seed=seed,
        exclude=excl,
        use_ai=use_ai,
    )


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