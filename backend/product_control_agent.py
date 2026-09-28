"""backend/product_control_agent.py

Product Control Agent — strict quality gate that every winning product
must pass before reaching the user.

NON-NEGOTIABLE RULE
--------------------
A winning product is NOT valid unless it has at least one REAL, VERIFIED,
USABLE product image. The agent enforces:

  * **product_quality_pass**  — required fields, semantic category alignment
  * **usable_product_image**  — http/https URL, HEAD reachable, image/*
                                content type, size above the placeholder
                                floor, no tracking pixel / spacer.

If either fails, audit_product() raises a structured ValueError that
the researcher catches to skip the candidate and try the next.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("ProductControlAgent")

# ── Public size constants ────────────────────────────────────────────────────

# Minimum byte size for a usable product image. Anything below this is
# almost certainly a placeholder, a tracking pixel, or a transparent
# spacer. Real product photos are typically > 5 KB even at thumbnail
# resolution.
MIN_IMAGE_BYTES = 5_000

# Reject obvious placeholder file names regardless of file size.
PLACEHOLDER_PATH_HINTS = (
    "/placeholder.",
    "/placeholders/",
    "/blank.",
    "/spacer.",
    "/1x1.",
    "/pixel.",
    "/transparent.",
    "/tracking.",
    "/avatar-default.",
)

# Generic site-logo file names — almost never a product photo.
GENERIC_LOGO_HINTS = (
    "logo", "favicon", "site-icon", "apple-touch-icon",
    "og-image-default", "default-image",
)

PLACEHOLDER_DATA_PREFIXES = ("data:image/svg", "data:image/gif;base64,R0lGOD")


# ── Structured audit result ─────────────────────────────────────────────────


@dataclass
class ImageCheckResult:
    """Result of validating a single image URL."""
    ok: bool = False
    reason: str = ""               # short tag: missing_image_url, broken_image, ...
    image_status: str = "unknown"  # "verified" | "missing" | "broken" | "placeholder" | "too_small" | "wrong_type" | "tracking_pixel" | "logo"
    image_url: Optional[str] = None
    image_bytes: Optional[int] = None
    image_content_type: Optional[str] = None
    http_status: Optional[int] = None


@dataclass
class AuditReport:
    """Full audit result for a product candidate."""
    ok: bool
    product: dict
    reasons: list[str] = field(default_factory=list)
    image_check: Optional[ImageCheckResult] = None

    @property
    def primary_reason(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "ok"


# ── Validation helpers ───────────────────────────────────────────────────────


def _looks_like_placeholder_url(url: str) -> bool:
    """Return True for URL/path signatures that strongly suggest a placeholder.

    These are NEVER acceptable for a real winning product image.
    """
    low = url.lower()
    for hint in PLACEHOLDER_PATH_HINTS:
        if hint in low:
            return True
    for hint in GENERIC_LOGO_HINTS:
        if hint in low:
            return True
    return False


# ── Visual-dominance ranking ─────────────────────────────────────────────────
#
# Many image-search URLs returned by Tavily are TECHNICALLY valid (correct
# content-type, large enough, http) but visually POOR:
#   * magazine editorial photos where the product is a small part of a
#     lifestyle scene
#   * Amazon "lifestyle gallery" photos that include other items (e.g. the
#     devices being charged by a lamp, when the lamp is the actual winner)
#   * wordpress / blog CDN assets used for marketing articles
# Without a heuristic ranking layer, the pipeline picks the first URL that
# passes validation, which is often the wrong one for a Pinterest pin.
#
# The ranker below is a deterministic, no-network heuristic that scores each
# URL on three signals:
#   1. HOST CLASS      — known retailer/manufacturer CDNs get a big bonus;
#                         known magazine/blog editorial hosts get a penalty.
#   2. PATH TOKENS     — /products/, /cdn/shop/, /images/I/...  → bonus;
#                         /editorial/, /article/, /wp-content/  → penalty.
#   3. FILENAME KEYWORDS — does the URL contain words from the product name?
#
# The result is a list of (url, score) sorted by score descending. Callers
# iterate the list and pick the first URL that also passes _validate_image.
# This guarantees that when MULTIPLE images for the same product are
# available, we choose the most product-forward one.

# Host → bonus / penalty. Tuned from real Tavily results observed on Render.
PRODUCT_HOST_SCORES: dict[str, int] = {
    # Major retailer product-image CDNs (typically 1-2 large JPGs, no scenery).
    "m.media-amazon.com": 60,
    "images-na.ssl-images-amazon.com": 60,
    "images.amazon.com": 55,
    "i.etsystatic.com": 55,
    "i5.walmartimages.com": 55,
    "mobileimages.lowes.com": 50,
    "target.scene7.com": 50,
    "ak1.ostkcdn.com": 50,
    "media.startech.com": 50,
    "gdx-assets.costco.com": 55,
    "img.kentfaith.com": 35,
    "www.4allpromos.com": 30,
    # Shopify-hosted product images (most retailers render a single product
    # shot for these).
    "cdn.shopify.com": 35,
    "www.progressivedesk.com": 25,
    "carlsonpetproducts.com": 25,
    "www.letifly.com": 25,
    "www.pamperedchef.com": 25,
    "jasonmarkk.com": 25,
    "speedcleaning.com": 25,
    "slickproductsusa.com": 25,
    # Generic / unknown hosts — neutral.
}
LIFESTYLE_HOST_SCORES: dict[str, int] = {
    "hips.hearstapps.com": -45,            # Hearst magazine editorial
    "vader-prod.s3.amazonaws.com": -45,    # Hearst magazine editorial
    "pyxis.nymag.com": -45,                # New York Magazine
    "food.fnr.sndimg.com": -45,            # Food Network editorial
    "www.familyhandyman.com": -30,         # DIY blog
    "www.nationsphotolab.com": -25,        # Photo service (not product)
    "images.ctfassets.net": -10,           # Generic CMS — ambiguous
}
# Path tokens that strongly suggest a single-product hero shot.
PRODUCT_PATH_TOKENS = (
    "/products/", "/product/", "/img/", "/photo/", "/photos/",
    "/cdn/shop/", "/cdn/shop/files/", "/cdn/shop/products/",
    "/images/i/", "/images/I/", "/productimages/",
    "/iceberg/com/product/", "/shop/files/",
)
# Path tokens that strongly suggest editorial / lifestyle / scene.
LIFESTYLE_PATH_TOKENS = (
    "/editorial/", "/article/", "/blog/", "/lifestyle/",
    "/wp-content/uploads/", "/wp-json/",
    "/stories/", "/guide/", "/how-to/",
)
# Filename patterns that suggest lifestyle scenes (multiple products, props).
LIFESTYLE_FILENAME_TOKENS = (
    "_scene_", "_lifestyle_", "_editorial_", "_in-use_",
    "_setup_", "_with-phone_", "_with-watch_",
    "-with-", "-and-",
)


# ── Query-to-product relevance ───────────────────────────────────────────────
#
# The user's query (e.g. "kitchen organizer", "desk lamp") is a hard
# eligibility constraint, NOT a loose inspiration signal. A product must
# semantically match the query before trend score or any other factor can
# make it a winner.
#
# The scorer below is deterministic and model-free. It uses:
#   1. Direct token overlap with the query
#   2. Category-family synonyms (e.g. "organizer" family includes rack,
#      shelf, storage, holder, bin, drawer, pantry, cabinet, etc.)
#   3. Negative-category guards (e.g. "kitchen organizer" must NOT match
#      brushes, scrubbers, lamps, pet supplies, etc.)
#
# Returns a float in [0.0, 1.0] plus a list of matched tokens for logging.

CATEGORY_FAMILIES: dict[str, list[str]] = {
    # kitchen organizer / storage
    "kitchen_org": [
        "organizer", "storage", "rack", "shelf", "shelves", "holder",
        "bin", "baskets", "basket", "drawer", "pantry", "cabinet",
        "countertop", "utensil", "spice", "drying", "dish", "tray",
        "container", "caddy", "organize",
    ],
    "kitchen_cook": [
        "kitchen", "cook", "chef", "fryer", "airfryer", "knife",
        "coffee", "mug", "lunch", "bento", "pan", "skillet", "meal",
        "recipe", "bake", "grocery", "pot", "wok", "rice",
    ],
    # phone stand
    "phone": [
        "phone", "iphone", "smartphone", "mobile", "magsafe", "android",
        "stand", "holder", "mount", "dock", "cradle", "charging",
    ],
    # desk lamp / task lamp
    "lamp": [
        "lamp", "light", "lighting", "led", "task", "desk",
        "study", "reading", "bedside", "table", "bulb",
    ],
    # pet bed
    "pet_bed": [
        "dog", "cat", "pet", "pup", "puppy", "kitten", "bed",
        "calming", "orthopedic", "kennel", "crate", "cuddler",
        "self-warming", "plush",
    ],
    # pet general
    "pet": [
        "dog", "cat", "pet", "pup", "puppy", "kitten",
        "leash", "grooming", "feeder", "litter", "treat",
        "tree", "scratching", "toy", "bowl",
    ],
    # cleaning brush / scrubber
    "cleaning_brush": [
        "brush", "scrubber", "scrub", "cleaning", "spin",
        "electric", "toilet", "bathroom", "scrubbing",
    ],
    # general cleaning
    "cleaning": [
        "clean", "scrub", "brush", "mop", "wash", "wipe", "vacuum",
        "grout", "ultrasonic", "soap", "tile", "stain", "dust",
    ],
    # fitness
    "fitness": [
        "yoga", "fitness", "workout", "exercise", "gym", "stretch",
        "posture", "massage", "foam", "roller", "resistance", "band",
        "dumbbell", "pilates", "wellness", "pain",
    ],
    # decor
    "decor": [
        "lamp", "sunset", "projection", "light", "decor", "aesthetic",
        "ambient", "mood", "bedroom", "cozy", "throw", "candle",
        "plant", "shelf", "mirror",
    ],
}

# Tokens that, when present in the PRODUCT, contradict a query family.
# Each entry: query_family → list of product tokens that DISQUALIFY.
QUERY_NEGATIVES: dict[str, list[str]] = {
    "kitchen_org": [
        # unrelated families
        "lamp", "light", "pet", "dog", "cat", "pup",
        "phone", "iphone", "yoga", "fitness",
        "brush", "scrubber", "mop", "vacuum",
    ],
    "kitchen_cook": [
        "lamp", "phone", "pet", "dog", "cat",
        "brush", "scrubber", "yoga", "fitness",
    ],
    "phone": [
        "lamp", "light", "dog", "cat", "pet", "bed", "brush",
        "scrubber", "mop", "cookware", "skillet", "fryer",
        "yoga", "fitness",
    ],
    "lamp": [
        "organizer", "shelf", "bin", "dog", "cat", "pet",
        "bed", "brush", "scrubber", "mop", "pan", "skillet",
        "cookware", "knife", "yoga", "fitness",
    ],
    "pet_bed": [
        "lamp", "phone", "organizer", "shelf", "brush",
        "scrubber", "mop", "cookware", "yoga", "fitness",
    ],
    "pet": [
        "lamp", "phone", "organizer", "cookware", "yoga",
    ],
    "cleaning_brush": [
        "lamp", "phone", "dog", "cat", "pet", "bed",
        "organizer", "shelf", "yoga", "fitness",
    ],
    "cleaning": [
        "lamp", "phone", "dog", "cat", "pet", "bed",
        "organizer", "shelf", "yoga", "fitness",
    ],
    "fitness": [
        "lamp", "phone", "dog", "cat", "pet", "bed",
        "organizer", "brush", "scrubber",
    ],
    "decor": [
        "phone", "brush", "scrubber", "pet", "dog",
    ],
}


# ── Product-type intent ─────────────────────────────────────────────────────
#
# A "product type" is the specific noun the user wants — bed, organizer,
# stand, lamp, brush, … — independent of the broad category. A pet feeder
# is a pet product but it is NOT a pet bed. A general kitchen item is in
# the kitchen category but it is NOT a kitchen organizer.
#
# PRODUCT_TYPE_SYNONYMS lists the acceptable product nouns for each type.
# PRODUCT_TYPE_NEGATIVES lists tokens in the candidate that disqualify it
# (e.g. "vacuum" disqualifies a candidate from being a "brush" winner).

PRODUCT_TYPE_SYNONYMS: dict[str, list[str]] = {
    "organizer": [
        "organizer", "organize", "storage", "rack", "shelf", "shelves",
        "holder", "bin", "baskets", "basket", "drawer", "pantry",
        "cabinet", "countertop", "utensil", "spice", "drying",
        "dish", "tray", "container", "caddy",
    ],
    "bed": [
        "bed", "cuddler", "sleeping", "mattress", "orthopedic",
        "self-warming", "plush", "calming", "pillow", "cushion",
    ],
    "stand": [
        "stand", "holder", "mount", "dock", "cradle", "support",
        "tripod", "pedestal",
    ],
    "lamp": [
        "lamp", "task", "study", "desk light", "desk lamp",
        "reading light", "nightlight", "table lamp", "task light",
    ],
    "brush": [
        "brush", "scrubber", "scrub", "scrubbing", "broom",
        "sponge", "sweeper",
    ],
    "feeder": ["feeder", "food dispenser", "water dispenser"],
    "leash": ["leash", "lead", "harness", "collar"],
    "toy": ["toy"],
    "tree": ["tree", "tower", "perch"],
    "knife": ["knife", "knives", "sharpener", "cutting"],
    "mug": ["mug", "tumbler", "cup", "warmer"],
    "lunch": ["lunch", "bento"],
    "pan": ["pan", "skillet", "wok", "pot"],
    "yoga": ["yoga", "mat", "meditation"],
    "massage": ["massage", "gun"],
    "bottle": ["bottle", "flask", "tumbler"],
    "dumbbell": ["dumbbell", "weight"],
    "foam": ["foam", "roller"],
    "plant": ["plant", "pot"],
    "shelf_decor": ["shelf", "shelves"],
    "clock": ["clock"],
    "blanket": ["blanket", "throw"],
    "led_strip": ["led strip", "strip light"],
    "sunset": ["sunset", "projection", "ambient"],
}

PRODUCT_TYPE_NEGATIVES: dict[str, list[str]] = {
    "organizer": [
        "brush", "scrubber", "lamp", "phone", "bed", "leash",
        "feeder", "toy", "tree", "knife", "mug", "pan",
        "yoga", "mat", "massage", "bottle", "dumbbell",
    ],
    "bed": [
        "feeder", "leash", "tree", "toy", "scratcher",
        "brush", "scrubber", "lamp", "phone", "organizer",
        "knife", "mug", "pan", "yoga", "massage", "bottle",
        "dumbbell", "clock", "blanket",
    ],
    "stand": [
        "lamp", "bed", "brush", "scrubber", "feeder", "leash",
        "toy", "tree", "knife", "mug", "pan", "yoga",
        "massage", "bottle", "dumbbell", "clock", "blanket",
        "charger",
    ],
    "lamp": [
        "bed", "organizer", "brush", "scrubber", "feeder",
        "leash", "toy", "tree", "knife", "mug", "pan",
        "yoga", "massage", "bottle", "dumbbell", "clock",
        "blanket",
        # ceiling/wall/sconce fixtures are NOT desk/task lamps
        "ceiling", "wall", "sconce", "pendant", "flush",
        "outdoor", "string light", "fairy light",
    ],
    "brush": [
        "lamp", "phone", "bed", "organizer", "feeder",
        "leash", "toy", "tree", "knife", "mug", "pan",
        "yoga", "massage", "bottle", "dumbbell", "clock",
        "blanket", "vacuum",
    ],
}


def _extract_product_type_intent(query: str) -> tuple[str | None, list[str]]:
    """Identify the primary product-type noun in the query.

    Returns (product_type, matched_synonyms). product_type is None when
    no recognizable product noun is present (e.g. "kitchen stuff").
    """
    q_tokens = _tokenize(query)
    if not q_tokens:
        return None, []

    # Prefer the longest synonym match so "cleaning brush" picks "brush"
    # rather than just "cleaning".
    best: tuple[str, list[str], int] | None = None
    for product_type, synonyms in PRODUCT_TYPE_SYNONYMS.items():
        hits: list[str] = []
        for syn in synonyms:
            for qt in q_tokens:
                if _tokens_match(qt, syn) or (len(qt) >= 4 and (qt in syn or syn in qt)):
                    if qt not in hits:
                        hits.append(qt)
                    break
        if hits:
            score = len(hits) * 10 + max(len(h) for h in hits)
            if best is None or score > best[2]:
                best = (product_type, hits, score)

    if best is None:
        return None, []
    return best[0], best[1]


def _tokenize(s: str) -> set[str]:
    """Lowercase tokenization that keeps compound tokens (kitchen-organizer
    → {kitchen, organizer, kitchen-organizer})."""
    if not s:
        return set()
    low = s.lower()
    # Replace non-alphanumeric with space, but preserve hyphenated words.
    import re as _re
    parts = _re.split(r"[^a-z0-9\-]+", low)
    out: set[str] = set()
    for p in parts:
        if not p:
            continue
        out.add(p)
        # also break on hyphens so 'kitchen-organizer' → {kitchen, organizer}
        for sub in p.split("-"):
            if sub and len(sub) > 1:
                out.add(sub)
    return out


def _classify_query_families(query: str) -> set[str]:
    """Map a free-form query to one or more category families.

    Uses EXACT token matching (with a small allowance for plural forms
    ending in 's'). We intentionally avoid substring matching because
    words like "bed" appear in unrelated family tokens (e.g. "bedside"
    in the lamp family) and would falsely activate the wrong family.
    """
    q_tokens = _tokenize(query)
    families: set[str] = set()
    for family_name, family_tokens in CATEGORY_FAMILIES.items():
        for ft in family_tokens:
            for qt in q_tokens:
                if _tokens_match(qt, ft):
                    families.add(family_name)
                    break
    return families


def _tokens_match(a: str, b: str) -> bool:
    """True if two tokens refer to the same concept.

    Allows exact match, plural-vs-singular, and very small stems only
    (≥4 chars). Avoids substring matching ("bed" vs "bedside") which
    causes false-positive family activations.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    # Plurals: drop trailing 's' if both ≥4 chars.
    if len(a) >= 4 and len(b) >= 4:
        if a.endswith("s") and a[:-1] == b:
            return True
        if b.endswith("s") and b[:-1] == a:
            return True
    return False


