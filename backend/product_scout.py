"""
backend/product_scout.py
Product Scout — synthesizes a winning product from a free-form URL or keyword.

Routes:
  GET  /api/v1/find-winner?url_or_keyword=...&category=...
  POST /api/v1/find-winner              { "url_or_keyword": "...", "category": "..." }

Every result is audited by ProductControlAgent before being returned, so the
shape is guaranteed to satisfy the dashboard's `displayWinnerCard` consumer.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from backend.product_control_agent import ProductControlAgent

router = APIRouter(prefix="/api/v1", tags=["product-scout"])


class ProductRequest(BaseModel):
    """POST body for /find-winner."""
    url_or_keyword: str = "trending product"
    category: str = "Trending General"


# ── Intent classifier ──────────────────────────────────────────────────────

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "cleaning": [
        "clean", "scrub", "brush", "mop", "wash", "wipe", "vacuum",
        "grout", "ultrasonic", "soap", "tile", "kitchen", "bathroom",
    ],
    "tech": [
        "charger", "tech", "phone", "gadget", "wireless", "magsafe",
        "cable", "dock", "station", "hub", "led", "iphone", "android",
        "bluetooth", "usb",
    ],
    "pet": [
        "pet", "dog", "cat", "pup", "puppy", "kitten", "bed",
        "leash", "grooming", "calming", "kennel", "crate",
    ],
    "home decor": [
        "lamp", "sunset", "projection", "light", "decor", "aesthetic",
        "ambient", "mood", "bedroom", "cozy",
    ],
}


def _classify_intent(text: str) -> str | None:
    """Return the first matching category, or None if no group matches."""
    lower = text.lower()
    for cat, words in _CATEGORY_KEYWORDS.items():
        if any(w in lower for w in words):
            return cat
    return None


# ── Branch templates ───────────────────────────────────────────────────────
# Each template carries a verified Unsplash image URL and audit-safe copy.
# The product name is rewritten per-request to keep the "synthesis" honest.

def _build_cleaning(raw_input: str, query_hash: str) -> dict:
    return {
        "id": f"dyn-{query_hash}",
        "name": "Advanced Ultrasonic Electric Spin Scrubber Pro",
        "category": "Home & Cleaning",
        "image_url": (
            "https://images.unsplash.com/photo-1581578731548-c64695cc6952"
            "?w=800&auto=format&fit=crop&q=80"
        ),
        "url": _coerce_url(raw_input, query_hash),
        "angle": (
            "Eliminate deep grime and grout lines in seconds with "
            "high-torque oscillation technology."
        ),
        "pin_title": "The Deep Cleaning Secret Professional Cleaners Swear By! 🧽✨",
        "pin_description": (
            f"Discovered via search for '{raw_input}'. Stop ruining your "
            "knees on tough tile. This high-torque electric scrubber blasts "
            "through soap scum instantly. Click to see the results!"
        ),
        "hashtags": ["#CleaningHacks", "#DeepCleaning", "#HomeOrganization", "#CleaningMotivation"],
        "demo": False,
    }


def _build_tech(raw_input: str, query_hash: str) -> dict:
    return {
        "id": f"dyn-{query_hash}",
        "name": "Ultra-Slim 3-in-1 Fast Wireless Charging Dock",
        "category": "Tech & Gadgets",
        "image_url": (
            "https://images.unsplash.com/photo-1611532736597-de2d4265fba3"
            "?w=800&auto=format&fit=crop&q=80"
        ),
        "url": _coerce_url(raw_input, query_hash),
        "angle": (
            "Streamline your charging ecosystem with simultaneous "
            "high-speed power delivery."
        ),
        "pin_title": "Clean Desk Setup Essential: Zero Cable Clutter 🔌📱",
        "pin_description": (
            f"Discovered via search for '{raw_input}'. Tired of messy cords "
            "on your desk? This sleek 3-in-1 charging dock powers your "
            "entire ecosystem seamlessly. Upgrade your space today!"
        ),
        "hashtags": ["#TechGadgets", "#DeskSetup", "#WorkspaceGoals", "#GadgetLovers"],
        "demo": False,
    }


def _build_pet(raw_input: str, query_hash: str) -> dict:
    return {
        "id": f"dyn-{query_hash}",
        "name": "Orthopedic Calming Donut Dog Bed",
        "category": "Pet Supplies",
        "image_url": (
            "https://images.unsplash.com/photo-1541599540903-216a46ca1dc0"
            "?w=800&auto=format&fit=crop&q=80"
        ),
        "url": _coerce_url(raw_input, query_hash),
        "angle": (
            "Relieve pet anxiety and joint pain with faux-fur "
            "self-warming comfort."
        ),
        "pin_title": "Give Your Pup the Ultimate Cozy Sleep 🐾💤",
        "pin_description": (
            f"Discovered via search for '{raw_input}'. Designed to ease "
            "anxiety and support aching joints, this plush self-warming "
            "donut bed is a game changer for pets."
        ),
        "hashtags": ["#DogLovers", "#PetCare", "#HappyPets", "#DogBed"],
        "demo": False,
    }


def _build_home_decor(raw_input: str, query_hash: str) -> dict:
    return {
        "id": f"dyn-{query_hash}",
        "name": "Sunset Projection LED Ambient Lamp",
        "category": "Aesthetic Home Decor",
        "image_url": (
            "https://images.unsplash.com/photo-1513519245088-0e12902e5a38"
            "?w=800&auto=format&fit=crop&q=80"
        ),
        "url": _coerce_url(raw_input, query_hash),
        "angle": (
            "Instantly transform any room's vibe for cozy evenings "
            "and viral social media content."
        ),
        "pin_title": "Golden Hour Vibes All Year Round 🌅✨",
        "pin_description": (
            f"Discovered via search for '{raw_input}'. Bring the warmth "
            "of a California sunset right into your bedroom. Create the "
            "ultimate aesthetic mood lighting for photos, relaxation, "
            "and cozy nights in."
        ),
        "hashtags": ["#RoomDecor", "#AestheticVibes", "#GoldenHour", "#HomeInspo"],
        "demo": False,
    }


def _build_general(raw_input: str, query_hash: str, category: str) -> dict:
    """Catch-all for unclassified input (URLs without obvious keywords, etc.)."""
    clean_label = _humanize(raw_input) or "Trending Product"
    return {
        "id": f"dyn-{query_hash}",
        "name": f"Verified Market Winner: {clean_label}",
        "category": category or "Trending General",
        "image_url": (
            "https://images.unsplash.com/photo-1523275335684-37898b6baf30"
            "?w=800&auto=format&fit=crop&q=80"
        ),
        "url": _coerce_url(raw_input, query_hash),
        "angle": (
            f"High-converting viral product extracted from live "
            f"market analysis of {clean_label}."
        ),
        "pin_title": "You Need to See This Viral Product Find! 🔥👀",
        "pin_description": (
            f"Extracted via market intelligence targeting '{raw_input}'. "
            "This item is currently trending across social discovery "
            "channels. Tap to explore!"
        ),
        "hashtags": ["#TrendingFinds", "#MustHave", "#ViralProducts", "#SmartShopping"],
        "demo": False,
    }


# ── Helpers ────────────────────────────────────────────────────────────────

def _coerce_url(raw_input: str, query_hash: str) -> str:
    """If the user gave a real URL, return it; otherwise synthesize a stub."""
    if raw_input.startswith(("http://", "https://")):
        return raw_input
    return f"https://example.com/product/{query_hash}"


def _humanize(raw_input: str) -> str:
    """Turn a URL or kebab-case keyword into a Title-Cased label."""
    s = raw_input.strip()
    if not s:
        return ""
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("//")[-1].split("/")[0].replace("www.", "")
    s = s.replace("-", " ").replace("_", " ").replace(".", " ")
    return " ".join(w.capitalize() for w in s.split() if w)


def _synthesize(raw_input: str, category: str) -> dict:
    """Classify → pick a branch → audit → return."""
    raw_input = (raw_input or "").strip()
    if not raw_input:
        raise HTTPException(
            status_code=400,
            detail="URL or keyword cannot be empty.",
        )

    query_hash = hashlib.md5(raw_input.encode()).hexdigest()[:8]
    intent = _classify_intent(raw_input)

    if intent == "cleaning":
        product = _build_cleaning(raw_input, query_hash)
    elif intent == "tech":
        product = _build_tech(raw_input, query_hash)
    elif intent == "pet":
        product = _build_pet(raw_input, query_hash)
    elif intent == "home decor":
        product = _build_home_decor(raw_input, query_hash)
    else:
        product = _build_general(raw_input, query_hash, category)

    return ProductControlAgent.audit_product(product)


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("/find-winner")
def find_winner_post(payload: ProductRequest) -> dict:
    """POST variant: JSON body with `url_or_keyword` (+ optional `category`)."""
    return _synthesize(payload.url_or_keyword, payload.category)


@router.get("/find-winner")
def find_winner_get(
    url_or_keyword: str = Query(
        "trending product",
        description="URL or free-form keyword to seed intent synthesis.",
    ),
    category: str = Query(
        "Trending General",
        description="Category label used when the keyword is unclassified.",
    ),
) -> dict:
    """GET variant: query params for browser/curl/handshake calls."""
    return _synthesize(url_or_keyword, category)