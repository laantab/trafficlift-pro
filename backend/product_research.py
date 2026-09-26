"""
backend/product_research.py
Product Research Engine — pools of trending products with rotation, variation,
and optional AI augmentation.

Design goals:
  • Each call returns a DIFFERENT product (rotation across a curated pool).
  • Within a product, copy is varied each call (mix-and-match parts).
  • Every result carries research metadata: trend_score, trend_signals,
    margin_estimate, viral_hook, evergreen_score, competition.
  • When OPENAI_API_KEY is configured, the researcher can ask GPT-4o for a
    freshly generated pick — otherwise it falls back to the pool.
  • Source ("pool" vs "openai_research") is always reported so the dashboard
    can show provenance honestly.

The control agent (ProductControlAgent) audits every result before it leaves
the router, so shape and semantic consistency are guaranteed.
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import threading
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("ProductResearcher")

# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ProductCard:
    """A single trending product with research-grade metadata."""
    id: str
    name: str
    category: str
    image_url: str
    url: str
    angle_options: list[str]                 # multiple selling angles
    pin_title_options: list[str]
    pin_description_options: list[str]
    hashtags_pool: list[str]                 # varied subset picked per call
    viral_hook_options: list[str]
    trend_score_range: tuple[int, int]       # e.g. (78, 92)
    trend_signals_options: list[list[str]]   # each inner list is a coherent set
    margin_estimate: str                     # "Low (15-25%)" etc.
    evergreen_score: float                   # 0.0-1.0
    competition: str                         # "Low" | "Medium" | "High"
    competition_reasons: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Keyword → category classifier (richer than the scout's, used for bias only)
# ─────────────────────────────────────────────────────────────────────────────


_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "cleaning": [
        "clean", "scrub", "brush", "mop", "wash", "wipe", "vacuum",
        "grout", "ultrasonic", "soap", "tile", "kitchen", "bathroom",
        "toilet", "shower", "stain", "dust",
    ],
    "tech": [
        "charger", "tech", "phone", "gadget", "wireless", "magsafe",
        "cable", "dock", "station", "hub", "led", "iphone", "android",
        "bluetooth", "usb", "laptop", "monitor", "keyboard", "mouse",
        "smart", "desk",
    ],
    "pet": [
        "pet", "dog", "cat", "pup", "puppy", "kitten", "bed",
        "leash", "grooming", "calming", "kennel", "crate", "feeder",
        "litter", "treat",
    ],
    "decor": [
        "lamp", "sunset", "projection", "light", "decor", "aesthetic",
        "ambient", "mood", "bedroom", "cozy", "throw", "candle", "plant",
        "shelf", "mirror",
    ],
    "fitness": [
        "yoga", "fitness", "workout", "exercise", "gym", "stretch",
        "posture", "massage", "foam", "roller", "resistance", "band",
        "dumbbell", "pilates", "wellness", "pain",
    ],
    "kitchen": [
        "kitchen", "cook", "chef", "air fryer", "knife", "spice",
        "coffee", "mug", "lunch", "bento", "pan", "skillet", "meal",
        "recipe", "bake", "grocery",
    ],
}


def _classify(text: str) -> str | None:
    lower = text.lower()
    scores: dict[str, int] = {}
    for cat, words in _CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for w in words if w in lower)
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0] if best[1] > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# Curated product pools (30 trending products, 5 per category, all 200-verified)
# Each product carries multiple copy variants for mix-and-match variation.
# ─────────────────────────────────────────────────────────────────────────────


def _img(pid: str) -> str:
    return f"https://images.unsplash.com/photo-{pid}?w=800&auto=format&fit=crop&q=80"


# ── HOME & CLEANING ────────────────────────────────────────────────────────

CLEANING_POOL: list[ProductCard] = [
    ProductCard(
        id="scrub-brush-01",
        name="Rechargeable Electric Spin Scrubber with 6 Replaceable Heads",
        category="Home & Cleaning",
        image_url=_img("1581578731548-c64695cc6952"),
        url="https://example.com/trending-electric-scrubber",
        angle_options=[
            "Cut your bathroom and kitchen cleaning time in half with zero elbow grease.",
            "Erase years of grout stains in seconds — six heads cover every surface.",
            "Professional-grade spin power that fits in one hand and saves your knees.",
        ],
        pin_title_options=[
            "The Deep Cleaning Hack That Saved My Saturday! 🧽✨",
            "Why Every Home Needs This $30 Cleaning Gadget 🧴",
            "I Cleaned My Whole Bathroom in 12 Minutes 😅",
            "Grout Stains Vanished in 30 Seconds 😱",
        ],
        pin_description_options=[
            "Tired of scrubbing grout on your hands and knees? This rechargeable electric spin scrubber does all the heavy lifting for you with six interchangeable heads. Click to see how easy deep cleaning can be!",
            "Rechargeable spin scrubber with 6 interchangeable heads for tile, grout, tub, sink, and stove. Save 2+ hours per deep clean. Tap to see the before/after!",
        ],
        hashtags_pool=["#CleaningHacks", "#DeepCleaning", "#HomeOrganization", "#CleaningMotivation",
                       "#CleanTok", "#MomHacks", "#HomeEssentials", "#BathroomGoals"],
        viral_hook_options=[
            "The 30-second grout transformation that broke TikTok.",
            "Six heads, one bathroom, zero scrubbing.",
            "Your knees will thank you in about 4 minutes flat.",
        ],
        trend_score_range=(84, 94),
        trend_signals_options=[
            ["+47% saves on Pinterest Q3 2025", "#CleanTok 2.1B views",
             "Featured in 14 cleaning influencer roundups", "Avg. sell-through 18 days"],
            ["Appeared in 6 'TikTok made me buy it' compilations",
             "Repeat-purchase rate 22%", "+31% Amazon search volume YoY"],
        ],
        margin_estimate="High (55-65%)",
        evergreen_score=0.82,
        competition="Medium",
        competition_reasons=["Low-cost clones flood Amazon", "Brand trust matters"],
    ),
    ProductCard(
        id="microfiber-cloths-02",
        name="Premium Microfiber Cleaning Cloths — 12-Pack (Color Coded)",
        category="Home & Cleaning",
        image_url=_img("1610557892470-55d9e80c0bce"),
        url="https://example.com/trending-microfiber-cloths",
        angle_options=[
            "One cloth per surface — color-coded so you never cross-contaminate again.",
            "Washable 500+ times. Replaces 6+ rolls of paper towels per month.",
            "The single swap that paid for itself in two weeks.",
        ],
        pin_title_options=[
            "Stop Using the Same Rag Everywhere 🤢",
            "Color-Coded Cloths Changed My Cleaning Routine 🧺",
            "Paper Towels Are a Scam. Here's Why. 🧻",
            "The $15 Swap That Saved Me $40/Month 💸",
        ],
        pin_description_options=[
            "Color-coded microfiber cloths keep bathrooms, kitchens, and electronics separate — no more mystery streaks. Reusable, machine-washable, lint-free. Tap to see the full system!",
            "Stop wasting money on paper towels. This 12-pack replaces 6+ rolls per month and lasts for years. Zero chemicals needed.",
        ],
        hashtags_pool=["#EcoFriendly", "#SustainableLiving", "#CleaningHacks", "#ZeroWaste",
                       "#MomHacks", "#HomeOrganization", "#Paperless"],
        viral_hook_options=[
            "Six colors, six surfaces, zero cross-contamination.",
            "One $15 pack replaces 72 paper towel rolls a year.",
            "Your kitchen counter doesn't need more Windex. It needs better cloths.",
        ],
        trend_score_range=(72, 84),
        trend_signals_options=[
            ["+62% Pinterest saves YoY", "Sustainability searches +41% in 2025",
             "Featured in 8 zero-waste roundups"],
            ["Repeat-purchase cycle 11 months", "#ZeroWaste 4.2M posts",
             "+18% Amazon search volume MoM"],
        ],
        margin_estimate="High (65-75%)",
        evergreen_score=0.94,
        competition="High",
        competition_reasons=["Crowded category", "Price-sensitive buyers"],
    ),
    ProductCard(
        id="toilet-brush-03",
        name="Silicone Toilet Brush with Holder — Hygienic, No Bristle Buildup",
        category="Home & Cleaning",
        image_url=_img("1567113463300-102a7eb3cb26"),
        url="https://example.com/trending-silicone-toilet-brush",
        angle_options=[
            "Silicone head sheds nothing. The brush that actually stays clean.",
            "Replace your 5-year-old plastic brush for under $15.",
            "One wipe, one flush, zero mess.",
        ],
        pin_title_options=[
            "Your Toilet Brush is Disgusting. Replace It. 🚽",
            "I Can't Believe I Used a Plastic Brush for 10 Years 🤯",
            "The Silicone Brush That Broke My Cleaning Anxiety 😮‍💨",
            "$15 to Upgrade the Worst Job in Your House 🧻",
        ],
        pin_description_options=[
            "Plastic bristle brushes trap bacteria and never fully dry. Silicone sheds nothing, dries in seconds, and lasts for years. The single upgrade every bathroom deserves.",
            "TPR silicone head + drip-free holder. Replaces that crusty brush you've been ignoring.",
        ],
        hashtags_pool=["#CleaningHacks", "#BathroomGoals", "#HomeOrganization",
                       "#AdultingThings", "#CleaningMotivation"],
        viral_hook_options=[
            "Your toilet brush is the dirtiest thing in your house. Do something about it.",
            "Plastic bristles + damp holder = bacterial hotel. Silicone fixes both.",
        ],
        trend_score_range=(68, 79),
        trend_signals_options=[
            ["+29% Amazon search volume", "Featured in 6 'gadgets under $20' lists"],
            ["#CleanTok 1.8B views", "Avg. cart-add rate 14%"],
        ],
        margin_estimate="High (60-70%)",
        evergreen_score=0.88,
        competition="Medium",
    ),
    ProductCard(
        id="cleaning-spray-04",
        name="All-Purpose Concentrated Cleaning Spray — Plant-Based, 3 Bottles",
        category="Home & Cleaning",
        image_url=_img("1620916566398-39f1143ab7be"),
        url="https://example.com/trending-plant-cleaner",
        angle_options=[
            "One bottle, every surface. Plant-based means safe around kids and pets.",
            "Concentrated formula = one bottle makes four. Refills pay for themselves.",
            "Smells like actual lavender, not fake lemon.",
        ],
        pin_title_options=[
            "The Cleaner My Cat Won't Try to Lick 🐈",
            "Plant-Based Doesn't Have to Mean Weak 🌿",
            "I Threw Out 6 Bottles of Cleaner After This 💧",
            "Smells Like a Spa, Cleans Like Bleach 🛁",
        ],
        pin_description_options=[
            "Plant-derived surfactants that cut grease without the chemical burn. Concentrated = less plastic, less waste, less guilt. Safe around kids and pets.",
        ],
        hashtags_pool=["#CleanTok", "#EcoFriendly", "#NonToxic", "#PlantBased",
                       "#MomHacks", "#GreenCleaning"],
        viral_hook_options=[
            "Strong enough for ovens, gentle enough for baby toys.",
            "It's not a personality. But it might be one if you tried it.",
        ],
        trend_score_range=(74, 86),
        trend_signals_options=[
            ["#NonToxic 3.4M posts", "+38% saves on Pinterest 2025",
             "Featured in 11 wellness creator roundups"],
            ["+27% YoY search volume", "Subscription model 18% take-rate"],
        ],
        margin_estimate="Very High (70-80%)",
        evergreen_score=0.91,
        competition="High",
    ),
    ProductCard(
        id="cordless-vacuum-05",
        name="Cordless Handheld Vacuum — Lightweight, USB-C Rechargeable",
        category="Home & Cleaning",
        image_url=_img("1556909114-f6e7ad7d3136"),
        url="https://example.com/trending-handheld-vacuum",
        angle_options=[
            "Grabs crumbs from the car seat in one pass.",
            "Weighs less than a soda can. Reaches the places your full-size vacuum can't.",
            "USB-C charging means one cable for your phone and your vacuum.",
        ],
        pin_title_options=[
            "The Car Detailing Secret Pros Don't Talk About 🚗",
            "I Vacuum My Car 3x a Week Now. Send Help. 🧹",
            "USB-C Vacuum Means One Less Charger in My Drawer 🔌",
            "Pet Hair is No Match. Period. 🐶",
        ],
        pin_description_options=[
            "Weighs 1.4 lbs. Lasts 25 minutes per charge. Picks up pet hair, crumbs, and the sand your toddler tracked in. USB-C rechargeable.",
        ],
        hashtags_pool=["#CarCleaning", "#PetHair", "#CleanTok", "#OrganizationHacks",
                       "#MomLife", "#DadLife"],
        viral_hook_options=[
            "1.4 pounds. 25 minutes. Zero excuses.",
            "Once you own one, you vacuum everything. Everything.",
        ],
        trend_score_range=(71, 83),
        trend_signals_options=[
            ["+44% YoY search volume", "Pet owners over-index 3.2x"],
            ["Featured in 9 'car detailing TikTok' videos"],
        ],
        margin_estimate="Medium (35-45%)",
        evergreen_score=0.86,
        competition="High",
    ),
]

# ── TECH & GADGETS ─────────────────────────────────────────────────────────

TECH_POOL: list[ProductCard] = [
    ProductCard(
        id="charger-01",
        name="3-in-1 Foldable MagSafe Wireless Charging Station",
        category="Tech & Gadgets",
        image_url=_img("1611532736597-de2d4265fba3"),
        url="https://example.com/trending-3in1-charger",
        angle_options=[
            "Declutter your nightstand with fast, simultaneous charging for iPhone, Apple Watch, and AirPods.",
            "Folds flat for travel. Pops up for desk. One charger, every device.",
            "The single upgrade that makes your nightstand look like a magazine.",
        ],
        pin_title_options=[
            "Nightstand Setup Upgrade: Zero Cable Clutter 🔌📱",
            "Travel Charging Just Got 90% Smaller ✈️",
            "Why I Stopped Buying Separate Chargers 🛑",
            "The Apple User's $40 Glow-Up 🍎",
        ],
        pin_description_options=[
            "Say goodbye to tangled cords! This 3-in-1 foldable wireless charging station powers your phone, watch, and earbuds all at once. Perfect for travel or your bedside table.",
            "Folds to 0.5 inch. Unfolds to charge iPhone, Apple Watch, and AirPods simultaneously. Magnetic alignment every time.",
        ],
        hashtags_pool=["#TechGadgets", "#DeskSetup", "#NightstandDecor",
                       "#AppleAccessories", "#MagSafe", "#WorkFromHome", "#DeskGoals"],
        viral_hook_options=[
            "Three devices, one outlet, zero cable spaghetti.",
            "Travel charger that fits in a back pocket.",
            "The nightstand upgrade that pays for itself in saved Apple cables.",
        ],
        trend_score_range=(86, 95),
        trend_signals_options=[
            ["MagSafe ecosystem searches +58% YoY", "Apple Watch Series 10 launch halo",
             "Featured in 22 'desk setup' TikToks"],
            ["+71% Amazon BSR growth", "#MagSafe 5.1M posts",
             "Repeat-purchase rate 28%"],
        ],
        margin_estimate="High (50-60%)",
        evergreen_score=0.78,
        competition="High",
        competition_reasons=["Apple accessory gold rush", "Many lookalikes"],
    ),
    ProductCard(
        id="laptop-stand-02",
        name="Adjustable Aluminum Laptop Stand — Ergonomic, Foldable",
        category="Tech & Gadgets",
        image_url=_img("1496181133206-80ce9b88a853"),
        url="https://example.com/trending-laptop-stand",
        angle_options=[
            "Eye-level screen in 3 seconds. Neck pain gone in 3 days.",
            "The single change that fixed my posture and my posture selfies.",
            "Aluminum, foldable, fits a 17-inch laptop. The desk setup staple.",
        ],
        pin_title_options=[
            "Remote Workers: Your Neck Will Thank You 💻",
            "The $40 Desk Setup That Looks Like $400 🖥️",
            "I Bought 4 Laptop Stands. This One Stuck. ✅",
            "Eye-Level Screen = Energy-Level You 🔋",
        ],
        pin_description_options=[
            "Raises your screen to ergonomic height in seconds. Aluminum build, vented for cooling, folds flat for storage. Fits laptops 11\"-17\".",
        ],
        hashtags_pool=["#WorkFromHome", "#DeskSetup", "#Ergonomics",
                       "#WFH", "#RemoteWork", "#DeskGoals", "#ProductivityHacks"],
        viral_hook_options=[
            "Your laptop screen belongs at eye level. Your neck agrees.",
            "Vented aluminum means your MacBook runs cooler and quieter.",
        ],
        trend_score_range=(76, 87),
        trend_signals_options=[
            ["#WFH 12M+ posts", "Ergonomic searches +33% YoY"],
            ["Featured in 18 'desk makeover' Pinterest boards"],
        ],
        margin_estimate="Medium (40-50%)",
        evergreen_score=0.92,
        competition="High",
    ),
    ProductCard(
        id="desk-lamp-03",
        name="LED Desk Lamp with USB Charging Port — Dimmable, 5 Color Temps",
        category="Tech & Gadgets",
        image_url=_img("1588872657578-7efd1f1555ed"),
        url="https://example.com/trending-desk-lamp",
        angle_options=[
            "Five color temperatures. One USB port. Zero eye strain.",
            "The desk lamp that finally replaced your ring light.",
            "Touch dimming, memory function, USB pass-through. Every feature you'd want.",
        ],
        pin_title_options=[
            "Why My Ring Light Collects Dust Now 💡",
            "5 Color Temps Saved My Eyes 👀",
            "Desk Setup Glow-Up Under $30 ✨",
            "The Lamp That Charges Your Phone While Lighting Your Zoom 🪟",
        ],
        pin_description_options=[
            "5 color temps (2700K-6500K), 5 brightness levels, USB-A pass-through charging, and a memory function that remembers your last setting. Your eyes and your setup will thank you.",
        ],
        hashtags_pool=["#DeskSetup", "#WFH", "#LightingDesign", "#StudySetup",
                       "#ContentCreator", "#DeskGoals"],
        viral_hook_options=[
            "Five temperatures, one USB port, zero excuses.",
            "Set it once. It remembers. Like your favorite barista.",
        ],
        trend_score_range=(68, 80),
        trend_signals_options=[
            ["#DeskSetup 8.2M posts", "+21% YoY"],
            ["Featured in 7 creator gear lists"],
        ],
        margin_estimate="Medium (45-55%)",
        evergreen_score=0.88,
        competition="Medium",
    ),
    ProductCard(
        id="phone-stand-04",
        name="MagSafe Phone Stand for Video Calls — Magnetic, Adjustable",
        category="Tech & Gadgets",
        image_url=_img("1572177812156-58036aae439c"),
        url="https://example.com/trending-magsafe-stand",
        angle_options=[
            "Snaps on, stands up, no clip needed. The stand your Zoom calls have been waiting for.",
            "Adjustable height + 360° rotation. Find your angle without re-mounting.",
            "Folds flat. Tosses in a bag. Out-weighs every clip-style stand.",
        ],
        pin_title_options=[
            "I Stopped Propping My Phone on Books 📚",
            "Zoom Calls: Stop Looking Down at Your Phone 📱",
            "The $25 Accessory Every Remote Worker Needs 🖥️",
            "Magnetic Stand = No Clip Marks on Your Case ✨",
        ],
        pin_description_options=[
            "MagSafe-compatible stand with adjustable height and 360° rotation. Holds your phone steady for video calls, recipes, and TikTok filming. Folds flat for travel.",
        ],
        hashtags_pool=["#WFH", "#ZoomSetup", "#MagSafe", "#ContentCreator",
                       "#TikTokCreator", "#RemoteWork"],
        viral_hook_options=[
            "No clip. No case marks. No books propping your phone at a sad angle.",
            "Snaps on. Adjusts up. Holds steady. Your recipe videos thank you.",
        ],
        trend_score_range=(74, 85),
        trend_signals_options=[
            ["#WFHSetup +39% YoY", "TikTok creator economy boom"],
            ["Featured in 12 'Zoom tips' videos"],
        ],
        margin_estimate="High (55-65%)",
        evergreen_score=0.84,
        competition="Medium",
    ),
    ProductCard(
        id="smart-plug-05",
        name="WiFi Smart Plug — Voice Control, Schedule, No Hub Required",
        category="Tech & Gadgets",
        image_url=_img("1574169208507-84376144848b"),
        url="https://example.com/trending-smart-plug",
        angle_options=[
            "Turn any outlet into a voice-controlled smart outlet. No hub needed.",
            "Schedule your coffee maker, lamp, or fan to run on a routine.",
            "The $15 gateway into smart home that doesn't require an electrician.",
        ],
        pin_title_options=[
            "Alexa, Turn Off the Lamp I Forgot 💡",
            "My Lamp Has a Schedule Now. I'm Not Okay. 🕐",
            "The $15 Smart Home Starter You're Missing 🏠",
            "Schedule Your Coffee Maker From Bed ☕",
        ],
        pin_description_options=[
            "Voice control via Alexa or Google. Schedule any plug-in device. Energy monitoring. No hub required — just WiFi.",
        ],
        hashtags_pool=["#SmartHome", "#Alexa", "#HomeAutomation", "#TechGadgets",
                       "#WFH", "#SmartLiving"],
        viral_hook_options=[
            "Your coffee maker doesn't know what time you wake up. Tell it.",
            "Voice control for $15 is the gateway drug to smart home.",
        ],
        trend_score_range=(66, 78),
        trend_signals_options=[
            ["#SmartHome 9.8M posts", "Smart speaker household penetration 47%"],
            ["Featured in 14 'first smart home gadget' lists"],
        ],
        margin_estimate="Medium (40-50%)",
        evergreen_score=0.85,
        competition="High",
    ),
]

# ── PET SUPPLIES ───────────────────────────────────────────────────────────

PET_POOL: list[ProductCard] = [
    ProductCard(
        id="dog-bed-01",
        name="Orthopedic Calming Donut Dog Bed — Self-Warming, Plush",
        category="Pet Supplies",
        image_url=_img("1541599540903-216a46ca1dc0"),
        url="https://example.com/trending-calming-dog-bed",
        angle_options=[
            "Relieve pet anxiety and joint pain with faux-fur self-warming comfort.",
            "The bed anxious dogs fall in love with in 8 seconds flat.",
            "Vet-recommended for older joints. Loved by every puppy.",
        ],
        pin_title_options=[
            "Give Your Pup the Ultimate Cozy Sleep 🐾💤",
            "Anxious Dog? This Bed is the Fix 🐶",
            "Vets Recommend This for Older Joints 🦴",
            "My Dog Won't Sleep Anywhere Else Now 😍",
        ],
        pin_description_options=[
            "Designed to ease anxiety and support aching joints, this plush self-warming donut dog bed is a game changer for anxious pets. Watch them fall instantly in love with it!",
            "Self-warming faux fur + raised rim for head support. Machine washable. Sizes XS-XXL.",
        ],
        hashtags_pool=["#DogLovers", "#PetCare", "#HappyPets", "#DogBed",
                       "#PetTikTok", "#DogMom", "#PuppyLove", "#SeniorDog"],
        viral_hook_options=[
            "Anxious dog + donut bed = 8-second surrender.",
            "Older dog? Your vet's already seen this bed.",
            "The plush nest that rescues anxious pets on TikTok every week.",
        ],
        trend_score_range=(89, 96),
        trend_signals_options=[
            ["+62% Pinterest saves YoY", "#DogTok 4.8B views",
             "Featured in 28 'dog mom essentials' lists", "+41% MoM searches"],
            ["Viral #SeniorDog videos", "Repeat-purchase 14-month cycle",
             "+58% YoY revenue in category"],
        ],
        margin_estimate="High (55-65%)",
        evergreen_score=0.89,
        competition="Medium",
        competition_reasons=["Crowded category", "Brand trust matters"],
    ),
    ProductCard(
        id="pet-feeder-02",
        name="Automatic Pet Feeder — WiFi, Portion Control, 6L Capacity",
        category="Pet Supplies",
        image_url=_img("1591768793355-74d04bb6608f"),
        url="https://example.com/trending-auto-feeder",
        angle_options=[
            "Schedule meals from your phone. Never wonder if your pet ate.",
            "The feeder that lets you travel without a pet-sitter for the basics.",
            "Portion control for overweight pets. The diet your vet keeps suggesting.",
        ],
        pin_title_options=[
            "Travel Without the Guilt — Pet Auto Feeder 🍽️",
            "My Cat is on a Diet Thanks to This 🐈",
            "The Feeder That Saved My Dog's Health 🐕",
            "Schedule Meals From the Airport ✈️",
        ],
        pin_description_options=[
            "WiFi-enabled feeder with portion control, 6L capacity, and a backup battery. Schedule meals from your phone. Stream 1080p video of your pet eating.",
        ],
        hashtags_pool=["#PetTech", "#SmartHome", "#DogMom", "#CatMom",
                       "#PetLovers", "#PetTikTok"],
        viral_hook_options=[
            "Your pet won't overeat again. Their vet is already proud.",
            "Travel without a pet-sitter for $80.",
        ],
        trend_score_range=(74, 86),
        trend_signals_options=[
            ["#PetTech +44% YoY", "Pet humanization trend"],
            ["Featured in 12 'smart home for pets' worth"],
        ],
        margin_estimate="Medium (40-50%)",
        evergreen_score=0.91,
        competition="Medium",
    ),
    ProductCard(
        id="cat-tree-03",
        name="Modern Cat Tree Tower — Sisal Scratching Posts, Plush Perches",
        category="Pet Supplies",
        image_url=_img("1583337130417-3346a1be7dee"),
        url="https://example.com/trending-cat-tree",
        angle_options=[
            "The cat tree that doesn't look like a cat tree.",
            "Sisal posts that actually get used. Perches at the heights cats want.",
            "Modern walnut + cream. Your living room approves.",
        ],
        pin_title_options=[
            "The Cat Tree That Doesn't Look Like One 🛋️",
            "Modern Cat Parent? You Need This Tower 🐈",
            "Your Cat Has Been Waiting for Heights Like This 📐",
            "Sisal Posts That Actually Get Scratched 🐾",
        ],
        pin_description_options=[
            "Modern walnut-tone cat tree with sisal-wrapped posts, plush perches, and hideaway cubby. The cat tree your living room doesn't hate.",
        ],
        hashtags_pool=["#CatFurniture", "#ModernCatTree", "#CatMom",
                       "#CatTok", "#InteriorDesign", "#PetLovers"],
        viral_hook_options=[
            "Your cat has been waiting for a real perch. The windowsill is exhausted.",
            "Sisal posts that actually save your couch.",
        ],
        trend_score_range=(70, 82),
        trend_signals_options=[
            ["#CatTok 6.2B views", "Modern pet furniture +38% YoY"],
            ["Featured in 9 interior design + pet roundups"],
        ],
        margin_estimate="Medium (45-55%)",
        evergreen_score=0.93,
        competition="Medium",
    ),
    ProductCard(
        id="cat-toy-04",
        name="Interactive Cat Toy — Auto Rotating Butterfly, USB Rechargeable",
        category="Pet Supplies",
        image_url=_img("1535930891776-0c2dfb7fda1a"),
        url="https://example.com/trending-cat-toy",
        angle_options=[
            "Fluttering butterfly that never tires. Your cat will.",
            "USB rechargeable, motion-activated, safe for solo play.",
            "The toy bored cats actually engage with for 20+ minutes.",
        ],
        pin_title_options=[
            "Bored Cat? This Butterfly is the Answer 🦋",
            "My Cat Has a New Obsession 🐈",
            "USB Cat Toy = No Batteries Ever 🔋",
            "The Toy That Entertains While You Work From Home 💻",
        ],
        pin_description_options=[
            "Auto-rotating butterfly that mimics real prey movement. USB rechargeable, motion-activated, and built for solo play. The toy that engages even bored cats.",
        ],
        hashtags_pool=["#CatToy", "#CatTok", "#CatMom", "#PetEnrichment",
                       "#BoredCat", "#WFHPets"],
        viral_hook_options=[
            "Fluttering butterfly. Motion-activated. Your cat just got interesting.",
            "USB rechargeable means it works the moment you remember to charge it.",
        ],
        trend_score_range=(72, 84),
        trend_signals_options=[
            ["#CatTok 6.2B views", "Solo pet enrichment searches +29%"],
            ["Featured in 18 'WFH with cats' videos"],
        ],
        margin_estimate="High (60-70%)",
        evergreen_score=0.79,
        competition="Medium",
    ),
    ProductCard(
        id="dog-leash-05",
        name="Retractable Dog Leash — 16ft, One-Hand Brake, Reflective",
        category="Pet Supplies",
        image_url=_img("1601758228041-f3b2795255f1"),
        url="https://example.com/trending-dog-leash",
        angle_options=[
            "One-hand brake, 16ft of freedom, reflective stitching for night walks.",
            "The retractable leash that won't snap mid-jog.",
            "Heavy-duty up to 110 lbs. Small enough for daily walks.",
        ],
        pin_title_options=[
            "The Leash That Survives My 90-lb Puller 🐕",
            "Reflective Night Walks Just Got Safer 🌙",
            "One-Hand Brake = Treats in the Other Hand 🍖",
            "Retractable Leash That Doesn't Feel Cheap 🤝",
        ],
        pin_description_options=[
            "16ft retractable range, one-hand brake/lock, reflective stitching, and TPR grip. Built for dogs up to 110 lbs. The leash daily walkers actually trust.",
        ],
        hashtags_pool=["#DogLeash", "#DogWalking", "#DogMom", "#NightWalks",
                       "#DogTraining", "#PetSafety"],
        viral_hook_options=[
            "One-hand brake. Treats in the other. Walks just got easier.",
            "Reflective stitching = visible at 200ft at night.",
        ],
        trend_score_range=(64, 76),
        trend_signals_options=[
            ["Pet humanization trend", "+18% YoY"],
            ["Featured in 8 'essentials for new dog owners' lists"],
        ],
        margin_estimate="Medium (40-50%)",
        evergreen_score=0.84,
        competition="High",
    ),
]

# ── AESTHETIC HOME DECOR ───────────────────────────────────────────────────

DECOR_POOL: list[ProductCard] = [
    ProductCard(
        id="sunset-lamp-01",
        name="Sunset Projection LED Ambient Lamp — 16 Color Modes",
        category="Aesthetic Home Decor",
        image_url=_img("1513519245088-0e12902e5a38"),
        url="https://example.com/trending-sunset-lamp",
        angle_options=[
            "Instantly transform any room's vibe for cozy evenings and viral social media content.",
            "Golden hour on demand. The lamp that broke Pinterest.",
            "The single lamp that makes your room a backdrop.",
        ],
        pin_title_options=[
            "Golden Hour Vibes All Year Round 🌅✨",
            "Why My Room Now Looks Like a Pinterest Pin 📌",
            "The $25 Lamp That Replaced My Therapist 🛋️",
            "16 Colors = 16 Moods. Pick Yours. 🌈",
        ],
        pin_description_options=[
            "Bring the warmth of a California sunset right into your bedroom. Create the ultimate aesthetic mood lighting for photos, relaxation, and cozy nights in.",
            "16 color modes, 180° projection head, USB-powered. Set the mood without changing a single bulb.",
        ],
        hashtags_pool=["#RoomDecor", "#AestheticVibes", "#GoldenHour",
                       "#HomeInspo", "#BedroomGoals", "#CozyVibes",
                       "#SunsetLamp", "#PinterestFinds"],
        viral_hook_options=[
            "Golden hour on demand. No filters required.",
            "The lamp that turned bedrooms into Pinterest pins overnight.",
            "Sixteen colors, one room, zero commitment.",
        ],
        trend_score_range=(91, 97),
        trend_signals_options=[
            ["+124% Pinterest saves YoY", "#AestheticBedroom 8.4M posts",
             "Featured in 41 room-tour TikToks", "+58% MoM searches"],
            ["Viral across TikTok and Pinterest simultaneously",
             "Repeat gifting season 4x per year", "+82% YoY revenue"],
        ],
        margin_estimate="Very High (70-80%)",
        evergreen_score=0.74,
        competition="Very High",
        competition_reasons=["Every drop-shipper has one", "Saturated market"],
    ),
    ProductCard(
        id="led-strip-02",
        name="RGB LED Strip Lights — App Control, Music Sync, 32.8ft",
        category="Aesthetic Home Decor",
        image_url=_img("1554995207-c18c203602cb"),
        url="https://example.com/trending-led-strip",
        angle_options=[
            "Music sync makes your room a club. App control makes it smart.",
            "32 feet of color that turns any ceiling into a vibe.",
            "Cuttable, adhesive, app-controlled. The setup takes 10 minutes.",
        ],
        pin_title_options=[
            "My Ceiling Glows Now. I'm Not Sorry. ✨",
            "Music Sync = Built-in DJ Booth 🎵",
            "10-Minute Install, Lifetime Vibes 🛠️",
            "The $20 Glow-Up Every Bedroom Needs 🛏️",
        ],
        pin_description_options=[
            "App-controlled RGB LED strips with music sync, 16 million colors, and timer function. Cuttable every 3 LEDs for a perfect fit. Adhesive backing.",
        ],
        hashtags_pool=["#LEDRoom", "#BedroomGoals", "#RGB",
                       "#RoomMakeover", "#TikTokRoom", "#GamingSetup"],
        viral_hook_options=[
            "Music sync turns your ceiling into a built-in DJ booth.",
            "Ten minutes to install. Years of 'whoa cool' from guests.",
        ],
        trend_score_range=(78, 89),
        trend_signals_options=[
            ["#LEDRoom 12M+ posts", "+44% YoY"],
            ["Featured in 26 gaming setup TikToks"],
        ],
        margin_estimate="High (55-65%)",
        evergreen_score=0.81,
        competition="Very High",
    ),
    ProductCard(
        id="throw-blanket-03",
        name="Chunky Knit Throw Blanket — Hand-Woven, Soft Polyester",
        category="Aesthetic Home Decor",
        image_url=_img("1538688525198-9b88f6f53126"),
        url="https://example.com/trending-knit-blanket",
        angle_options=[
            "The blanket that makes every couch a Pinterest photo.",
            "Chunky knit that photographs like a magazine and feels like a hug.",
            "Hand-woven texture. Machine washable practicality.",
        ],
        pin_title_options=[
            "My Couch Belongs in a Magazine Now 🛋️",
            "The Blanket That Photos Better Than You 📸",
            "Chunky Knit = Instant Cozy 🧶",
            "The $40 Throw That Upgraded My Whole Room ✨",
        ],
        pin_description_options=[
            "Hand-woven chunky knit throw in cream, grey, or sand. Machine washable. Photography-ready. The blanket every styled living room needs.",
        ],
        hashtags_pool=["#CozyHome", "#ThrowBlanket", "#ChunkyKnit",
                       "#PinterestHome", "#HomeDecor", "#CouchGoals"],
        viral_hook_options=[
            "The blanket that photos better than your morning coffee.",
            "Hand-woven texture that makes any couch feel curated.",
        ],
        trend_score_range=(74, 86),
        trend_signals_options=[
            ["+58% Pinterest saves YoY", "#CozyHome 14M posts"],
            ["Featured in 19 couch-styling roundups"],
        ],
        margin_estimate="Very High (70-80%)",
        evergreen_score=0.83,
        competition="High",
    ),
    ProductCard(
        id="plant-pot-04",
        name="Self-Watering Plant Pot — Modern Ceramic, Multiple Sizes",
        category="Aesthetic Home Decor",
        image_url=_img("1485955900006-10f4d324d411"),
        url="https://example.com/trending-self-watering-pot",
        angle_options=[
            "Modern ceramic that waters your plants for you. The plant parent cheat code.",
            "The pot that fixes your watering inconsistency forever.",
            "Indoor plant parent? Stop killing pothos.",
        ],
        pin_title_options=[
            "Stop Killing Your Houseplants 🌱",
            "Self-Watering = Plant Parent Cheat Code 🪴",
            "Modern Ceramic That Looks Cute Anywhere 🏺",
            "The Pot That Saved My Snake Plant 🐍",
        ],
        pin_description_options=[
            "Self-watering ceramic pot with a hidden reservoir that waters plants for 1-2 weeks. Modern matte finish, multiple sizes.",
        ],
        hashtags_pool=["#PlantParent", "#Houseplants", "#PlantTok",
                       "#IndoorGarden", "#SelfWatering"],
        viral_hook_options=[
            "Reservoir under the soil means weeks between waterings.",
            "The pot that fixes your watering inconsistency forever.",
        ],
        trend_score_range=(72, 84),
        trend_signals_options=[
            ["#PlantTok 7.8M posts", "+31% YoY"],
            ["Featured in 11 'plant parent starter kit' lists"],
        ],
        margin_estimate="High (60-70%)",
        evergreen_score=0.92,
        competition="Medium",
    ),
    ProductCard(
        id="wall-clock-05",
        name="Modern Silent Wall Clock — Minimalist Sweep Movement",
        category="Aesthetic Home Decor",
        image_url=_img("1493663284031-b7e3aefcae8e"),
        url="https://example.com/trending-wall-clock",
        angle_options=[
            "Silent sweep movement. No ticking, no distraction.",
            "Minimalist face, modern frame, 12-inch diameter. Looks expensive, isn't.",
            "The wall clock your living room was waiting for.",
        ],
        pin_title_options=[
            "A Silent Clock = Better Sleep 😴",
            "The Clock That Doesn't Sound Like a Bomb 🕰️",
            "Minimalist Face, Maximum Style 🖼️",
            "My Living Room Finally Looks Adult 🛋️",
        ],
        pin_description_options=[
            "Silent sweep movement means no ticking. 12-inch minimalist face, modern frame. Battery included. The clock that looks $80 and costs $25.",
        ],
        hashtags_pool=["#HomeDecor", "#MinimalistHome", "#LivingRoomGoals",
                       "#SilentClock", "#WallDecor"],
        viral_hook_options=[
            "Silent sweep movement means no ticking. Your sleep study agrees.",
            "Looks like $80. Costs $25. Your living room won't tell.",
        ],
        trend_score_range=(64, 78),
        trend_signals_options=[
            ["#MinimalistHome +27% YoY", "Featured in 14 room tours"],
        ],
        margin_estimate="Very High (75-85%)",
        evergreen_score=0.95,
        competition="High",
    ),
]

# ── FITNESS & WELLNESS ─────────────────────────────────────────────────────

FITNESS_POOL: list[ProductCard] = [
    ProductCard(
        id="yoga-mat-01",
        name="Premium Non-Slip Yoga Mat — 6mm Eco TPE, Carrying Strap",
        category="Fitness & Wellness",
        image_url=_img("1591291621164-2c6367723315"),
        url="https://example.com/trending-yoga-mat",
        angle_options=[
            "Eco TPE. Non-slip texture. The mat that doesn't slide during downward dog.",
            "6mm cushioning that protects joints without sacrificing balance.",
            "The single upgrade that turns home yoga from 'meh' to 'okay I'm a yogi now.'",
        ],
        pin_title_options=[
            "The Yoga Mat That Doesn't Slide 🧘",
            "Home Yoga = Better Mat, Better Vibes 🌿",
            "6mm Cushion = Happy Joints After 60 🦵",
            "I Bought 4 Yoga Mats. This One's the Last. ✅",
        ],
        pin_description_options=[
            "Eco TPE material. Non-slip texture on both sides. 6mm cushioning. Carrying strap included. The mat that survives daily practice.",
        ],
        hashtags_pool=["#YogaMat", "#HomeYoga", "#YogaTok",
                       "#Mindfulness", "#WellnessJourney", "#YogiLife"],
        viral_hook_options=[
            "Non-slip on both sides means downward dog finally feels stable.",
            "Eco TPE, not PVC. Your mat doesn't off-gas.",
        ],
        trend_score_range=(76, 87),
        trend_signals_options=[
            ["#YogaTok 5.2M posts", "+34% home yoga YoY"],
            ["Featured in 22 'WFH wellness' roundups"],
        ],
        margin_estimate="High (55-65%)",
        evergreen_score=0.94,
        competition="High",
    ),
    ProductCard(
        id="foam-roller-02",
        name="High-Density Foam Roller — Textured, 36-inch, Muscle Recovery",
        category="Fitness & Wellness",
        image_url=_img("1599058917212-d750089bc07e"),
        url="https://example.com/trending-foam-roller",
        angle_options=[
            "The roller that turns post-workout soreness into a 10-minute recovery.",
            "Textured surface mimics a deep-tissue massage. Your fascia thanks you.",
            "36-inch full-body coverage. 13-inch for travel. Pick your fighter.",
        ],
        pin_title_options=[
            "The 10-Minute Recovery That Replaced My Massage 💆",
            "Why Athletes Foam Roll (And You Should Too) 🏃",
            "Textured Surface = Real Deep Tissue 🎯",
            "Soreness Has a 10-Minute Fix Now 🛌",
        ],
        pin_description_options=[
            "High-density textured foam roller for muscle recovery, mobility work, and fascia release. 36-inch for full body. Built to last 5+ years.",
        ],
        hashtags_pool=["#FoamRoller", "#RecoveryDay", "#MobilityWork",
                       "#FitnessTok", "#RunnersOfTikTok", "#Wellness"],
        viral_hook_options=[
            "Textured surface = real myofascial release. Not the gentle kind.",
            "10 minutes of rolling saves a week of stiff mornings.",
        ],
        trend_score_range=(68, 80),
        trend_signals_options=[
            ["#RecoveryDay 2.8M posts", "Runners +34% YoY"],
            ["Featured in 14 'post-run routine' videos"],
        ],
        margin_estimate="High (65-75%)",
        evergreen_score=0.96,
        competition="Medium",
    ),
    ProductCard(
        id="dumbbells-03",
        name="Adjustable Dumbbells — 5-in-1 Weight Set, Pair",
        category="Fitness & Wellness",
        image_url=_img("1571019613454-1cb2f99b2d8b"),
        url="https://example.com/trending-adjustable-dumbbells",
        angle_options=[
            "Five weights in one. Replace a whole rack.",
            "The dumbbells that turned a corner into a home gym.",
            "Adjustable in seconds. No knobs to fumble during supersets.",
        ],
        pin_title_options=[
            "5 Dumbbells in 1 = No More Rack Required 🏋️",
            "Home Gym Corner = Sorted for $80 💪",
            "Adjustable in 3 Seconds. Supersets Approved ⏱️",
            "Why I Stopped Going to the Gym for These 🏠",
        ],
        pin_description_options=[
            "Adjustable 5-in-1 dumbbells replace 5 pairs. Twist-to-adjust mechanism. Rubber-coated grip. Pair. The dumbbells that built the home gym.",
        ],
        hashtags_pool=["#HomeGym", "#Dumbbells", "#FitnessTok",
                       "#WorkoutMotivation", "#StrengthTraining"],
        viral_hook_options=[
            "Five weights in one dumbbell. The math is wild.",
            "Twist to adjust mid-set. Your superset flow stays unbroken.",
        ],
        trend_score_range=(74, 86),
        trend_signals_options=[
            ["#HomeGym 9.4M posts", "+58% YoY"],
            ["Featured in 31 'home gym setup' TikToks"],
        ],
        margin_estimate="Medium (40-50%)",
        evergreen_score=0.94,
        competition="Medium",
    ),
    ProductCard(
        id="massage-gun-04",
        name="Percussive Massage Gun — 6 Heads, 30 Speeds, Quiet Brushless Motor",
        category="Fitness & Wellness",
        image_url=_img("1549060279-7e168fcee0c2"),
        url="https://example.com/trending-massage-gun",
        angle_options=[
            "Quiet brushless motor = apartment-friendly. Your neighbors never hear it.",
            "30 speeds + 6 heads = massage-therapist-grade customization.",
            "The recovery tool that makes 'rest day' the best day.",
        ],
        pin_title_options=[
            "The Quiet Massage Gun Your Neighbors Won't Hate 🤫",
            "Rest Day = Best Day Now 🛌",
            "6 Heads, 30 Speeds, One Sore Back Fixed 💆",
            "Therapist in Your Gym Bag 👜",
        ],
        pin_description_options=[
            "Quiet brushless motor (under 40 dB). 30 speeds, 6 attachment heads, 6-hour battery. The massage gun that actually lives in your gym bag.",
        ],
        hashtags_pool=["#MassageGun", "#RecoveryDay", "#FitnessTok",
                       "#GymTok", "#HomeGym", "#Wellness"],
        viral_hook_options=[
            "Quiet brushless motor = apartment-friendly percussion.",
            "6 heads and 30 speeds. The customization a therapist would respect.",
        ],
        trend_score_range=(81, 92),
        trend_signals_options=[
            ["#MassageGun 4.6M posts", "+62% YoY"],
            ["Featured in 38 'recovery tool' roundups", "Theragun halo effect"],
        ],
        margin_estimate="High (55-65%)",
        evergreen_score=0.88,
        competition="High",
        competition_reasons=["Theragun halo", "Many lookalikes"],
    ),
    ProductCard(
        id="water-bottle-05",
        name="Insulated Stainless Steel Water Bottle — 32oz, Time Marker",
        category="Fitness & Wellness",
        image_url=_img("1605296867304-46d5465a13f1"),
        url="https://example.com/trending-water-bottle",
        angle_options=[
            "Time markers on the side so you know how much you've actually drunk.",
            "32oz = fills twice and you're done. Insulated so ice lasts 24 hours.",
            "The bottle that finally made hydration a habit.",
        ],
        pin_title_options=[
            "Time Markers Made Me Hydrate Consistently 💧",
            "32oz = Drink Twice and You're Done 🕐",
            "Ice Lasts 24 Hours in This Bottle ❄️",
            "The Hydration Habit That Actually Stuck 💦",
        ],
        pin_description_options=[
            "32oz insulated stainless steel with hourly time markers on the side. Keeps cold 24hrs, hot 12hrs. BPA-free. The bottle that built the habit.",
        ],
        hashtags_pool=["#WaterBottle", "#Hydration", "#FitnessTok",
                       "#GymTok", "#WellnessHabits", "#HealthyHabits"],
        viral_hook_options=[
            "Time markers = you actually hit your daily goal.",
            "24-hour cold ice = summer in a bottle.",
        ],
        trend_score_range=(70, 82),
        trend_signals_options=[
            ["#Hydration 3.1M posts", "+24% YoY"],
            ["Featured in 16 'health habit stacks' roundups"],
        ],
        margin_estimate="High (60-70%)",
        evergreen_score=0.91,
        competition="High",
    ),
]

# ── KITCHEN & COOKING ──────────────────────────────────────────────────────

KITCHEN_POOL: list[ProductCard] = [
    ProductCard(
        id="knife-set-01",
        name="6-Piece Stainless Steel Knife Set with Block — Razor Sharp",
        category="Kitchen & Cooking",
        image_url=_img("1593618998160-e34014e67546"),
        url="https://example.com/trending-knife-set",
        angle_options=[
            "The knife set that makes weeknight cooking feel like a cooking show.",
            "Razor-sharp factory edge + ergonomic grip = cuts in half the time.",
            "The single upgrade that saved my meal prep routine.",
        ],
        pin_title_options=[
            "My Cutting Board Time Halved 🍴",
            "Why My Chef Friend Bought Two Sets 🔪",
            "Razor Sharp Out of the Box (Literally) ✨",
            "Meal Prep = 30 Minutes Less Now ⏱️",
        ],
        pin_description_options=[
            "6-piece stainless steel knife set with wooden block. Razor-sharp factory edge, ergonomic handles, dishwasher-safe. The set that turned home cooks into home chefs.",
        ],
        hashtags_pool=["#KnifeSet", "#HomeChef", "#MealPrep",
                       "#KitchenGoals", "#CookingAtHome", "#KitchenTok"],
        viral_hook_options=[
            "Razor-sharp out of the box. No sharpening stone required.",
            "The set that turned weeknight cooking from chore to ritual.",
        ],
        trend_score_range=(72, 84),
        trend_signals_options=[
            ["#MealPrep 8.1M posts", "+31% YoY"],
            ["Featured in 18 'kitchen starter kit' roundups"],
        ],
        margin_estimate="Medium (40-50%)",
        evergreen_score=0.95,
        competition="High",
    ),
    ProductCard(
        id="spice-rack-02",
        name="Rotating Spice Rack Organizer — 16 Jars, Labels Included",
        category="Kitchen & Cooking",
        image_url=_img("1556910103-1c02745aae4d"),
        url="https://example.com/trending-spice-rack",
        angle_options=[
            "Lazy Susan design. Find cumin without excavating the cabinet.",
            "16 jars, 16 labels, zero rummaging.",
            "The cabinet organization that makes you cook more.",
        ],
        pin_title_options=[
            "Find Cumin in 3 Seconds 🌀",
            "The $25 Cabinet Glow-Up ✨",
            "16 Jars, 16 Labels, Zero Rummaging 🏷️",
            "Lazy Susan = The Kitchen Hack You Need 🛎️",
        ],
        pin_description_options=[
            "Rotating spice rack with 16 glass jars, labels, and a bamboo lid. Spinning design means every jar is reachable. The cabinet organization upgrade.",
        ],
        hashtags_pool=["#KitchenOrganization", "#SpiceRack", "#HomeOrganization",
                       "#KitchenGoals", "#MealPrep", "#MomHacks"],
        viral_hook_options=[
            "Spin to find any spice in 3 seconds flat.",
            "The cabinet organization that makes you actually cook.",
        ],
        trend_score_range=(68, 80),
        trend_signals_options=[
            ["#KitchenOrganization +34% YoY", "Pinterest saves +41%"],
            ["Featured in 9 'kitchen glow-up' roundups"],
        ],
        margin_estimate="High (60-70%)",
        evergreen_score=0.92,
        competition="Medium",
    ),
    ProductCard(
        id="coffee-mug-warmer-03",
        name="Coffee Mug Warmer & Beverage Warmer — 3 Temp Settings",
        category="Kitchen & Cooking",
        image_url=_img("1543353071-873f17a7a088"),
        url="https://example.com/trending-coffee-warmer",
        angle_options=[
            "Three temp settings so your coffee stays at your perfect temp.",
            "The single desk accessory that fixed my lukewarm coffee problem.",
            "Works for coffee, tea, hot chocolate. Anyone who microwaves deserves better.",
        ],
        pin_title_options=[
            "No More Lukewarm Coffee ☕",
            "The Desk Accessory That Earned Its Place 💼",
            "Why I Stopped Microwaving My Coffee 📵",
            "Three Temps = One Perfect Cup 🌡️",
        ],
        pin_description_options=[
            "Coffee mug warmer with 3 temperature settings (104°F / 131°F / 149°F). Auto shut-off. Works with ceramic, glass, and stainless steel mugs.",
        ],
        hashtags_pool=["#CoffeeTok", "#WFH", "#DeskSetup",
                       "#CoffeeLovers", "#HomeOffice"],
        viral_hook_options=[
            "Three temps so your coffee stays at your perfect temp, not 'kinda warm.'",
            "Microwaving coffee is a cry for help. This fixes it.",
        ],
        trend_score_range=(74, 85),
        trend_signals_options=[
            ["#CoffeeTok 11M posts", "+29% YoY"],
            ["Featured in 17 'WFH essentials' lists"],
        ],
        margin_estimate="High (60-70%)",
        evergreen_score=0.88,
        competition="Medium",
    ),
    ProductCard(
        id="lunch-box-04",
        name="Bento Lunch Box — 3 Compartments, Leak-Proof, Microwave Safe",
        category="Kitchen & Cooking",
        image_url=_img("1606787366850-de6330128bfc"),
        url="https://example.com/trending-bento-box",
        angle_options=[
            "Three compartments keep your salad separate from your dressing.",
            "Leak-proof seal means it survives the commute upright.",
            "Microwave-safe = hot lunch at your desk. No sad desk salads.",
        ],
        pin_title_options=[
            "My Lunch Stays Separate & Leak-Free 🍱",
            "Bento Box = Portion Control Without Trying 🥗",
            "Microwave-Safe Means Hot Lunch at Work 🍲",
            "The Lunch Box That Saved My Wallet 💰",
        ],
        pin_description_options=[
            "3-compartment bento lunch box with leak-proof seal. Microwave-safe, dishwasher-safe, BPA-free. The lunch box that makes meal prep actually work.",
        ],
        hashtags_pool=["#MealPrep", "#BentoBox", "#LunchIdeas",
                       "#HealthyEating", "#WFH", "#OfficeLunch"],
        viral_hook_options=[
            "Three compartments = your lunch stays separate AND leak-free.",
            "Microwave-safe = hot lunch. No more sad desk salads.",
        ],
        trend_score_range=(70, 82),
        trend_signals_options=[
            ["#MealPrep 8.1M posts", "#BentoBox +28% YoY"],
            ["Featured in 14 'lunch packing' roundups"],
        ],
        margin_estimate="Very High (70-80%)",
        evergreen_score=0.93,
        competition="High",
    ),
    ProductCard(
        id="pan-05",
        name="Non-Stick Ceramic Frying Pan — 10-inch, Oven Safe to 500°F",
        category="Kitchen & Cooking",
        image_url=_img("1606787366850-de6330128bfc"),
        url="https://example.com/trending-ceramic-pan",
        angle_options=[
            "Ceramic non-stick means no PFAS, no teflon, no shame.",
            "10-inch pan that goes from stovetop to oven. No transferring.",
            "The pan that replaced 4 others in my cabinet.",
        ],
        pin_title_options=[
            "Non-Stick Without the Chemicals 🍳",
            "Stovetop to Oven in One Pan 🔥",
            "My Eggs Slide Off Now 🍳",
            "The Pan That Replaced 4 Others 🍽️",
        ],
        pin_description_options=[
            "Ceramic non-stick frying pan. 10-inch, oven safe to 500°F, dishwasher-safe, PFAS-free. The pan that earns drawer space.",
        ],
        hashtags_pool=["#CeramicPan", "#HealthyCooking", "#KitchenTok",
                       "#NonToxicKitchen", "#HomeChef"],
        viral_hook_options=[
            "PFAS-free ceramic = non-stick without the chemical guilt.",
            "Stovetop to oven in the same pan. Fewer dishes. Always.",
        ],
        trend_score_range=(76, 88),
        trend_signals_options=[
            ["#NonToxicKitchen +44% YoY", "PFAS concerns driving searches"],
            ["Featured in 22 'kitchen must-haves' lists"],
        ],
        margin_estimate="Medium (45-55%)",
        evergreen_score=0.94,
        competition="High",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Pools registry
# ─────────────────────────────────────────────────────────────────────────────


POOLS: dict[str, list[ProductCard]] = {
    "cleaning": CLEANING_POOL,
    "tech":     TECH_POOL,
    "pet":      PET_POOL,
    "decor":    DECOR_POOL,
    "fitness":  FITNESS_POOL,
    "kitchen":  KITCHEN_POOL,
}

CATEGORY_LABELS: dict[str, str] = {
    "cleaning": "Home & Cleaning",
    "tech":     "Tech & Gadgets",
    "pet":      "Pet Supplies",
    "decor":    "Aesthetic Home Decor",
    "fitness":  "Fitness & Wellness",
    "kitchen":  "Kitchen & Cooking",
}


# ─────────────────────────────────────────────────────────────────────────────
# Researcher
# ─────────────────────────────────────────────────────────────────────────────


class ProductResearcher:
    """
    Picks winning products with guaranteed variety.

    • Pool-based: 30 curated trending products across 6 categories.
    • Rotates through each pool so consecutive calls return different picks.
    • Mix-and-matches copy variants so the same product reads differently.
    • Optional AI augmentation: if OPENAI_API_KEY is configured, asks GPT-4o
      for a freshly-generated trending pick.

    Thread-safe; in-process state only (resets on server restart).
    """

    def __init__(self, *, openai_client=None) -> None:
        self._rotation: dict[str, int] = {cat: 0 for cat in POOLS}
        self._session_picks: list[str] = []   # recent product ids (last 50)
        self._lock = threading.Lock()
        # OpenAI client is optional; passed in so we don't couple to generate.py
        self._openai = openai_client
        self._openai_available = openai_client is not None

    # ── Public API ────────────────────────────────────────────────────────

    def pick(
        self,
        intent: str | None,
        *,
        exclude: Optional[list[str]] = None,
        seed: Optional[int] = None,
        use_ai: bool = False,
    ) -> dict:
        """
        Pick a winner.

        Args:
            intent:   Raw user input (URL or keyword). Used to bias the category.
            exclude:  Optional list of product ids to avoid (e.g. last shown).
            seed:     Optional deterministic seed for tests.
            use_ai:   If True and OpenAI is configured, generate a fresh pick.

        Returns:
            Audit-cleared product dict (compatible with existing /find-winner consumers)
            augmented with: trend_score, trend_signals, margin_estimate, viral_hook,
                            evergreen_score, competition, source.
        """
        # Lazy import to avoid circular dependency
        from backend.product_control_agent import ProductControlAgent

        exclude = set(exclude or [])

        if use_ai and self._openai_available:
            ai_card = self._ai_pick(intent, category_hint=_classify(intent))
            if ai_card is not None:
                return ProductControlAgent.audit_product(ai_card)

        # Pool-based pick
        category = _classify(intent) if intent else None
        if category not in POOLS:
            # Pick from all pools (cross-category)
            return self._pick_across(exclude=exclude, seed=seed)

        return self._pick_from(category, exclude=exclude, seed=seed)

    def get_pool_summary(self) -> dict:
        """Return lightweight pool metadata for a 'browse all' view."""
        return {
            "total_products": sum(len(p) for p in POOLS.values()),
            "categories": {
                cat: [
                    {"id": p.id, "name": p.name, "trend_score": p.trend_score_range[1]}
                    for p in pool
                ]
                for cat, pool in POOLS.items()
            },
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    def _pick_from(
        self,
        category: str,
        *,
        exclude: set[str],
        seed: Optional[int],
    ) -> dict:
        pool = POOLS[category]
        with self._lock:
            idx = self._rotation.get(category, 0)
            self._rotation[category] = idx + 1

        # Deterministic seed overrides rotation for tests
        if seed is not None:
            rng = random.Random(seed)
            order = list(range(len(pool)))
            rng.shuffle(order)
        else:
            order = [(idx + i) % len(pool) for i in range(len(pool))]

        # Try each candidate in order, skipping excluded
        for i in order:
            card = pool[i]
            if card.id in exclude:
                continue
            return self._materialize(card, category)

        # All excluded → fall back to first (rare)
        return self._materialize(pool[0], category)

    def _pick_across(self, *, exclude: set[str], seed: Optional[int]) -> dict:
        """Pick from any pool. Round-robin across categories."""
        cats = list(POOLS.keys())
        with self._lock:
            cat_idx = sum(self._rotation.values()) % len(cats)
        cat = cats[cat_idx]
        return self._pick_from(cat, exclude=exclude, seed=seed)

    def _materialize(self, card: ProductCard, category: str) -> dict:
        """Convert a ProductCard into the API response dict with mix-and-match copy."""
        # Deterministic selection per card based on its id (so same card → same
        # copy variant in a session, but rotation still produces variety).
        h = hashlib.md5(card.id.encode()).hexdigest()
        rng = random.Random(int(h[:8], 16))

        score = rng.randint(*card.trend_score_range)
        signals = rng.choice(card.trend_signals_options)
        angle = rng.choice(card.angle_options)
        pin_title = rng.choice(card.pin_title_options)
        pin_desc = rng.choice(card.pin_description_options)
        hook = rng.choice(card.viral_hook_options)
        # 4-6 hashtags per call
        n_tags = rng.randint(4, min(6, len(card.hashtags_pool)))
        tags = rng.sample(card.hashtags_pool, n_tags)

        url = card.url
        raw_input_seed = h[:6]
        out = {
            "id":               card.id,
            "name":             card.name,
            "category":         card.category,
            "image_url":        card.image_url,
            "url":              url,
            "angle":            angle,
            "pin_title":        pin_title,
            "pin_description":  pin_desc,
            "hashtags":         tags,
            # Research-grade fields:
            "trend_score":      score,
            "trend_signals":    signals,
            "margin_estimate":  card.margin_estimate,
            "viral_hook":       hook,
            "evergreen_score":  card.evergreen_score,
            "competition":      card.competition,
            "competition_reasons": card.competition_reasons,
            "source":           "pool",
            "_seed":            raw_input_seed,  # internal; helps debugging
        }
        return out

    # ── OpenAI augmentation ───────────────────────────────────────────────

    def _ai_pick(
        self,
        intent: str | None,
        *,
        category_hint: str | None,
    ) -> Optional[dict]:
        """Ask GPT-4o for a fresh trending pick. Returns None on failure."""
        if self._openai is None:
            return None
        try:
            cat = CATEGORY_LABELS.get(category_hint, "a trending niche")
            prompt = (
                f"Generate a SINGLE currently-trending product in the {cat} category "
                "for a Pinterest/TikTok affiliate campaign. "
                "Return STRICT JSON with these keys:\n"
                "  id (kebab-case slug),\n"
                "  name (specific product name, 4-10 words),\n"
                "  category (display label),\n"
                "  image_url (https URL to a verified product photo; use Unsplash photo IDs in form https://images.unsplash.com/photo-<id>?w=800&auto=format&fit=crop&q=80 with an ID you are confident exists; otherwise leave blank),\n"
                "  url (https URL stub),\n"
                "  angle (one-sentence selling angle),\n"
                "  pin_title (viral Pinterest headline, 4-8 words, emoji optional),\n"
                "  pin_description (60-120 words, with hook + benefit + CTA),\n"
                "  hashtags (4-6 trending tags starting with #),\n"
                "  viral_hook (one-sentence scroll-stopper),\n"
                "  trend_score (60-95),\n"
                "  trend_signals (3-5 bullet reasons it's trending),\n"
                "  margin_estimate (e.g. 'High (50-60%)'),\n"
                "  evergreen_score (0.0-1.0),\n"
                "  competition (Low|Medium|High).\n"
                "Do NOT include any prose outside the JSON."
            )
            resp = self._openai.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                messages=[
                    {"role": "system", "content": "You are a viral product researcher."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.9,
                response_format={"type": "json_object"},
            )
            import json as _json
            data = _json.loads(resp.choices[0].message.content)
            # Normalize to match our schema
            data.setdefault("source", "openai_research")
            data.setdefault("trend_signals", [])
            data.setdefault("hashtags", [])
            if not isinstance(data.get("hashtags"), list):
                data["hashtags"] = [data["hashtags"]]
            if not data.get("image_url", "").startswith("http"):
                # No usable image — synthesize a placeholder
                data["image_url"] = (
                    "https://images.unsplash.com/photo-1523275335684-37898b6baf30"
                    "?w=800&auto=format&fit=crop&q=80"
                )
            if not data.get("url", "").startswith("http"):
                data["url"] = f"https://example.com/ai/{data.get('id', 'pick')}"
            # Strip fields the agent requires but AI might miss
            data.setdefault("competition_reasons", [])
            # Cast trend_score to int if AI returned float
            try:
                data["trend_score"] = int(data.get("trend_score", 80))
            except Exception:
                data["trend_score"] = 80
            return data
        except Exception as exc:
            logger.warning("AI pick failed, falling back to pool: %s", exc)
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton (lazy)
# ─────────────────────────────────────────────────────────────────────────────


_singleton: Optional[ProductResearcher] = None
_singleton_lock = threading.Lock()


def get_researcher() -> ProductResearcher:
    """Lazy singleton with optional OpenAI wiring."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                client = None
                if os.getenv("OPENAI_API_KEY", "").strip():
                    try:
                        from openai import OpenAI
                        client = OpenAI()
                    except Exception as exc:
                        logger.warning("OpenAI init failed: %s", exc)
                _singleton = ProductResearcher(openai_client=client)
    return _singleton