def compute_query_product_relevance(
    query: str,
    product_name: str,
    product_category: str = "",
) -> tuple[float, float, list[str]]:
    """Deterministic two-axis relevance score.

    Returns (category_score, product_type_score, matched_tokens).
    Both axes are scored independently on [0.0, 1.0]. A candidate is
    only acceptable when BOTH meet their thresholds (CATEGORY_THRESHOLD
    and PRODUCT_TYPE_THRESHOLD).

    category_score    — does the candidate belong to the broad category
                        implied by the query? (kitchen, pet, tech, etc.)
                        Driven by CATEGORY_FAMILIES + QUERY_NEGATIVES.

    product_type_score — does the candidate match the SPECIFIC PRODUCT
                        TYPE the user asked for? (organizer, bed, stand,
                        lamp, brush, …) Driven by PRODUCT_TYPE_SYNONYMS
                        + PRODUCT_TYPE_NEGATIVES.

    A pet feeder must NOT win a "pet bed" query even though both are pet
    products. A Spin Scrubber must NOT win a "kitchen organizer" query
    even though both are in the cleaning-adjacent space. Each axis
    must independently pass.
    """
    if not query or not query.strip():
        # No constraint — neutral relevance on both axes.
        return 0.50, 0.50, []

    q_tokens = _tokenize(query)
    product_text = f"{product_name or ''} {product_category or ''}".strip()
    p_tokens = _tokenize(product_text)

    if not q_tokens:
        return 0.50, 0.50, []

    # ── 1. Category score ───────────────────────────────────────────
    matched: list[str] = []
    direct_hits = 0
    for qt in q_tokens:
        for pt in p_tokens:
            if _tokens_match(qt, pt) or (len(qt) >= 4 and (qt in pt or pt in qt)):
                direct_hits += 1
                matched.append(qt)
                break
    direct_score = min(direct_hits / max(len(q_tokens), 1), 1.0)

    families = _classify_query_families(query)
    family_score = 0.0
    if families:
        matched_families: set[str] = set()
        for fam in families:
            fams_tokens = CATEGORY_FAMILIES.get(fam, [])
            for ft in fams_tokens:
                for pt in p_tokens:
                    if _tokens_match(ft, pt) or (len(ft) >= 4 and (ft in pt or pt in ft)):
                        matched_families.add(fam)
                        break
                if fam in matched_families:
                    break
        family_score = min(len(matched_families) / max(len(families), 1), 1.0)

    negative_penalty = 0.0
    for fam in families:
        for neg_tok in QUERY_NEGATIVES.get(fam, []):
            for pt in p_tokens:
                if _tokens_match(neg_tok, pt) or (len(neg_tok) >= 4 and (neg_tok in pt)):
                    negative_penalty += 0.30
                    break
    negative_penalty = min(negative_penalty, 0.70)

    category_raw = (direct_score * 0.45) + (family_score * 0.55)
    category_score = max(0.0, min(1.0, category_raw - negative_penalty))

    # ── 2. Product-type score ───────────────────────────────────────
    product_type, product_type_matched = _extract_product_type_intent(query)
    if product_type is None:
        # Query has no recognizable product-type noun. We do NOT
        # constrain on product type — treat as neutral (1.0) so a
        # category-only query does not over-reject.
        product_type_score = 1.0
    else:
        synonyms = PRODUCT_TYPE_SYNONYMS.get(product_type, [])
        negatives = PRODUCT_TYPE_NEGATIVES.get(product_type, [])

        # Count how many type synonyms the product text contains.
        syn_hits = 0
        for syn in synonyms:
            for pt in p_tokens:
                if _tokens_match(syn, pt) or (len(syn) >= 4 and (syn in pt or pt in syn)):
                    syn_hits += 1
                    break
        # Synonym match: at least one synonym is good enough for 0.8,
        # two or more is 1.0. None is 0.0.
        if syn_hits >= 2:
            type_raw = 1.0
        elif syn_hits == 1:
            type_raw = 0.8
        else:
            type_raw = 0.0

        # Negative tokens for this product type drop the score to 0.
        neg_hit = False
        for neg_tok in negatives:
            for pt in p_tokens:
                if _tokens_match(neg_tok, pt) or (len(neg_tok) >= 4 and (neg_tok in pt)):
                    neg_hit = True
                    break
            if neg_hit:
                break
        if neg_hit:
            type_raw = 0.0

        product_type_score = round(type_raw, 3)

    return (
        round(category_score, 3),
        round(product_type_score, 3),
        matched + (["type:" + product_type] if product_type else []),
    )


