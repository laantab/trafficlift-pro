"""
backend/product_scout.py
Product Scout — research-backed winning-product picker.

Routes:
  GET  /api/v1/find-winner?url_or_keyword=...&category=...&seed=...&exclude=id1,id2
  POST /api/v1/find-winner              { "url_or_keyword": "...", "category": "...",
                                          "seed": ..., "exclude": [...], "use_ai": false }

The researcher rotates through a curated pool of 30 trending products across
6 categories and varies copy on every call. When OPENAI_API_KEY is configured,
`use_ai=true` triggers a fresh GPT-4o research pick (falls back to pool on
failure). Every result is audited by ProductControlAgent.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.product_research import (
    ProductResearcher,
    get_researcher,
    POOLS,
    CATEGORY_LABELS,
)

router = APIRouter(prefix="/api/v1", tags=["product-scout"])


class ProductRequest(BaseModel):
    """POST body for /find-winner."""
    url_or_keyword: str = Field(
        default="trending product",
        description="URL or free-form keyword to seed intent bias.",
    )
    category: str = Field(default="Trending General")
    seed: Optional[int] = Field(default=None, ge=0, le=10_000)
    exclude: list[str] = Field(
        default_factory=list,
        description="Product ids to avoid (e.g. the last one shown).",
    )
    use_ai: bool = Field(
        default=False,
        description="If true and OpenAI is configured, generate a fresh pick via GPT-4o.",
    )


# ── Routes ─────────────────────────────────────────────────────────────────


def _synthesize(
    *,
    url_or_keyword: str,
    seed: Optional[int],
    exclude: list[str],
    use_ai: bool,
) -> dict:
    raw_input = (url_or_keyword or "").strip()
    if not raw_input:
        raise HTTPException(
            status_code=400,
            detail="URL or keyword cannot be empty.",
        )
    researcher = get_researcher()
    return researcher.pick(
        intent=raw_input,
        exclude=exclude,
        seed=seed,
        use_ai=use_ai,
    )


@router.post("/find-winner")
def find_winner_post(payload: ProductRequest) -> dict:
    """POST variant: JSON body with the full control surface."""
    return _synthesize(
        url_or_keyword=payload.url_or_keyword,
        seed=payload.seed,
        exclude=payload.exclude,
        use_ai=payload.use_ai,
    )


@router.get("/find-winner")
def find_winner_get(
    url_or_keyword: str = Query(
        "trending product",
        description="URL or free-form keyword to seed intent synthesis.",
    ),
    seed: Optional[int] = Query(None, ge=0, le=10_000),
    exclude: Optional[str] = Query(
        None,
        description="Comma-separated product ids to avoid (e.g. 'scrub-brush-01,dog-bed-01').",
    ),
    use_ai: bool = Query(False, description="Generate via GPT-4o (requires OPENAI_API_KEY)."),
) -> dict:
    """GET variant: query params for browser/curl/handshake calls."""
    excl = [x for x in (exclude or "").split(",") if x] if exclude else []
    return _synthesize(
        url_or_keyword=url_or_keyword,
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
    """Browse all products in the research pool (lightweight summary)."""
    summary = get_researcher().get_pool_summary()
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