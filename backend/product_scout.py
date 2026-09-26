import random
import hashlib
from fastapi import APIRouter, Request
from pydantic import BaseModel
from backend.product_control_agent import ProductControlAgent

router = APIRouter(prefix="/api/v1", tags=["product-scout"])

class ProductRequest(BaseModel):
    url_or_keyword: str | None = None
    category: str | None = None

PRODUCTS_POOL = [
    {
        "id": "tech-01",
        "name": "LED Desk Lamp with USB Charging Port — Dimmable, 5 Color Temps",
        "category": "Tech & Gadgets",
        "image_url": "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?w=800&auto=format&fit=crop&q=80",
        "url": "https://www.amazon.com/dp/B083ZD8W7S",
        "angle": "Touch dimming, 5 color temps, memory function, and USB pass-through charging.",
        "pin_title": "Why My Ring Light Collects Dust Now 💡",
        "pin_description": "5 color temps (2700K-6500K), 5 brightness levels, USB-A pass-through charging, and memory function. Perfect for WFH and desk setups.",
        "hashtags": ["#DeskSetup", "#WFH", "#TechGadgets", "#LightingDesign", "#AmazonFinds"],
        "trend_score": 92,
        "margin": "High (60-70%)",
        "evergreen": "90%"
    },
    {
        "id": "home-02",
        "name": "High-Torque Electric Spin Scrubber Pro",
        "category": "Home & Cleaning",
        "image_url": "https://images.unsplash.com/photo-1581578731548-c64695cc6952?w=800&auto=format&fit=crop&q=80",
        "url": "https://www.amazon.com/dp/B09X7G7291",
        "angle": "Eliminate deep grime, grout lines, and bathroom tiles in seconds without breaking your back.",
        "pin_title": "Deep Cleaning Tile & Grout Made Effortless 🧽✨",
        "pin_description": "Stop scrubbing on your knees! High-torque electric scrubber blasts through soap scum instantly with 4 replaceable brush heads.",
        "hashtags": ["#CleaningHacks", "#DeepCleaning", "#HomeOrganization", "#AmazonMustHaves"],
        "trend_score": 95,
        "margin": "High (65-75%)",
        "evergreen": "85%"
    },
    {
        "id": "kitchen-03",
        "name": "Compact Portable Espresso Maker & Cold Brew Press",
        "category": "Kitchen & Dining",
        "image_url": "https://images.unsplash.com/photo-1514432324607-a09d9b4aefdd?w=800&auto=format&fit=crop&q=80",
        "url": "https://www.amazon.com/dp/B073WD6M8Z",
        "angle": "Barista-quality espresso anywhere in under 60 seconds with manual pressure pumping.",
        "pin_title": "Ditch  Coffee Shops With This Travel Espresso Press ☕",
        "pin_description": "Extract rich, velvety crema anywhere—office, road trips, or camping. No electricity needed.",
        "hashtags": ["#CoffeeLovers", "#Espresso", "#TravelEssentials", "#KitchenGadgets"],
        "trend_score": 88,
        "margin": "Medium (50-60%)",
        "evergreen": "92%"
    },
    {
        "id": "fitness-04",
        "name": "Smart Pilates Reformer Bar with Resistance Bands",
        "category": "Fitness & Wellness",
        "image_url": "https://images.unsplash.com/photo-1518611012118-696072aa579a?w=800&auto=format&fit=crop&q=80",
        "url": "https://www.amazon.com/dp/B08BL3X78Q",
        "angle": "Full-body studio workout from home targeting core, arms, and glutes in 15 minutes.",
        "pin_title": "Studio Pilates At Home For Less Than One Session 🧘‍♀️",
        "pin_description": "Get tone and defined without heavy equipment. Toning bar + adjustable resistance bands for full body strength.",
        "hashtags": ["#HomeWorkout", "#PilatesBar", "#FitnessGoals", "#AtHomeGym"],
        "trend_score": 91,
        "margin": "High (70%)",
        "evergreen": "88%"
    }
]

@router.api_route("/find-winner", methods=["GET", "POST"])
async def find_winner(request: Request, payload: ProductRequest | None = None):
    # Select a random winner from the curated pool
    selected = random.choice(PRODUCTS_POOL).copy()
    
    # Audit via ProductControlAgent
    audited = ProductControlAgent.audit_product(selected)
    return audited
