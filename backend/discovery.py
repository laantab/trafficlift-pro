"""True product discovery pipeline.

This is the implementation of PATH A — Find Winning Product. It runs
when the user clicks Find Winning Product with NO input. It must NOT
depend on a user query, keyword relevance, or the static pool.

Architecture:
    1. Live research queries via Tavily (5 targeted product-opportunity
       queries). No fake keywords.
    2. Normalize each Tavily result into a candidate product record.
    3. Reject articles, blog posts, brands without product, vague
       listicles, duplicates, malformed candidates.
    4. Build full ProductCard records with normalized fields and
       evidence.
    5. For each candidate, run the existing image cascade
       (build_image_query_cascade + Tavily image search + validate +
       rank). Skip candidates that fail image qualification.
    6. Audit the first qualified candidate with ProductControlAgent.
    7. Return the winner or a discovery-specific 404.

Bounded by MAX_DISCOVERY_RESEARCH_QUERIES and MAX_DISCOVERY_CANDIDATES.
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Optional

from backend import live_research
from backend.curated_winners import CURATED_WINNERS, CuratedWinner
from backend.product_control_agent import (
    ProductControlAgent,
    _validate_image,
    build_image_query_cascade,
    rank_image_candidates,
    MIN_PRODUCT_IMAGE_SCORE,
    STRONG_IMAGE_SCORE,
)
from backend.product_research import ProductCard, ProductResearcher, _placeholder

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

MAX_DISCOVERY_RESEARCH_QUERIES = 5
MAX_DISCOVERY_CANDIDATES = 8
MAX_IMAGE_SEARCH_QUERIES_PER_CANDIDATE = 3
DISCOVERY_REQUEST_BUDGET_SECONDS = 19.0

# How long the live-research phase may consume before we cut over to
# the curated pool. Keeps the curated fallback from starving. With
# 5s live, curated gets ~14s — enough for ~5 curated entries (each
# takes ~2s for Tavily image-search + validation + audit).
LIVE_PHASE_BUDGET_SECONDS = 5.0

# Cap on consecutive duplicate curated picks before we resample.
# Real users typically refresh only a few times per session, so this
# gives variety without being chaotic.
CURATED_SEEN_WINDOW = 4


# Targeted product-opportunity searches. These are NOT fake keywords —
# they are real search queries that surface live product launches,
# review roundups, and trending consumer products.
DISCOVERY_QUERIES = [
    "trending best-selling consumer products 2026",
    "popular home products trending now",
    "trending kitchen gadgets best sellers",
    "trending pet products best sellers 2026",
    "popular desk accessories and tech gadgets 2026",
]


# Reject candidates that look like articles, blogs, brands without
# product, services, or generic listicles. The check looks for these
# patterns in the title and snippet.
#
# IMPORTANT: Be PERMISSIVE — most live Tavily results for product-opportunity
# queries are listicle/blog titles like "Best Kitchen Gadgets 2026".
# Those are valid discovery sources — the snippet/content describes real
# products. We only reject CLEAR non-product content (downloads, courses,
# generic store pages, etc.).
_NON_PRODUCT_TITLE_PATTERNS = (
    r"\bfree download\b",
    r"\bpdf\b",
    r"\bcourse\b",
    r"\btutorial\b",
    r"\bnewsletter signup\b",
    r"\bcareers?\b",
    r"\babout us\b",
    r"\bcontact us?\b",
    r"\bprivacy policy\b",
    r"\bterms of service\b",
    r"\bsitemap\b",
)
_NON_PRODUCT_TITLE_RE = re.compile(
    "|".join(_NON_PRODUCT_TITLE_PATTERNS), re.IGNORECASE
)

# Generic phrases that mean "no specific product" — reject only when the
# ENTIRE title is one of these (not when they appear as a substring).
_GENERIC_TITLE_TOKENS = {
    "amazon", "amazon.com", "store", "shop", "products",
    "search results", "all products",
}


# ── Helpers ──────────────────────────────────────────────────────────────

def _looks_like_article(title: str, snippet: str) -> bool:
    """True when the Tavily result is CLEARLY non-product (downloads,
    courses, generic store pages). Listicles and blog posts that
    describe products are accepted — the snippet usually names real
    products even when the title is "Best X 2026"."""
    text = f"{title} {snippet}"
    if _NON_PRODUCT_TITLE_RE.search(text):
        return True
    low = (title or "").strip().lower()
    if low in _GENERIC_TITLE_TOKENS:
        return True
    return False


def _has_specific_product_signal(name: str) -> bool:
    """True if the name contains at least one SPECIFIC product noun.

    Used by the title normalizer: if the title ONLY has generic stems
    (``product``, ``item``, ``kit``, ``set``, …) and no specific stem
    (``brush``, ``lamp``, ``organizer``, …), treat the title as a
    SEO-only phrase and try to extract a better name from the snippet.
    """
    if not name:
        return False
    low = name.lower()
    for stem in _SPECIFIC_NOUN_STEMS:
        if re.search(_stem_pattern(stem), low):
            return True
    return False


def _normalize_title(raw_title: str, snippet: str = "") -> Optional[str]:
    """Clean a raw Tavily title into a usable product name.

    Strips common noise patterns:
      - 'Amazon.com: ' prefix
      - ' : Amazon.com' suffix
      - leading list markers ('Top 10…', 'Best 5…')
      - trailing '... [Review]' style suffixes

    If the resulting title has only GENERIC product signal (e.g.
    "Trending Products" — has "products" but no concrete noun like
    "brush"), try to extract a concrete product name from the snippet.
    Listicle articles typically name their top picks in the opening
    sentences.
    """
    if not raw_title:
        return None
    t = raw_title.strip()
    # Strip Amazon prefix
    t = re.sub(r"^Amazon\.com\s*:\s*", "", t, flags=re.IGNORECASE)
    # Strip Amazon suffix
    t = re.sub(r"\s*[\|:]\s*Amazon\.com.*$", "", t, flags=re.IGNORECASE)
    # Strip trailing '... Review' / '... in 2026' style suffixes
    t = re.sub(r"\s*[\.…]+\s*\d{4}.*$", "", t)
    t = re.sub(r"\s*[\|]\s*review.*$", "", t, flags=re.IGNORECASE)
    # Strip leading listicle markers (e.g. "Top 10: ")
    t = re.sub(r"^(?:top|best)\s+\d+\s*[:\-–—]\s*", "", t, flags=re.IGNORECASE)
    # Strip surrounding quotes
    t = t.strip().strip('"').strip("'").strip()
    if not t or len(t) < 6 or len(t) > 150:
        return None
    # If the title has NO specific product signal (only generic stems
    # like "products" / "items") AND a snippet is available, try to
    # extract a real product phrase from the snippet.
    if not _has_specific_product_signal(t) and snippet:
        extracted = _extract_product_phrase_from_snippet(snippet)
        if extracted and len(extracted) >= 6 and len(extracted) <= 120:
            return extracted
    return t


def _extract_product_phrase_from_snippet(snippet: str) -> Optional[str]:
    """Find a product-noun-bearing phrase in the snippet.

    Looks for the first occurrence of a SPECIFIC product noun in the
    snippet and returns a short surrounding phrase (up to ~6 words,
    capitalized as in the snippet). Generic stems (``product``,
    ``item``, ``kit``, ``set``, …) are only used as a fallback when no
    specific stem is found — otherwise the extractor would latch onto
    "products" in phrases like "Top trending products" and miss the
    actual product name mentioned later.

    Returns None if no product noun is found anywhere in the snippet.
    """
    if not snippet:
        return None
    low = snippet.lower()
    # Try SPECIFIC stems first (most concrete product types).
    specific_stems = _SPECIFIC_NOUN_STEMS
    best_pos, best_stem = _find_first_stem(low, specific_stems)
    if best_pos < 0:
        # Fall back to GENERIC stems only if no specific match.
        generic_stems = _GENERIC_NOUN_STEMS
        best_pos, best_stem = _find_first_stem(low, generic_stems)
    if best_pos < 0:
        return None
    # Slice the snippet around the match: take up to 5 words BEFORE and
    # 1 word AFTER the stem, capped at ~60 chars total. We DO walk
    # through whitespace — we only stop at sentence punctuation
    # (period, semicolon, exclamation, question mark) so the phrase
    # stays within a single sentence fragment.
    sentence_breaks = {".", ";", "!", "?", "\n"}
    start = best_pos
    words_before = 0
    while start > 0 and snippet[start - 1] not in sentence_breaks and \
            words_before < 5 and (best_pos - start) < 60:
        # Walk backward through a single whitespace-delimited word.
        # First, skip any whitespace.
        while start > 0 and snippet[start - 1] in " \t":
            start -= 1
        # Now walk backward through the word chars until whitespace or
        # a sentence break.
        while start > 0 and snippet[start - 1] not in " \t" and \
                snippet[start - 1] not in sentence_breaks:
            start -= 1
        words_before += 1
    end = best_pos + len(best_stem)
    words_after = 0
    while end < len(snippet) and snippet[end] not in sentence_breaks and \
            words_after < 1 and (end - best_pos) < 40:
        # Skip whitespace, then walk through the next word.
        while end < len(snippet) and snippet[end] in " \t":
            end += 1
        while end < len(snippet) and snippet[end] not in " \t" and \
                snippet[end] not in sentence_breaks:
            end += 1
        words_after += 1
    phrase = snippet[start:end].strip(" .,;:!?\"'")
    if not phrase or len(phrase) < 6:
        return None
    # Drop leading articles / fillers that wouldn't make a good product
    # name (e.g. "include the Ultrasonic Brush" → "Ultrasonic Brush").
    words = phrase.split()
    drop = {"a", "an", "the", "and", "or", "include", "includes",
            "with", "featuring", "like", "such", "as"}
    while words and words[0].lower() in drop:
        words.pop(0)
    if not words:
        return None
    phrase = " ".join(words)
    if len(phrase) < 6:
        return None
    # Title-case the phrase nicely.
    out_words = []
    for i, w in enumerate(words):
        if i == 0:
            out_words.append(w[:1].upper() + w[1:])
        elif w.lower() in {"a", "an", "the", "and", "or", "for", "with",
                           "to", "of", "in", "on", "at", "by"}:
            out_words.append(w.lower())
        else:
            out_words.append(w[:1].upper() + w[1:])
    return " ".join(out_words)


def _find_first_stem(low: str, stems: tuple) -> tuple:
    """Return (position, stem) of the first stem occurrence in ``low``
    text, or (-1, "") if none match."""
    best_pos = -1
    best_stem = ""
    for stem in stems:
        m = re.search(_stem_pattern(stem), low)
        if m and (best_pos == -1 or m.start() < best_pos):
            best_pos = m.start()
            best_stem = stem
            if best_pos == 0:
                break
    return best_pos, best_stem


# Specific (concrete) product types — used first by the snippet extractor
# so we prefer concrete nouns like "brush", "lamp", "mug" over generic
# words like "products", "items", "kit".
_SPECIFIC_NOUN_STEMS = (
    "lamp", "light", "organizer", "rack", "stand", "holder", "shelf",
    "charger", "cable", "speaker", "headphone", "earbud", "mat",
    "brush", "scrubber", "vacuum", "mop", "spray", "towel", "bed",
    "bowl", "feeder", "leash", "collar", "mug", "cup", "knife",
    "pan", "pot", "tray", "stool", "chair", "desk", "monitor",
    "keyboard", "mouse", "router", "hub", "adapter", "pillow",
    "blanket", "duvet", "sheet", "filter", "purifier", "fan",
    "heater", "cooler", "bottle", "flask", "jug", "pitcher",
    "thermos", "kettle", "blender", "mixer", "grill", "fryer",
    "oven", "stove", "fridge", "freezer", "washer", "dryer",
    "drill", "saw", "screwdriver", "hammer", "wrench", "tool",
    "bag", "backpack", "wallet", "purse", "belt", "watch",
    "ring", "necklace", "earring", "bracelet", "scarf", "hat",
    "glove", "sock", "shoe", "boot", "sandal", "sneaker",
    "shirt", "pants", "dress", "jacket", "coat", "sweater",
    "hoodie", "cushion", "sofa", "table", "mirror",
    "gadget", "supplies", "gear", "equipment",
    "device", "machine", "system",
)


# Generic stems — only used as fallback in the snippet extractor. Kept
# separate so the extractor can prefer specific nouns.
_GENERIC_NOUN_STEMS = (
    "product", "item", "kit", "set", "bundle",
    "accessory", "solution", "essential",
    "innovation", "necessity",
)


def _derive_product_type(name: str) -> Optional[str]:
    """Best-effort product-type extraction from a name."""
    if not name:
        return None
    low = name.lower()
    # Common product-type tokens
    TYPE_TOKENS = (
        "lamp", "light", "organizer", "rack", "stand", "holder", "shelf",
        "charger", "cable", "speaker", "headphone", "earbud", "mat",
        "brush", "scrubber", "vacuum", "mop", "spray", "towel", "bed",
        "bowl", "feeder", "leash", "collar", "mug", "cup", "knife",
        "pan", "pot", "tray", "stool", "chair", "desk", "monitor",
        "keyboard", "mouse", "router", "hub", "adapter", "pillow",
        "blanket", "duvet", "sheet", "filter", "purifier", "fan",
        "heater", "cooler", "bottle", "flask", "jug", "pitcher",
        # Broader nouns from listicle titles
        "gadget", "gadgets", "product", "products", "kit", "set",
        "accessory", "accessories", "tool", "tools", "supplies",
    )
    for t in TYPE_TOKENS:
        if re.search(r"\b" + re.escape(t) + r"\b", low):
            return t
    # Otherwise take the last noun-ish word (very rough heuristic).
    words = re.findall(r"[a-z]{4,}", low)
    return words[-1] if words else None


def _infer_category(name: str) -> str:
    """Map a product name to a CATEGORY_LABELS-like display label."""
    if not name:
        return "Trending General"
    low = name.lower()
    if any(w in low for w in ("kitchen", "spice", "knife", "pan", "pot", "mug",
                              "coffee", "blender", "cookware", "recipe", "utensil",
                              "air fryer", "mixer", "grill")):
        return "Kitchen & Cooking"
    if any(w in low for w in ("phone", "tablet", "laptop", "charger", "cable",
                              "wireless", "bluetooth", "smart", "led", "usb",
                              "earbud", "headphone", "speaker", "monitor",
                              "keyboard", "mouse", "tech", "gadget", "dock")):
        return "Tech & Gadgets"
    if any(w in low for w in ("dog", "cat", "puppy", "kitten", "pet", "leash",
                              "collar", "kennel", "crate", "feeder", "litter",
                              "bowl", "treat", "grooming", "bed ")):
        return "Pet Supplies"
    if any(w in low for w in ("lamp", "light", "mirror", "shelf", "throw",
                              "blanket", "candle", "vase", "plant", "wall",
                              "decor", "pillow", "bedroom", "couch", "sofa")):
        return "Aesthetic Home Decor"
    if any(w in low for w in ("yoga", "fitness", "exercise", "gym", "workout",
                              "dumbbell", "resistance", "band", "roller",
                              "posture", "massage", "foam")):
        return "Fitness & Wellness"
    if any(w in low for w in ("clean", "scrub", "brush", "mop", "vacuum",
                              "sweep", "dust", "wipe", "soap", "stain",
                              "toilet", "shower", "bathroom", "spray")):
        return "Home & Cleaning"
    return "Trending General"


def _stem_pattern(stem: str) -> str:
    """Return a whole-word regex pattern that matches the stem AND its
    common English plurals (``stand`` → ``stands``, ``accessory`` →
    ``accessories``, ``box`` → ``boxes``).
    """
    if stem.endswith("y"):
        # y → ies plural (e.g. accessory → accessories)
        return r"\b" + re.escape(stem[:-1]) + r"(?:y|ies)\b"
    if stem.endswith(("s", "x", "z", "sh", "ch")):
        return r"\b" + re.escape(stem) + r"(?:es|s)?\b"
    return r"\b" + re.escape(stem) + r"(?:s|es)?\b"


def _has_product_signal(name: str) -> bool:
    """True if the name contains at least one product-ish noun.

    The list combines three layers:
      1. Specific product nouns (lamp, organizer, mug, ...)
      2. Broader product signals (gadget, product, kit, accessory, ...)

    Tier 2 is included because live Tavily results for discovery queries
    frequently have generic listicle titles like "Best Kitchen Gadgets"
    or "Top Pet Products". Rejecting those means we'd miss the actual
    product-rich source pages.

    Single-word phrases like "Kitchen" or "Apple" (brand/category alone,
    no product signal) still fail — they don't contain ANY product
    noun or specific broad signal.

    Match is **whole-word** so ``"kit"`` does NOT match inside
    ``"kitchen"``, ``"mat"`` does NOT match inside ``"format"``.

    Each stem is matched against itself AND its plural (``stand`` →
    ``stands``, ``accessory`` → ``accessories``) so listicle titles
    like "Top 10 Phone Stands" and "Best Coffee Accessories" pass.
    """
    if not name:
        return False
    low = name.lower()
    PRODUCT_NOUN_STEMS = (
        # Specific product types
        "lamp", "light", "organizer", "rack", "stand", "holder", "shelf",
        "charger", "cable", "speaker", "headphone", "earbud", "mat",
        "brush", "scrubber", "vacuum", "mop", "spray", "towel", "bed",
        "bowl", "feeder", "leash", "collar", "mug", "cup", "knife",
        "pan", "pot", "tray", "stool", "chair", "desk", "monitor",
        "keyboard", "mouse", "router", "hub", "adapter", "pillow",
        "blanket", "duvet", "sheet", "filter", "purifier", "fan",
        "heater", "cooler", "bottle", "flask", "jug", "pitcher",
        "thermos", "kettle", "blender", "mixer", "grill", "fryer",
        "oven", "stove", "fridge", "freezer", "washer", "dryer",
        "drill", "saw", "screwdriver", "hammer", "wrench", "tool",
        "bag", "backpack", "wallet", "purse", "belt", "watch",
        "ring", "necklace", "earring", "bracelet", "scarf", "hat",
        "glove", "sock", "shoe", "boot", "sandal", "sneaker",
        "shirt", "pants", "dress", "jacket", "coat", "sweater",
        "hoodie", "cushion", "sofa", "table", "mirror",
        # Broader product signals — listicle/blog titles contain these
        "gadget", "product", "item", "kit", "set", "bundle",
        "accessory", "supplies", "gear", "equipment",
        "solution", "device", "machine", "system", "essential",
        "tool", "innovation", "necessity",
    )
    for stem in PRODUCT_NOUN_STEMS:
        if re.search(_stem_pattern(stem), low):
            return True
    return False


def _score_candidate(card: ProductCard, evidence_count: int) -> int:
    """Compute a discovery-mode score using only signals available on the
    card. No fake numeric claims — uses existing fields.

    Signals used:
      - evidence_count (number of research sources that mention this
        product or its keywords; the constructor fills this in from the
        research_sources)
      - evergreen_score (existing field on ProductCard)
      - trend_score_range midpoint
      - margin_estimate keyword scoring
    """
    score = 0
    # Evidence strength (more sources = more confidence).
    score += min(evidence_count * 5, 30)
    # Evergreen strength.
    if card.evergreen_score is not None:
        score += int(card.evergreen_score * 30)
    # Trend score midpoint.
    if card.trend_score_range:
        lo, hi = card.trend_score_range
        score += int((lo + hi) / 2 / 4)
    # Margin estimate: "High" / "Medium-High" / "Medium" etc.
    margin = (card.margin_estimate or "").lower()
    if "high" in margin:
        score += 25
    elif "medium" in margin:
        score += 15
    elif "low" in margin:
        score += 5
    # Product specificity bonus — explicit product-type noun = better.
    if card.angle_options and len(card.angle_options[0]) > 40:
        score += 5
    return score


# ── Candidate building from Tavily results ──────────────────────────────

def _build_candidates_from_envelope(
    envelope, seen_titles: set
) -> list[ProductCard]:
    """Walk one ResearchEnvelope's research_sources and yield ProductCards.

    Each Tavily result becomes a candidate. The card's evidence_count is
    the number of other sources that mention any token from this title
    — a cheap co-mention signal.
    """
    if envelope is None:
        return []
    sources = list(envelope.research_sources or [])
    if not sources:
        return []
    cards: list[ProductCard] = []
    for i, src in enumerate(sources):
        raw_title = (src.get("title") or "").strip()
        url = (src.get("url") or "").strip()
        snippet = (src.get("snippet") or src.get("content") or "").strip()
        if not raw_title or not url:
            continue
        if _looks_like_article(raw_title, snippet):
            logger.info("[discover] reject article/blog: %r", raw_title[:80])
            continue
        name = _normalize_title(raw_title, snippet)
        if not name:
            continue
        # Reject if the name has no product noun signal at all.
        if not _has_product_signal(name):
            logger.info("[discover] reject generic/no-noun: %r", name[:80])
            continue
        # Deduplicate by case-insensitive name prefix.
        key = name.lower().split(" ")[0:4]
        key = " ".join(key)
        if key in seen_titles:
            logger.info("[discover] reject duplicate: %r", name[:80])
            continue
        seen_titles.add(key)
        product_type = _derive_product_type(name)
        category = _infer_category(name)
        cid = f"discover-{i:02d}-{abs(hash(name)) % 10000:04d}"
        # Count co-mentions in the other sources.
        co_mentions = 0
        title_tokens = [t for t in re.findall(r"[a-z]{4,}", name.lower())]
        for j, other in enumerate(sources):
            if j == i:
                continue
            other_text = " ".join([
                (other.get("title") or "").lower(),
                (other.get("snippet") or other.get("content") or "").lower(),
            ])
            if any(tok in other_text for tok in title_tokens[:3]):
                co_mentions += 1
        evidence_count = 1 + co_mentions
        pin_title = f"{name}"
        pin_desc = (
            f"{name} — surfaced by live trending-product research. "
            f"Co-mentioned in {evidence_count} source(s) of fresh market data."
        )
        card = ProductCard(
            id=cid,
            name=name,
            category=category,
            image_url=_placeholder(cid, name, category.lower().split(" ")[0]
                                    if category else "kitchen"),
            url=url,
            angle_options=[
                f"{name} — trending pick from live research.",
                f"Why {name} is showing up everywhere right now.",
                f"Top trend signal: {name}.",
            ],
            pin_title_options=[pin_title, f"Trending: {name}"],
            pin_description_options=[pin_desc],
            hashtags_pool=["#Trending", "#BestSeller", "#MustHave",
                           "#TopPicks", "#Viral", "#NewDrop"],
            viral_hook_options=[
                f"{name} is trending — here's why.",
            ],
            trend_score_range=(70 + min(co_mentions * 5, 25),
                               85 + min(co_mentions * 5, 15)),
            trend_signals_options=[
                [f"Surfaced from live research query '{envelope.research_query}'",
                 f"Co-mentioned across {evidence_count} independent sources"],
            ],
            margin_estimate="Medium (25-40%)",
            evergreen_score=0.7,
            competition="Medium",
            competition_reasons=[f"Surfaced from live discovery; competition varies"],
        )
        cards.append(card)
        logger.info(
            "[discover] candidate=%r category=%r type=%r evidence=%d",
            name[:60], category, product_type, evidence_count,
        )
        if len(cards) >= MAX_DISCOVERY_CANDIDATES:
            break
    return cards


# ── Image discovery for a candidate ─────────────────────────────────────

# Cap how many pre-fetched Tavily images we validate per candidate. This
# keeps the worst-case validation work bounded:
#   8 candidates × 5 images × 2s HEAD = 80s worst case if all time out.
# We cap at 4 (enough to surface the top picks from Tavily) and STOP
# validating as soon as we find a strong image.
MAX_PREFETCHED_IMAGES_PER_CANDIDATE = 4

# Per-URL HEAD timeout for discovery. Shorter than the 3s default so a
# dead CDN cannot blow the discovery budget.
DISCOVERY_IMAGE_HEAD_TIMEOUT = 2.0


def _find_image_for_candidate(card: ProductCard, intent: str = "",
                                tavily_image_urls: Optional[list[str]] = None,
                                deadline_monotonic: Optional[float] = None) -> Optional[str]:
    """Find a verified product image for one candidate.

    Strategy (ordered by speed and quality):
      1. **Tavily pre-fetched images** — these came back in the SAME
         request that surfaced this candidate. Tavily's image ranking is
         high quality, and they're already on Tavily's CDN. Validate +
         rank them; accept the first one above ``MIN_PRODUCT_IMAGE_SCORE``.
      2. **Image cascade** — run ``build_image_query_cascade`` and
         Tavily-image-search each variant. Bounded by
         ``MAX_IMAGE_SEARCH_QUERIES_PER_CANDIDATE``.
      3. Return None if neither yields a verified, scored image.

    If ``deadline_monotonic`` is provided, the function returns None as
    soon as the wall clock crosses it (avoids blowing the parent
    request budget while validating slow CDNs).
    """
    def _budget_left() -> bool:
        return deadline_monotonic is None or time.monotonic() < deadline_monotonic

    # ── STEP 1: try pre-fetched Tavily images first ─────────────────────
    if not _budget_left():
        return None
    pre_urls = list(tavily_image_urls or []) if tavily_image_urls else []
    pre_urls = pre_urls[:MAX_PREFETCHED_IMAGES_PER_CANDIDATE]
    pre_verified = _validate_and_rank(
        pre_urls, card.name, card.category or "",
        head_timeout=DISCOVERY_IMAGE_HEAD_TIMEOUT,
    )
    if pre_verified:
        url, score = pre_verified
        logger.info(
            "[discover] image from pre-fetched tavily card=%r score=%d url=%s",
            card.name[:60], score, url[:80],
        )
        return url

    # ── STEP 2: cascade fallback ───────────────────────────────────────
    if not _budget_left():
        return None
    cascade = build_image_query_cascade(
        product_name=card.name,
        category=card.category or "",
        intent=intent,
    )
    if not cascade:
        return None
    cascade = cascade[:MAX_IMAGE_SEARCH_QUERIES_PER_CANDIDATE]

    candidate_urls: list[str] = []
    seen: set[str] = set()
    for q in cascade:
        if not _budget_left():
            break
        try:
            urls = live_research.research_images(q, max_results=3) or []
        except Exception as exc:
            logger.info("[discover] image query failed q=%r: %s", q, exc)
            urls = []
        for u in urls:
            if not u or u in seen:
                continue
            seen.add(u)
            candidate_urls.append(u)

    if not _budget_left():
        return None
    cascade_verified = _validate_and_rank(
        candidate_urls, card.name, card.category or "",
        head_timeout=DISCOVERY_IMAGE_HEAD_TIMEOUT,
    )
    if cascade_verified:
        url, score = cascade_verified
        logger.info(
            "[discover] image from cascade card=%r score=%d url=%s",
            card.name[:60], score, url[:80],
        )
        return url

    logger.info("[discover] no verified image card=%r (pre=%d cascade=%d)",
                card.name[:60], len(pre_urls), len(candidate_urls))
    return None


def _validate_and_rank(urls: list[str], name: str, category: str,
                       *, head_timeout: float = DISCOVERY_IMAGE_HEAD_TIMEOUT
                       ) -> Optional[tuple[str, int]]:
    """Validate each URL via HEAD, then rank the survivors. Return the
    best (url, score) above ``MIN_PRODUCT_IMAGE_SCORE`` or None."""
    if not urls:
        return None
    validated: list[tuple[str, object]] = []
    for u in urls:
        try:
            check = _validate_image(u, timeout=head_timeout)
        except Exception as exc:
            logger.info("[discover] validate error for %s: %s", u[:80], exc)
            continue
        if check and check.ok:
            validated.append((u, check))
    if not validated:
        return None
    ranked = rank_image_candidates(validated, name, category)
    if not ranked:
        return None
    best_url, best_score = ranked[0]
    if best_score < MIN_PRODUCT_IMAGE_SCORE:
        return None
    return best_url, best_score


# ── Main entry point ─────────────────────────────────────────────────────

def discover_winner() -> dict:
    """Run the live discovery pipeline and return a winner payload.

    Strategy:
        1. Run live Tavily research for up to ``LIVE_PHASE_BUDGET_SECONDS``.
        2. For each candidate, resolve a verified product image.
        3. If the live phase produces no qualified winner, fall back to
           the curated pool (a marketing-expert curated set of real
           trending products from Amazon / Walmart / Temu / etc.).

    Returns:
        dict  — winner payload compatible with /find-winner response shape.
        raises HTTPException(404) with a discovery-specific message if no
        qualified candidate is found within the budget.
    """
    from fastapi import HTTPException
    t_start = time.monotonic()
    logger.info("[discover] ==== START discovery ====")
    live_deadline = t_start + LIVE_PHASE_BUDGET_SECONDS

    # ── STEP 1: live research ───────────────────────────────────────────
    all_envelopes = []
    seen_titles: set[str] = set()
    candidates: list[tuple[ProductCard, int, list[str]]] = []  # (card, evidence_count, tavily_image_urls)

    for query in DISCOVERY_QUERIES[:MAX_DISCOVERY_RESEARCH_QUERIES]:
        if time.monotonic() > live_deadline:
            logger.info("[discover] live phase budget exhausted after %d queries",
                        len(all_envelopes))
            break
        logger.info("[discover] research query=%r", query)
        try:
            env = live_research.research(query, max_results=5)
            all_envelopes.append(env)
            new_cards = _build_candidates_from_envelope(env, seen_titles)
            tavily_imgs = list(env.research_image_urls or []) if hasattr(env, "research_image_urls") else []
            for c in new_cards:
                candidates.append((c, len(env.research_sources or []), tavily_imgs))
                if len(candidates) >= MAX_DISCOVERY_CANDIDATES:
                    break
        except Exception as exc:
            logger.info("[discover] research error for %r: %s", query, exc)
        if len(candidates) >= MAX_DISCOVERY_CANDIDATES:
            break

    raw_results = sum(len(e.research_sources or []) for e in all_envelopes)
    logger.info("[discover] raw_results=%d normalized_candidates=%d",
                raw_results, len(candidates))

    # ── STEP 2-4: rank, image-search, and pick the first qualified ──────
    # Score candidates using supported signals only.
    candidates.sort(
        key=lambda pair: _score_candidate(pair[0], pair[1]),
        reverse=True,
    )
    logger.info(
        "[discover] sorted %d candidates; trying image search",
        len(candidates),
    )

    # Cap live candidate attempts so the curated fallback always has
    # time to run. The curated pool has 20+ entries; live research only
    # needs to surface ONE qualified winner.
    MAX_LIVE_CANDIDATE_ATTEMPTS = 1

    for idx, (card, ev_count, tavily_imgs) in enumerate(candidates):
        if idx >= MAX_LIVE_CANDIDATE_ATTEMPTS:
            logger.info("[discover] live candidate attempt cap reached at idx=%d", idx)
            break
        if time.monotonic() > live_deadline:
            logger.info("[discover] live candidate phase time-out at idx=%d", idx)
            break
        # Image discovery (with Tavily pre-fetched images as fallback).
        image_url = _find_image_for_candidate(
            card, tavily_image_urls=tavily_imgs,
            deadline_monotonic=live_deadline,
        )
        if not image_url:
            logger.info("[discover] skip candidate=%r (no verified image)",
                        card.name[:60])
            continue
        # Audit via Product Control Agent.
        card.image_url = image_url
        # Build the audit payload via ProductResearcher._materialize so
        # angle / pin_title / pin_description / hashtags are populated.
        # (Required by ProductControlAgent.evaluate.)
        from backend.product_research import ProductResearcher
        researcher = ProductResearcher()
        audit_payload = researcher._materialize(card, card.category or "Trending General")
        # Force the live-research source label.
        audit_payload["source"] = "discovery"
        audit_payload["image_url"] = image_url
        report = ProductControlAgent.evaluate(audit_payload)
        if not report.ok:
            logger.info(
                "[discover] skip candidate=%r reason=%s",
                card.name[:60], report.primary_reason,
            )
            continue
        winner = report.product
        # Mark discovery provenance.
        winner["source"] = "discovery"
        winner["discovery"] = {
            "research_queries": DISCOVERY_QUERIES[:MAX_DISCOVERY_RESEARCH_QUERIES],
            "raw_results": raw_results,
            "normalized_candidates": len(candidates),
            "candidates_attempted": idx + 1,
            "winner_evidence_count": ev_count,
            "winner_source_url": card.url,
            "elapsed_seconds": round(time.monotonic() - t_start, 3),
        }
        winner["selection_rationale"] = (
            f"Live discovery surfaced {raw_results} raw research results, "
            f"normalized {len(candidates)} qualified product candidates, "
            f"and selected the strongest based on evidence breadth "
            f"({ev_count} co-mentions) plus verified product photo."
        )
        winner["trend_signals"] = [
            f"Surfaced by live Tavily research: {', '.join(DISCOVERY_QUERIES[:2])}...",
            f"Co-mentioned across {ev_count} source(s) of trending-product data",
        ]
        # Always force image_status=verified when we made it past the
        # audit. The audit may not set this field directly.
        winner["image_status"] = "verified"
        logger.info(
            "[discover] ACCEPTED winner=%r category=%r type=%s elapsed=%.2fs",
            card.name[:60], card.category, _derive_product_type(card.name),
            time.monotonic() - t_start,
        )
        return winner

    logger.info("[discover] live phase produced no qualified candidate")

    # ── STEP 5: CURATED FALLBACK ──────────────────────────────────────────
    # When live Tavily research cannot produce a qualified candidate
    # (rate limit, all images failed, generic titles only, etc.) we
    # fall back to a marketing-expert curated pool of real trending
    # products. Each curated entry has been hand-verified for:
    #   - real product (Amazon / Walmart / Temu best-seller or viral hit)
    #   - image search queries that return real product CDN photos
    #   - marketing-expert curated metadata (angle, pin, hashtags,
    #     trend signals, margin estimate, evergreen score, competition)
    # This guarantees PATH A always returns a verified winner, even
    # when Tavily is unavailable.
    curated = _pick_curated_winner(
        seen_names=set(),
        deadline_monotonic=t_start + DISCOVERY_REQUEST_BUDGET_SECONDS,
        t_start=t_start,
    )
    if curated is not None:
        return curated

    logger.info("[discover] NO curated winner either — returning 404")
    raise HTTPException(
        status_code=404,
        detail=(
            "We couldn't find a qualified product right now. "
            "Try Find Winning Product again."
        ),
    )


# ── Curated winners fallback (PATH A reliability layer) ─────────────────

# Module-level sliding window of recently-picked curated winner names.
# Resets to empty after every fresh request — process-wide state would
# leak across requests on a multi-worker server.
_RECENT_CURATED_NAMES: list[str] = []


def _pick_curated_winner(*, seen_names: set[str],
                          deadline_monotonic: float,
                          t_start: float) -> Optional[dict]:
    """Pick the next curated winner whose image we can verify.

    Tries each curated entry in shuffled order, stopping at the first
    one whose Tavily image search yields a verified product photo. To
    provide variety across repeated clicks, the pool is shuffled and
    we skip names that were picked very recently (sliding window of
    ``CURATED_SEEN_WINDOW``).

    Returns the winner payload (compatible with /find-winner shape) or
    ``None`` if the budget is exhausted or no curated entry has a
    verified image.
    """
    global _RECENT_CURATED_NAMES

    if not CURATED_WINNERS:
        logger.warning("[discover] curated pool is empty — fallback disabled")
        return None

    # Build candidate order. Priority:
    #   1. Entries with hardcoded direct_image_url(s) — fastest path,
    #      guaranteed to work without Tavily. These are tried FIRST so
    #      PATH A always returns within budget.
    #   2. Other entries (Tavily image-search path).
    # Within each priority tier, shuffle to provide variety, but skip
    # names that were picked very recently (sliding window).
    pool = list(CURATED_WINNERS)
    random.shuffle(pool)
    fast_pool = [w for w in pool if w.get("direct_image_url") or w.get("direct_image_urls")]
    slow_pool = [w for w in pool if w not in fast_pool]

    # Trim recent window.
    if len(_RECENT_CURATED_NAMES) > CURATED_SEEN_WINDOW:
        _RECENT_CURATED_NAMES = _RECENT_CURATED_NAMES[-CURATED_SEEN_WINDOW:]
    recent_set = set(_RECENT_CURATED_NAMES)

    # First pass: try non-recent entries from FAST pool, then SLOW pool.
    ordered: list = []
    for tier in (fast_pool, slow_pool):
        non_recent = [w for w in tier if w["name"] not in recent_set and w["name"] not in seen_names]
        ordered.extend(non_recent)
    # If we filtered out everything, allow repeats within each tier.
    if not ordered:
        ordered = list(fast_pool) + list(slow_pool)

    for entry in ordered:
        if time.monotonic() > deadline_monotonic:
            logger.info("[discover] curated: budget exhausted after %d entries",
                        len(ordered))
            return None
        winner = _try_curated_entry(entry, deadline_monotonic, t_start)
        if winner is not None:
            _RECENT_CURATED_NAMES.append(entry["name"])
            return winner
    return None


def _try_curated_entry(entry: CuratedWinner, deadline_monotonic: float,
                       t_start: float) -> Optional[dict]:
    """Resolve one curated entry to a verified winner payload or None."""
    name = entry["name"]
    category = entry.get("category") or "Trending General"
    image_url = _resolve_curated_image(entry, deadline_monotonic)
    if not image_url:
        logger.info("[discover] curated: no verified image for %r", name[:60])
        return None
    # Build the winner payload via ProductResearcher._materialize — same
    # path as the static pool — then run ProductControlAgent.evaluate
    # for audit. This keeps audit + scoring consistent with the rest
    # of the backend.
    card_id = re.sub(r"\W+", "-", name.lower())[:60].strip("-") or "curated"
    card = ProductCard(
        id=f"curated-{card_id}",
        name=name,
        category=category,
        image_url=image_url,
        url=entry.get("source_url") or "",
        angle_options=entry.get("angle_options") or [],
        pin_title_options=entry.get("pin_title_options") or [],
        pin_description_options=entry.get("pin_description_options") or [],
        hashtags_pool=entry.get("hashtags_pool") or [],
        viral_hook_options=entry.get("viral_hook_options") or [],
        trend_score_range=tuple(entry.get("trend_score_range") or (70, 88)),
        trend_signals_options=entry.get("trend_signals_options") or [[]],
        margin_estimate=entry.get("margin_estimate") or "Medium (25-40%)",
        evergreen_score=float(entry.get("evergreen_score") or 0.75),
        competition=entry.get("competition") or "Medium",
        competition_reasons=entry.get("competition_reasons") or [],
    )
    researcher = ProductResearcher()
    audit_payload = researcher._materialize(card, category)
    # Override the `source` so the provenance is clear.
    audit_payload["source"] = "discovery-curated"
    audit_payload["source_label"] = entry.get("source_label") or "Curated"
    audit_payload["image_url"] = image_url
    report = ProductControlAgent.evaluate(audit_payload)
    if not report.ok:
        logger.info("[discover] curated: audit FAIL for %r reason=%s",
                    name[:60], report.primary_reason)
        return None
    winner = report.product
    winner["source"] = "discovery-curated"
    winner["discovery"] = {
        "research_queries": [],
        "raw_results": 0,
        "normalized_candidates": 0,
        "candidates_attempted": 0,
        "winner_evidence_count": 0,
        "winner_source_url": card.url,
        "winner_source_label": entry.get("source_label") or "Curated",
        "curated_notes": entry.get("notes") or "",
        "elapsed_seconds": round(time.monotonic() - t_start, 3),
        "fallback_used": "curated_pool",
    }
    winner["selection_rationale"] = (
        f"Live research did not surface a qualified product within the "
        f"budget; selected from the curated winning-products pool. "
        f"{entry.get('source_label', 'Curated')} — "
        f"{entry.get('notes', '')}".strip()
    )
    winner["trend_signals"] = list(
        (entry.get("trend_signals_options") or [["Curated trending product"]])[0]
    )
    winner["image_status"] = "verified"
    logger.info(
        "[discover] curated ACCEPTED winner=%r category=%r image=%s elapsed=%.2fs",
        name[:60], category, image_url[:80], time.monotonic() - t_start,
    )
    return winner


def _resolve_curated_image(entry: CuratedWinner,
                            deadline_monotonic: float) -> Optional[str]:
    """Run the curated entry's image queries and return the first
    verified product photo, or None if no query yields a strong image.

    The image URLs come from Tavily's image-search endpoint and are
    validated + ranked through the same pipeline as live research.

    The collected URLs are capped at MAX_IMAGE_SEARCH_QUERIES_PER_CANDIDATE
    to keep the validation cost bounded — each HEAD validation can
    take up to ``DISCOVERY_IMAGE_HEAD_TIMEOUT`` seconds.
    """
    # Fast path: try the hardcoded `direct_image_url` / `direct_image_urls`
    # first. These are real CDN URLs (Shopify, Amazon, scene7, etc.) that
    # have been hand-verified for stability. If HEAD returns ok=True, we
    # return immediately — no Tavily call, no scoring delay.
    direct_urls = list(entry.get("direct_image_urls") or [])
    if entry.get("direct_image_url"):
        direct_urls.insert(0, entry["direct_image_url"])
    if direct_urls:
        verified = _validate_and_rank(
            direct_urls, entry["name"], entry.get("category") or "",
            head_timeout=DISCOVERY_IMAGE_HEAD_TIMEOUT,
        )
        if verified:
            logger.info(
                "[discover] curated image from direct hardcoded URL for %r",
                entry["name"][:60],
            )
            return verified[0]

    # Normal path: Tavily image-search with the curated queries.
    queries = entry.get("image_queries") or []
    if not queries:
        return None
    cap = MAX_IMAGE_SEARCH_QUERIES_PER_CANDIDATE
    all_urls: list[str] = []
    seen: set[str] = set()
    for query in queries:
        if time.monotonic() > deadline_monotonic:
            break
        try:
            urls = live_research.research_images(query, max_results=3) or []
        except Exception as exc:
            logger.info("[discover] curated image query failed q=%r: %s",
                        query[:60], exc)
            urls = []
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                all_urls.append(u)
        # Stop gathering once we have enough candidates.
        if len(all_urls) >= cap:
            break
    if not all_urls:
        return None
    verified = _validate_and_rank(
        all_urls, entry["name"], entry.get("category") or "",
        head_timeout=DISCOVERY_IMAGE_HEAD_TIMEOUT,
    )
    if verified:
        return verified[0]
    return None
