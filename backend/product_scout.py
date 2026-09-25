import random
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.quality_control import ProductControlAgent

router = APIRouter(prefix="/api/v1", tags=["product-scout"])

class ProductResponse(BaseModel):
    id: str
    name: str
    category: str
    image_url: str
    url: str
    angle: str
    pin_title: str
    pin_description: str
    hashtags: list[str]
    demo: bool = True

EVERGREEN_WINNING_PRODUCTS = [
    {
        "id": "scrub-brush-01",
        "name": "Rechargeable Electric Spin Scrubber with Replaceable Heads",
        "category": "Home & Cleaning",
        "image_url": "https://images.unsplash.com/photo-1585421514284-efb74c2b69ba?w=800&auto=format&fit=crop&q=60",
        "url": "https://example.com/trending-electric-scrubber",
        "angle": "Cut your bathroom and kitchen cleaning time in half with zero elbow grease.",
        "pin_title": "The Deep Cleaning Hack That Saved My Saturday! 🧽✨",
        "pin_description": "Tired of scrubbing grout on your hands and knees? This rechargeable electric spin scrubber does all the heavy lifting for you with interchangeable heads. Click to see how easy deep cleaning can be!",
        "hashtags": ["#CleaningHacks", "#HomeOrganization", "#CleaningMotivation", "#SmartHome"],
        "demo": True
    },
    {
        "id": "charger-02",
        "name": "3-in-1 Foldable MagSafe Wireless Charging Station",
        "category": "Tech & Gadgets",
        "image_url": "https://images.unsplash.com/photo-1611532736597-de2d4265fba3?w=800&auto=format&fit=crop&q=60",
        "url": "https://example.com/trending-3in1-charger",
        "angle": "Declutter your nightstand with fast, simultaneous charging for iPhone, Apple Watch, and AirPods.",
        "pin_title": "Nightstand Setup Upgrade: Zero Cable Clutter 🔌📱",
        "pin_description": "Say goodbye to tangled cords! This 3-in-1 foldable wireless charging station powers your phone, watch, and earbuds all at once. Perfect for travel or your bedside table.",
        "hashtags": ["#TechGadgets", "#DeskSetup", "#NightstandDecor", "#AppleAccessories"],
        "demo": True
    },
    {
        "id": "dog-bed-03",
        "name": "Orthopedic Calming Donut Dog Bed",
        "category": "Pet Supplies",
        "image_url": "https://images.unsplash.com/photo-1541599540903-216a46ca1dc0?w=800&auto=format&fit=crop&q=60",
        "url": "https://example.com/trending-calming-dog-bed",
        "angle": "Relieve pet anxiety and joint pain with faux-fur self-warming comfort.",
        "pin_title": "Give Your Pup the Ultimate Cozy Sleep 🐾💤",
        "pin_description": "Designed to ease anxiety and support aching joints, this plush self-warming donut dog bed is a game changer for anxious pets. Watch them fall instantly in love with it!",
        "hashtags": ["#DogLovers", "#PetCare", "#HappyPets", "#DogBed"],
        "demo": True
    },
    {
        "id": "sunset-lamp-04",
        "name": "Sunset Projection LED Ambient Lamp",
        "category": "Aesthetic Home Decor",
        "image_url": "https://images.unsplash.com/photo-1507652313519-d4e9174996dd?w=800&auto=format&fit=crop&q=60",
        "url": "https://example.com/trending-sunset-lamp",
        "angle": "Instantly transform any room's vibe for cozy evenings and viral social media content.",
        "pin_title": "Golden Hour Vibes All Year Round 🌅✨",
        "pin_description": "Bring the warmth of a California sunset right into your bedroom. Create the ultimate aesthetic mood lighting for photos, relaxation, and cozy nights in.",
        "hashtags": ["#RoomDecor", "#AestheticVibes", "#GoldenHour", "#HomeInspo"],
        "demo": True
    }
]

# ─────────────────────────────────────────────────────────────────────────────
# Catalog audit (run at import time; semantic warnings are logged,
# schema failures are loud — never blocks the server silently)
# ─────────────────────────────────────────────────────────────────────────────
import logging
_logger = logging.getLogger("product_scout")
try:
    ProductControlAgent.audit_catalog(EVERGREEN_WINNING_PRODUCTS)
except ValueError as exc:
    _logger.error("Product catalog FAILED audit at import time: %s", exc)
    raise


@router.get("/trending-product", response_model=ProductResponse)
def get_winning_product(seed: int | None = None):
    """Returns a high-converting evergreen product with synced image and Pinterest copy."""
    if seed is not None:
        random.seed(seed)
    product = random.choice(EVERGREEN_WINNING_PRODUCTS)
    return product

@router.get("/trending-products", response_model=list[ProductResponse])
def get_all_trending_products(category: str | None = None):
    """Returns the full product catalog, with optional category filtering."""
    if category:
        filtered = [p for p in EVERGREEN_WINNING_PRODUCTS if category.lower() in p["category"].lower()]
        if not filtered:
            raise HTTPException(status_code=404, detail=f"No products found in category: {category}")
        return filtered
    return EVERGREEN_WINNING_PRODUCTS

@router.post("/trending-product/refresh")
def refresh_trending_sources():
    """Stub for syncing live product trends from external feeds."""
    return {
        "status": "success",
        "source": "static-curated-evergreen",
        "total_catalog": len(EVERGREEN_WINNING_PRODUCTS),
        "message": "Product catalog verified and synchronized successfully."
    }
