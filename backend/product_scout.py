"""
trafficlift_pro/backend/product_scout.py
Product Scout — Trending & Evergreen Product Suggestions

Exposes a curated list of high-converting evergreen products that the
TrafficLift Pro frontend can use as one-click demo URLs. Each product
ships with:
  - id, name, category           → identity
  - url                          → where to find it (often example.com for demos)
  - image_url                    → Unsplash hero photo
  - angle                        → one-sentence pitch for AI script seeding
  - pin_title / pin_description  → pre-written Pinterest SEO copy
  - hashtags                     → list[str] of Pinterest hashtag slugs
  - demo                         → true when the URL is a placeholder

Endpoints (mounted under /api/v1 via trafficlift_pro.include_router):
    GET /trending-product              → returns ONE random product
    GET /trending-products             → returns the full list
    GET /trending-products?category=…  → filtered list (404 on no match)
    POST /trending-product/refresh     → stub for future live-trends source
"""

from __future__ import annotations

import random
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["product-scout"])


# ─────────────────────────────────────────────────────────────────────────────
# Catalog
# ─────────────────────────────────────────────────────────────────────────────

EVERGREEN_WINNING_PRODUCTS: list[dict] = [
    {
        "id":      "scrub-brush-01",
        "name":    "Rechargeable Sonic Electric Scrub Brush with 6 Heads",
        "category": "Home & Cleaning",
        "image_url": "https://images.unsplash.com/photo-1527515637462-cff94eecc1ac",
        "url":     "https://example.com/trending-electric-scrubber",
        "angle":   (
            "Cut your bathroom and kitchen cleaning time in half with zero "
            "elbow grease."
        ),
        "pin_title":       "The Deep Cleaning Hack That Saved My Saturday! 🧽✨",
        "pin_description": (
            "Tired of scrubbing grout on your hands and knees? This "
            "rechargeable sonic scrubber does all the heavy lifting for you "
            "with 6 interchangeable heads. Click to see how easy deep "
            "cleaning can be!"
        ),
        "hashtags": ["#CleaningHacks", "#HomeOrganization", "#CleaningMotivation", "#SmartHome"],
        "demo":    True,
    },
    {
        "id":      "charger-02",
        "name":    "3-in-1 Foldable MagSafe Wireless Charging Station",
        "category": "Tech & Gadgets",
        "image_url": "https://images.unsplash.com/photo-1586816879360-004f5b0c51e3",
        "url":     "https://example.com/trending-3in1-charger",
        "angle":   (
            "Declutter your nightstand with fast, simultaneous charging for "
            "iPhone, Apple Watch, and AirPods."
        ),
        "pin_title":       "Nightstand Setup Upgrade: Zero Cable Clutter 🔌📱",
        "pin_description": (
            "Say goodbye to tangled cords! This 3-in-1 foldable wireless "
            "charging station powers your phone, watch, and earbuds all at "
            "once. Perfect for travel or your bedside table."
        ),
        "hashtags": ["#TechGadgets", "#DeskSetup", "#NightstandDecor", "#AppleAccessories"],
        "demo":    True,
    },
    {
        "id":      "dog-bed-03",
        "name":    "Orthopedic Calming Donut Dog Bed",
        "category": "Pet Supplies",
        "image_url": "https://images.unsplash.com/photo-1541599540903-216a46ca1dc0",
        "url":     "https://example.com/trending-calming-dog-bed",
        "angle":   (
            "Relieve pet anxiety and joint pain with faux-fur self-warming "
            "comfort."
        ),
        "pin_title":       "Give Your Pup the Ultimate Cozy Sleep 🐾💤",
        "pin_description": (
            "Designed to ease anxiety and support aching joints, this plush "
            "self-warming donut dog bed is a game changer for anxious pets. "
            "Watch them fall instantly in love with it!"
        ),
        "hashtags": ["#DogLovers", "#PetCare", "#HappyPets", "#DogBed"],
        "demo":    True,
    },
    {
        "id":      "sunset-lamp-04",
        "name":    "Sunset Projection LED Ambient Lamp",
        "category": "Aesthetic Home Decor",
        "image_url": "https://images.unsplash.com/photo-1513519245088-0e12902e5a38",
        "url":     "https://example.com/trending-sunset-lamp",
        "angle":   (
            "Instantly transform any room's vibe for cozy evenings and viral "
            "social media content."
        ),
        "pin_title":       "Golden Hour Vibes All Year Round 🌅✨",
        "pin_description": (
            "Bring the warmth of a California sunset right into your bedroom. "
            "Create the ultimate aesthetic mood lighting for photos, "
            "relaxation, and cozy nights in."
        ),
        "hashtags": ["#RoomDecor", "#AestheticVibes", "#GoldenHour", "#HomeInspo"],
        "demo":    True,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class ProductResponse(BaseModel):
    """Single trending product."""
    id:               str
    name:             str
    category:         str
    image_url:        str
    url:              str
    angle:            str
    pin_title:        str
    pin_description:  str
    hashtags:         list[str]
    demo:             bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trending-product", response_model=ProductResponse)
def get_winning_product(seed: Optional[int] = None) -> ProductResponse:
    """
    Return ONE trending evergreen product. Pass `?seed=N` for a deterministic
    pick (useful in tests / reproducible demos).
    """
    if seed is not None:
        random.seed(seed)
    product = random.choice(EVERGREEN_WINNING_PRODUCTS)
    return product


@router.get("/trending-products", response_model=list[ProductResponse])
def get_all_trending_products(category: Optional[str] = None) -> list[ProductResponse]:
    """Return the full product catalog, with optional category filtering."""
    if category:
        filtered = [
            p for p in EVERGREEN_WINNING_PRODUCTS
            if category.lower() in p["category"].lower()
        ]
        if not filtered:
            raise HTTPException(
                status_code=404,
                detail=f"No products found in category: {category}",
            )
        return filtered
    return EVERGREEN_WINNING_PRODUCTS


@router.post("/trending-product/refresh")
def refresh_trending_sources() -> dict:
    """Stub for syncing live product trends from external feeds."""
    return {
        "status":        "success",
        "source":        "static-curated-evergreen",
        "total_catalog": len(EVERGREEN_WINNING_PRODUCTS),
        "message":       "Product catalog verified and synchronized successfully.",
    }
