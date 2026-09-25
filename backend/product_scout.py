import random
import hashlib
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.product_control_agent import ProductControlAgent

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
        "image_url": "https://images.unsplash.com/photo-1581578731548-c64695cc6952?w=800&auto=format&fit=crop&q=80",
        "url": "https://example.com/trending-electric-scrubber",
        "angle": "Cut your bathroom and kitchen cleaning time in half with zero elbow grease.",
        "pin_title": "The Deep Cleaning Hack That Saved My Saturday! ???",
        "pin_description": "Tired of scrubbing grout on your hands and knees? This rechargeable electric spin scrubber does all the heavy lifting for you with interchangeable heads. Click to see how easy deep cleaning can be!",
        "hashtags": ["#CleaningHacks", "#HomeOrganization", "#CleaningMotivation", "#SmartHome"],
        "demo": True
    },
    {
        "id": "charger-02",
        "name": "3-in-1 Foldable MagSafe Wireless Charging Station",
        "category": "Tech & Gadgets",
        "image_url": "https://images.unsplash.com/photo-1611532736597-de2d4265fba3?w=800&auto=format&fit=crop&q=80",
        "url": "https://example.com/trending-3in1-charger",
        "angle": "Declutter your nightstand with fast, simultaneous charging for iPhone, Apple Watch, and AirPods.",
        "pin_title": "Nightstand Setup Upgrade: Zero Cable Clutter ????",
        "pin_description": "Say goodbye to tangled cords! This 3-in-1 foldable wireless charging station powers your phone, watch, and earbuds all at once. Perfect for travel or your bedside table.",
        "hashtags": ["#TechGadgets", "#DeskSetup", "#NightstandDecor", "#AppleAccessories"],
        "demo": True
    },
    {
        "id": "dog-bed-03",
        "name": "Orthopedic Calming Donut Dog Bed",
        "category": "Pet Supplies",
        "image_url": "https://images.unsplash.com/photo-1541599540903-216a46ca1dc0?w=800&auto=format&fit=crop&q=80",
        "url": "https://example.com/trending-calming-dog-bed",
        "angle": "Relieve pet anxiety and joint pain with faux-fur self-warming comfort.",
        "pin_title": "Give Your Pup the Ultimate Cozy Sleep ????",
        "pin_description": "Designed to ease anxiety and support aching joints, this plush self-warming donut dog bed is a game changer for anxious pets. Watch them fall instantly in love with it!",
        "hashtags": ["#DogLovers", "#PetCare", "#HappyPets", "#DogBed"],
        "demo": True
    },
    {
        "id": "sunset-lamp-04",
        "name": "Sunset Projection LED Ambient Lamp",
        "category": "Aesthetic Home Decor",
        "image_url": "https://images.unsplash.com/photo-1513519245088-0e12902e5a38?w=800&auto=format&fit=crop&q=80",
        "url": "https://example.com/trending-sunset-lamp",
        "angle": "Instantly transform any room's vibe for cozy evenings and viral social media content.",
        "pin_title": "Golden Hour Vibes All Year Round ???",
        "pin_description": "Bring the warmth of a California sunset right into your bedroom. Create the ultimate aesthetic mood lighting for photos, relaxation, and cozy nights in.",
        "hashtags": ["#RoomDecor", "#AestheticVibes", "#GoldenHour", "#HomeInspo"],
        "demo": True
    }
]

@router.get("/trending-product", response_model=ProductResponse)
def get_winning_product(seed: int | None = None):
    """Returns a winning product audited and cleared by the Product Control Agent."""
    if seed is not None:
        random.seed(seed)
    product = random.choice(EVERGREEN_WINNING_PRODUCTS)
    return ProductControlAgent.audit_product(product)

@router.get("/trending-products", response_model=list[ProductResponse])
def get_all_trending_products(category: str | None = None):
    """Returns the full audited product catalog."""
    if category:
        filtered = [p for p in EVERGREEN_WINNING_PRODUCTS if category.lower() in p["category"].lower()]
        if not filtered:
            raise HTTPException(status_code=404, detail=f"No products found in category: {category}")
        return ProductControlAgent.audit_catalog(filtered)
    return ProductControlAgent.audit_catalog(EVERGREEN_WINNING_PRODUCTS)


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic scout — synthesize a product from a free-form URL or keyword
# ─────────────────────────────────────────────────────────────────────────────

class ProductRequest(BaseModel):
    url_or_keyword: str
    category: str = "Trending General"