# Hard threshold: an image URL whose ranker score is below this is
# treated as "least bad" and the candidate is REJECTED rather than
# accepting a poor image just because no better candidate exists.
MIN_PRODUCT_IMAGE_SCORE = 25

# Early-stop threshold: when the best image found so far scores this
# high, the cascade stops asking Tavily for more queries (it almost
# certainly came from a retailer CDN).
STRONG_IMAGE_SCORE = 50

# Maximum image-search queries tried per candidate. Bounded so a slow
# provider cannot blow the request budget.
MAX_IMAGE_SEARCH_QUERIES = 3

# Hard thresholds for the two-axis relevance gate. A candidate must
# pass BOTH axes independently; broad category match alone is not
# sufficient (a pet feeder cannot win a pet-bed query).
CATEGORY_THRESHOLD = 0.30       # broad family match
PRODUCT_TYPE_THRESHOLD = 0.50   # specific product-type match

# Backwards-compat alias used by older tests.
MIN_RELEVANCE_SCORE = CATEGORY_THRESHOLD


# ── Product-title normalization for image search ───────────────────────────
#
# Marketing-heavy product titles like
#   "Rotating Spice Rack Organizer — 16 Jars, Labels Included"
# confuse image search because:
#   * em-dashes split the title in Tavily's parser,
#   * count/size suffixes ("16 Jars") make queries too specific,
#   * marketing adjectives ("Best", "Trending", "Premium") add noise.
#
# _normalize_product_title() produces a cleaner phrase for the search
# query while preserving the product-defining terms.

