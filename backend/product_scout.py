"""
trafficlift_pro/backend/product_scout.py
Product Scout — Trending & Evergreen Product Suggestions

Exposes a curated list of high-converting evergreen products that the
TrafficLift Pro frontend can use as one-click demo URLs. Each product
ships with an "angle" — a one-sentence pitch that doubles as a ready
script seed for the AI generation engine.

Endpoints (mounted under /api/v1 via trafficlift_pro.include_router):
    GET /trending-product      → returns ONE random product (the headline UX)
    GET /trending-products     → returns the full list (browse all)
    POST /trending-product/refresh  → stub for a future live-feed source
"""

from __future__ import annotations

import random
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["product-scout"])


# ─────────────────────────────────────────────────────────────────────────────
# Catalog
# ─────────────────────────────────────────────────────────────────────────────

EVERGREEN_WINNING_PRODUCTS: list[dict] = [
    {
        "id":      "3in1-magsafe-charger",
        "name":    "3-in-1 Foldable MagSafe Wireless Charging Station",
        "category": "Tech & Gadgets",
        "url":     "https://example.com/trending-3in1-charger",
        "angle":   (
            "Declutter your nightstand with fast, simultaneous charging for "
            "iPhone, Apple Watch, and AirPods."
        ),
        "demo":    True,
    },
    {
        "id":      "calming-donut-dog-bed",
        "name":    "Orthopedic Calming Donut Dog Bed",
        "category": "Pet Supplies",
        "url":     "https://example.com/trending-calming-dog-bed",
        "angle":   (
            "Relieve pet anxiety and joint pain with faux-fur self-warming "
            "comfort."
        ),
        "demo":    True,
    },
    {
        "id":      "sonic-scrub-brush",
        "name":    "Rechargeable Sonic Electric Scrub Brush with 6 Heads",
        "category": "Home & Cleaning",
        "url":     "https://example.com/trending-electric-scrubber",
        "angle":   (
            "Cut your bathroom and kitchen cleaning time in half with zero "
            "elbow grease."
        ),
        "demo":    True,
    },
    {
        "id":      "sunset-projection-lamp",
        "name":    "Sunset Projection LED Ambient Lamp",
        "category": "Aesthetic Home Decor",
        "url":     "https://example.com/trending-sunset-lamp",
        "angle":   (
            "Instantly transform any room's vibe for cozy evenings and viral "
            "social media content."
        ),
        "demo":    True,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class ProductResponse(BaseModel):
    """Single trending product."""
    id:       str
    name:     str
    category: str
    url:      str
    angle:    str
    demo:     bool = Field(
        description=(
            "True when the URL is a placeholder/demo. The frontend can use "
            "the angle as a synthetic product pitch instead of scraping."
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trending-product", response_model=ProductResponse)
def get_winning_product(seed: Optional[int] = None) -> ProductResponse:
    """
    Return ONE trending evergreen product. Pass `?seed=N` for a deterministic
    pick (useful in tests / reproducible demos).
    """
    rng = random.Random(seed) if seed is not None else random
    product = rng.choice(EVERGREEN_WINNING_PRODUCTS)
    return ProductResponse(**product)


@router.get("/trending-products", response_model=list[ProductResponse])
def list_winning_products(category: Optional[str] = None) -> list[ProductResponse]:
    """Return the full catalog. Optional `?category=...` filter."""
    products = EVERGREEN_WINNING_PRODUCTS
    if category:
        products = [
            p for p in products
            if category.lower() in p["category"].lower()
        ]
        if not products:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No products in category: {category}",
            )
    return [ProductResponse(**p) for p in products]


@router.post("/trending-product/refresh")
def refresh_catalog() -> dict:
    """
    Stub for a future live-trending-product source (e.g., scrape TikTok /
    Amazon Movers & Shakers / Etsy trends). The current catalog is a
    static curated list — this endpoint just acknowledges the call.
    """
    return {
        "success":    True,
        "count":      len(EVERGREEN_WINNING_PRODUCTS),
        "source":     "static-curated",
        "note":       (
            "Live refresh not yet implemented. To enable, wire a real "
            "trends source (e.g. Amazon Movers & Shakers, TikTok Creative "
            "Center) into this endpoint."
        ),
        "next_steps": [
            "Add an httpx scraper for Amazon Movers & Shakers JSON feed",
            "Pipe results into EVERGREEN_WINNING_PRODUCTS at startup",
            "Cache results in db.py for the configured TTL",
        ],
    }