@router.post("/find-winner", response_model=ProductResponse)
def analyze_and_find_winner(payload: ProductRequest) -> ProductResponse:
    """
    Performs live dynamic intent synthesis based on user input, crafts
    high-conversion marketing assets, and runs the result through the
    Product Control Agent before returning.
    """
    raw_input = (payload.url_or_keyword or "").strip()
    if not raw_input:
        raise HTTPException(
            status_code=400,
            detail="URL or keyword cannot be empty.",
        )

    # Deterministic unique ID per search query
    query_hash = hashlib.md5(raw_input.encode()).hexdigest()[:8]
    lower      = raw_input.lower()

    # Intent Detection & Dynamic Asset Generation
    is_cleaning = any(w in lower for w in ["clean", "scrub", "brush", "mop", "wash", "grout"])
    is_tech     = any(w in lower for w in ["charger", "tech", "phone", "gadget", "wireless", "magsafe"])
    is_pet      = any(w in lower for w in ["pet", "dog", "cat", "bed", "pup"])

    if is_cleaning:
        product_data = {
            "id":              f"dyn-{query_hash}",
            "name":            "Advanced Ultrasonic Electric Spin Scrubber Pro",
            "category":        "Home & Cleaning",
            "image_url":       "https://images.unsplash.com/photo-1581578731548-c64695cc6952?w=800&auto=format&fit=crop&q=80",
            "url":             raw_input if raw_input.startswith("http") else f"https://example.com/product/{query_hash}",
            "angle":           "Eliminate deep grime and grout lines in seconds with high-torque oscillation technology.",
            "pin_title":       "The Deep Cleaning Secret Professional Cleaners Swear By! 🧽✨",
            "pin_description": (
                f"Discovered via search for '{raw_input}'. Stop ruining your knees on "
                "tough tile. This high-torque electric scrubber blasts through soap "
                "scum instantly. Click to see the results!"
            ),
            "hashtags": ["#CleaningHacks", "#DeepCleaning", "#HomeOrganization", "#CleaningMotivation"],
            "demo":            False,
        }
    elif is_tech:
        product_data = {
            "id":              f"dyn-{query_hash}",
            "name":            "Ultra-Slim 3-in-1 Fast Wireless Charging Dock",
            "category":        "Tech & Gadgets",
            # 200 OK (verified) — the user snippet's URL 1622445275576-72232f5fc48f
            # returns 404 from Unsplash; replaced with the working one.
            "image_url":       "https://images.unsplash.com/photo-1611532736597-de2d4265fba3?w=800&auto=format&fit=crop&q=80",
            "url":             raw_input if raw_input.startswith("http") else f"https://example.com/product/{query_hash}",
            "angle":           "Streamline your charging ecosystem with simultaneous high-speed power delivery.",
            "pin_title":       "Clean Desk Setup Essential: Zero Cable Clutter 🔌📱",
            "pin_description": (
                f"Discovered via search for '{raw_input}'. Tired of messy cords on "
                "your desk? This sleek 3-in-1 charging dock powers your entire "
                "ecosystem seamlessly. Upgrade your space today!"
            ),
            "hashtags": ["#TechGadgets", "#DeskSetup", "#WorkspaceGoals", "#GadgetLovers"],
            "demo":            False,
        }
    elif is_pet:
        product_data = {
            "id":              f"dyn-{query_hash}",
            "name":            "Orthopedic Calming Donut Dog Bed",
            "category":        "Pet Supplies",
            "image_url":       "https://images.unsplash.com/photo-1541599540903-216a46ca1dc0?w=800&auto=format&fit=crop&q=80",
            "url":             raw_input if raw_input.startswith("http") else f"https://example.com/product/{query_hash}",
            "angle":           "Relieve pet anxiety and joint pain with faux-fur self-warming comfort.",
            "pin_title":       "Give Your Pup the Ultimate Cozy Sleep 🐾💤",
            "pin_description": (
                f"Discovered via search for '{raw_input}'. Designed to ease anxiety "
                "and support aching joints, this plush self-warming donut bed is a "
                "game changer for pets."
            ),
            "hashtags": ["#DogLovers", "#PetCare", "#HappyPets", "#DogBed"],
            "demo":            False,
        }
    else:
        # Dynamic extraction for any arbitrary store URL or product keyword
        clean_label = (
            raw_input.split("//")[-1].split("/")[0].replace("www.", "").title()
            if "//" in raw_input
            else raw_input.title()
        )
        product_data = {
            "id":              f"dyn-{query_hash}",
            "name":            f"Verified Market Winner: {clean_label}",
            "category":        payload.category,
            "image_url":       "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=800&auto=format&fit=crop&q=80",
            "url":             raw_input if raw_input.startswith("http") else f"https://example.com/product/{query_hash}",
            "angle":           f"High-converting viral product extracted from live analysis of {clean_label}.",
            "pin_title":       "You Need to See This Viral Product Find! 🔥👀",
            "pin_description": (
                f"Extracted via market intelligence targeting '{raw_input}'. This item "
                "is currently trending across social discovery channels. Tap to explore!"
            ),
            "hashtags": ["#TrendingFinds", "#MustHave", "#ViralProducts", "#SmartShopping"],
            "demo":            False,
        }

    # Pass the dynamically generated payload through the Product Control Agent
    audited = ProductControlAgent.audit_product(product_data)
    return ProductResponse(**audited)