_TITLE_NOISE_WORDS = frozenset({
    "best", "top", "trending", "viral", "popular", "hot",
    "limited", "limited-edition", "new", "improved", "premium",
    "professional", "ultimate", "essential", "must-have",
    "high-quality", "highquality", "top-rated", "amazing",
    "incredible", "perfect", "great", "awesome",
    # Common product-title filler
    "labels", "included", "bonus", "free", "shipping",
    "warranty", "guarantee", "official", "authentic", "genuine",
    "brand", "branded",
})


def _normalize_product_title(title: str) -> str:
    """Reduce a marketing-heavy product title to clean image-search terms.

    Returns a normalized phrase (≤ 8 tokens) that preserves the
    product-defining attributes and drops count/size/marketing noise.

    Example:
        "Rotating Spice Rack Organizer — 16 Jars, Labels Included"
            → "rotating spice rack organizer 16 jars"
    """
    if not title:
        return ""
    import re as _re
    # Normalize unicode dashes/punctuation to spaces.
    s = title.lower()
    s = _re.sub(r"[—–\-_/]+", " ", s)
    s = _re.sub(r"[,:;.()\[\]{}!?\"']", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    # Drop noise tokens.
    parts = [w for w in s.split(" ") if w and w not in _TITLE_NOISE_WORDS]
    # Cap at 8 tokens — Tavily's image search performs better with
    # shorter, focused queries.
    if len(parts) > 8:
        parts = parts[:8]
    return " ".join(parts)


def _simplify_for_search(title: str) -> str:
    """Drop trailing count/size/capacity tokens to broaden the search.

    Example:
        "rotating spice rack organizer 16 jars" → "spice rack organizer"
    """
    if not title:
        return ""
    import re as _re
    # Strip tokens that look like "16", "16oz", "12-pack", "3-compartment".
    cleaned = _re.sub(
        r"\b\d+(?:\.\d+)?\s*(?:oz|lb|kg|ml|l|pack|piece|pieces|count|"
        r"compartment|compartments|jar|jars|bottle|bottles|ct|"
        r"inch|in|cm|mm|ft|sq\.?\s?ft|set|sets|piece|day|days|"
        r"hour|hours|min|mins|sec|secs|pcs)\b",
        "", title,
    )
    cleaned = _re.sub(r"\b\d+(?:\.\d+)?\b", "", cleaned)  # bare numbers
    cleaned = _re.sub(r"\s+", " ", cleaned).strip()
    # Avoid an empty result if everything was a number.
    return cleaned or title


def build_image_query_cascade(
    product_name: str,
    category: str,
    intent: str,
    max_queries: int = MAX_IMAGE_SEARCH_QUERIES,
    *,
    asin: str | None = None,
) -> list[str]:
    """Build a bounded ordered list of image-search queries for a candidate.

    Order is MOST SPECIFIC → MOST GENERAL:
      1. ASIN (when supplied) — most specific, often returns the actual
         product page on a retailer CDN.
      2. Normalized full product title (e.g. "rotating spice rack organizer 16 jars")
      3. Simplified title without count/size (e.g. "spice rack organizer")
      4. User intent (e.g. "kitchen organizer") as last-resort fallback

    Queries are deduplicated and capped at max_queries.
    """
    cascade: list[str] = []
    seen: set[str] = set()

    def _add(q: str) -> None:
        q = (q or "").strip()
        if not q:
            return
        low = q.lower()
        if low in seen:
            return
        seen.add(low)
        cascade.append(q)

    # ASIN first when supplied — gives Tavily/Amazon search a strong
    # anchor for finding the actual product photo.
    if asin:
        _add(f"{asin} Amazon product photo")

    name = (product_name or "").strip()
    if name:
        normalized = _normalize_product_title(name)
        if normalized:
            _add(normalized)
            simpler = _simplify_for_search(normalized)
            if simpler and simpler.lower() != normalized.lower():
                _add(simpler)

    intent_clean = (intent or "").strip()
    if intent_clean and intent_clean.lower() != (name or "").lower():
        _add(intent_clean)

    return cascade[:max_queries]


def rank_image_candidates(
    urls: list[str],
    *,
    product_name: str = "",
    category: str = "",
) -> list[tuple[str, int]]:
    """Score and rank image URLs by visual-dominance heuristics.

    Higher score = more likely to be a clean, product-forward hero shot.
    The result is sorted descending so callers can pick the best URL
    that also passes _validate_image().

    This is a deterministic heuristic — no network calls, no model —
    that biases the selection toward retailer-hosted product photos and
    away from magazine/blog lifestyle editorial images.
    """
    if not urls:
        return []

    name_words = [w.lower() for w in (product_name or "").split() if len(w) > 2][:6]
    category_words = [w.lower() for w in (category or "").split() if len(w) > 2][:3]
    name_set = set(name_words + category_words)

    scored: list[tuple[str, int]] = []
    for url in urls:
        score = 0
        low = url.lower()
        # ── HOST CLASS ────────────────────────────────────────────
        try:
            host = low.split("//", 1)[-1].split("/", 1)[0]
            # strip leading www.
            host_no_www = host[4:] if host.startswith("www.") else host
        except Exception:
            host = ""
            host_no_www = ""
        matched = False
        for h, bonus in PRODUCT_HOST_SCORES.items():
            if host == h or host_no_www == h or host.endswith("." + h):
                score += bonus
                matched = True
                break
        if not matched:
            for h, penalty in LIFESTYLE_HOST_SCORES.items():
                if host == h or host_no_www == h or host.endswith("." + h):
                    score += penalty
                    matched = True
                    break

        # ── PATH TOKENS ────────────────────────────────────────────
        for tok in PRODUCT_PATH_TOKENS:
            if tok in low:
                score += 25
                break
        for tok in LIFESTYLE_PATH_TOKENS:
            if tok in low:
                score -= 30
                break

        # ── FILENAME KEYWORDS ──────────────────────────────────────
        if name_set:
            hits = sum(1 for w in name_set if w in low)
            if hits:
                score += min(hits, 3) * 12
        for tok in LIFESTYLE_FILENAME_TOKENS:
            if tok in low:
                score -= 25
                break

        # ── MIME HINTS from URL ────────────────────────────────────
        if any(low.endswith(ext) for ext in (".jpg", ".jpeg", ".webp", ".png")):
            score += 5  # explicit image format is a positive signal

        # ── QUERY STRINGS that hint at thumbnails ─────────────────
        if any(seg in low for seg in ("_thumb", "/thumb/", "?thumb", "size=thumb")):
            score -= 15

        scored.append((url, score))

    # Stable order: highest score first, ties broken by original order.
    scored.sort(key=lambda t: -t[1])
    return scored


def _validate_image(url: Optional[str], *, head_timeout: float = 3.0) -> ImageCheckResult:
    """Strictly validate a candidate image URL.

    Rejects (in order):
      * missing/empty
      * non-http(s) schemes (data:, blob:, file:, ftp:, …)
      * placeholder path hints (logo, placeholder, 1x1, pixel, …)
      * HEAD failure (4xx/5xx, network error, redirect to non-image)
      * wrong content-type (text/html, application/json, …)
      * too-small payload (< MIN_IMAGE_BYTES)

    On success returns ok=True with image_status="verified" and the
    HEAD response details for evidence.

    Validation flow:
      1. Cheap URL checks (scheme / placeholder / data: URI).
      2. HEAD with allow_redirects=True (bounded redirect chain). If the
         final URL is image/* → accept. If the final URL is not image/*
         → reject as wrong_type.
      3. If HEAD returns 403/405 (CDNs that don't support HEAD) OR if the
         final redirect went to an image host that did not advertise a
         Content-Length, fall back to a tiny ranged GET to verify.
      4. Reject if Content-Length / Content-Range says the file is
         smaller than MIN_IMAGE_BYTES (tracking pixel / spacer).

    All network calls share the same `head_timeout` and a single
    Session so a malicious redirect loop cannot blow up the request.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return ImageCheckResult(ok=False, reason="missing_image_url",
                               image_status="missing")

    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ImageCheckResult(ok=False, reason="invalid_image_url_scheme",
                               image_status="placeholder", image_url=url)

    if any(url.startswith(p) for p in PLACEHOLDER_DATA_PREFIXES):
        return ImageCheckResult(ok=False, reason="placeholder_image",
                               image_status="placeholder", image_url=url)

    if _looks_like_placeholder_url(url):
        return ImageCheckResult(ok=False, reason="placeholder_image",
                               image_status="placeholder", image_url=url)

    # NOTE: we use module-level requests.head/get rather than a Session so
    # tests can mock `requests.head` / `requests.get` directly. A Session
    # would bypass those mocks.

    # ── Step 1: HEAD with redirects allowed (bounded by requests' default
    # max_redirects=30, which is more than enough for any real CDN).
    try:
        head_resp = requests.head(
            url,
            timeout=head_timeout,
            allow_redirects=True,
        )
    except Exception as exc:
        # Network-level failure on HEAD — try a small GET next.
        head_resp = None
        head_exc = exc

    if head_resp is not None and head_resp.status_code < 400:
        # We got a successful response — possibly after redirects.
        content_type = (head_resp.headers.get("Content-Type") or "").lower().split(";")[0].strip()
        if not content_type.startswith("image/"):
            return ImageCheckResult(
                ok=False,
                reason=f"wrong_type: {content_type}",
                image_status="wrong_type", image_url=url,
                http_status=head_resp.status_code,
                image_content_type=content_type,
            )
        size = _extract_size(head_resp.headers)
        if size is None or size == 0:
            # No Content-Length — try a tiny ranged GET to confirm.
            size = _size_via_range_get(url, head_timeout)
        if size is not None and 0 < size < MIN_IMAGE_BYTES:
            return ImageCheckResult(
                ok=False,
                reason=f"image_too_small: {size} bytes",
                image_status="tracking_pixel", image_url=url,
                image_bytes=size, image_content_type=content_type,
                http_status=head_resp.status_code,
            )
        # Verified by HEAD.
        return ImageCheckResult(
            ok=True,
            image_status="verified",
            image_url=url,
            image_bytes=size if size and size > 0 else None,
            image_content_type=content_type,
            http_status=head_resp.status_code,
        )

    # ── Step 2: HEAD was 4xx/5xx or threw. Some CDNs reject HEAD.
    # If HEAD returned 403 or 405 we explicitly try a tiny GET to verify.
    head_failed_cleanly = head_resp is not None and head_resp.status_code in (403, 405)
    head_failed_other = head_resp is not None and head_resp.status_code >= 400 and not head_failed_cleanly
    head_threw = head_resp is None

    if head_failed_cleanly:
        # HEAD-hostile server — try a ranged GET and trust Content-Type + Content-Range.
        size, ct, status = _verify_via_ranged_get(url, head_timeout)
        if status is None:
            return ImageCheckResult(
                ok=False, reason=f"image_load_failed: GET also failed",
                image_status="broken", image_url=url,
            )
        if not (ct or "").startswith("image/"):
            return ImageCheckResult(
                ok=False, reason=f"wrong_type: {ct}",
                image_status="wrong_type", image_url=url,
                http_status=status, image_content_type=ct,
            )
        if size is not None and 0 < size < MIN_IMAGE_BYTES:
            return ImageCheckResult(
                ok=False, reason=f"image_too_small: {size} bytes",
                image_status="tracking_pixel", image_url=url,
                image_bytes=size, image_content_type=ct,
                http_status=status,
            )
        return ImageCheckResult(
            ok=True, image_status="verified",
            image_url=url,
            image_bytes=size if size and size > 0 else None,
            image_content_type=ct,
            http_status=status,
        )

    if head_threw:
        # Network error on HEAD — last-ditch GET with the same timeout.
        size, ct, status = _verify_via_ranged_get(url, head_timeout)
        if status is None:
            return ImageCheckResult(
                ok=False,
                reason=f"image_load_failed: {type(head_exc).__name__}",
                image_status="broken", image_url=url,
            )
        if not (ct or "").startswith("image/"):
            return ImageCheckResult(
                ok=False, reason=f"wrong_type: {ct}",
                image_status="wrong_type", image_url=url,
                http_status=status, image_content_type=ct,
            )
        return ImageCheckResult(
            ok=True, image_status="verified",
            image_url=url, image_bytes=size,
            image_content_type=ct, http_status=status,
        )

    # HEAD returned a non-403/405 error (e.g. 404, 500) — the resource is gone.
    return ImageCheckResult(
        ok=False,
        reason=f"image_load_failed: HTTP {head_resp.status_code}",
        image_status="broken", image_url=url,
        http_status=head_resp.status_code,
    )


def _extract_size(headers) -> Optional[int]:
    """Return total image size from Content-Length or Content-Range."""
    cl = headers.get("Content-Length")
    if cl and cl.isdigit():
        return int(cl)
    cr = headers.get("Content-Range")  # "bytes 0-1023/48231"
    if cr and "/" in cr:
        try:
            return int(cr.rsplit("/", 1)[1])
        except ValueError:
            return None
    return None


def _size_via_range_get(url: str, timeout: float) -> Optional[int]:
    """Fallback size probe via a 0-1023 range GET. Returns total bytes or None."""
    try:
        r = requests.get(url, headers={"Range": "bytes=0-1023"},
                         timeout=timeout, allow_redirects=True, stream=True)
        size = _extract_size(r.headers)
        if size is None:
            size = len(r.content or b"")
        r.close()
        return size
    except Exception:
        return None


def _verify_via_ranged_get(url: str, timeout: float):
    """HEAD-hostile fallback: do a small ranged GET and return
    (size, content_type, status). Returns (None, None, None) on failure.
    """
    try:
        r = requests.get(url, headers={"Range": "bytes=0-1023"},
                         timeout=timeout, allow_redirects=True, stream=True)
        size = _extract_size(r.headers)
        if size is None:
            size = len(r.content or b"")
        ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        status = r.status_code
        r.close()
        return size, ct, status
    except Exception:
        return None, None, None


# ── Main agent ───────────────────────────────────────────────────────────────


class ProductControlAgent:
    """Quality gate. audit_product() returns the product on success or
    raises ValueError with a structured reason on rejection. evaluate()
    returns the same info as an AuditReport dataclass (no raise) for
    callers that want to inspect reasons programmatically.
    """

    @staticmethod
    def audit_product(product: dict) -> dict:
        """Backward-compatible: return product on pass, raise ValueError on fail.

        Existing callers (researcher, tests) catch the exception to skip
        the candidate and move to the next one.
        """
        report = ProductControlAgent.evaluate(product)
        if not report.ok:
            raise ValueError(
                f"Product Control Violation: {report.primary_reason} "
                f"(product id={product.get('id', '?')})"
            )
        return report.product

    @staticmethod
    def evaluate(product: dict) -> AuditReport:
        """Full audit — returns AuditReport (does not raise)."""
        reasons: list[str] = []
        # ── 1. Required fields ─────────────────────────────────────────
        required_fields = [
            "id", "name", "category", "image_url", "url", "angle",
            "pin_title", "pin_description", "hashtags",
        ]
        for f in required_fields:
            if not product.get(f):
                reasons.append(f"missing_field: {f}")

        # ── 2. Category / name semantic alignment ─────────────────────
        category = (product.get("category") or "").lower()
        name = (product.get("name") or "").lower()
        category_keywords = {
            "cleaning": ["scrub", "clean", "brush", "mop", "vacuum", "wipe", "cleaner", "grout", "ultrasonic"],
            "tech": ["charger", "station", "cable", "wireless", "magsafe", "led", "phone", "hub", "dock"],
            "pet": ["dog", "cat", "pet", "bed", "pup", "leash", "grooming", "calming"],
            "home decor": ["lamp", "sunset", "projection", "light", "decor", "aesthetic", "ambient"],
        }
        matched_category = next((k for k in category_keywords if k in category), None)
        if matched_category:
            valid_terms = category_keywords[matched_category]
            if not any(term in name for term in valid_terms):
                reasons.append(
                    f"semantic_mismatch: category '{category}' vs name '{name}'"
                )

        # ── 3. Image eligibility (HARD GATE) ────────────────────────────
        image_check = _validate_image(product.get("image_url"))
        if not image_check.ok:
            reasons.append(f"image_rejected: {image_check.reason}")

        # ── 4. Decorate product with verified-image metadata ──────────
        out = dict(product)
        if image_check.ok:
            out["image_status"] = image_check.image_status              # "verified"
            # Do NOT overwrite image_source if the caller already set it
            # (e.g. the researcher may have set it to "image_research").
            out.setdefault("image_source", image_check.image_url)
            out["image_url_verified"] = image_check.image_url
            out["image_bytes"] = image_check.image_bytes
            out["image_content_type"] = image_check.image_content_type
            out["image_http_status"] = image_check.http_status
        else:
            out["image_status"] = image_check.image_status              # "missing"|"broken"|…
            out["image_rejection_reason"] = image_check.reason
            # Strip the bad image so downstream renderers can't use it.
            out["image_url"] = None

        return AuditReport(
            ok=(len(reasons) == 0),
            product=out,
            reasons=reasons,
            image_check=image_check,
        )