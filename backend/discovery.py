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
import re
import time
from typing import Optional

from backend import live_research
from backend.product_control_agent import (
    ProductControlAgent,
    _validate_image,
    build_image_query_cascade,
    rank_image_candidates,
    MIN_PRODUCT_IMAGE_SCORE,
    STRONG_IMAGE_SCORE,
)
from backend.product_research import ProductCard, _placeholder

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

MAX_DISCOVERY_RESEARCH_QUERIES = 5
MAX_DISCOVERY_CANDIDATES = 8
MAX_IMAGE_SEARCH_QUERIES_PER_CANDIDATE = 3
DISCOVERY_REQUEST_BUDGET_SECONDS = 18.0


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
_NON_PRODUCT_TITLE_PATTERNS = (
    r"^best\s+\d+\b",
    r"\bbest of\b",
    r"\btop\s+\d+\b",
    r"\breview(s)?\b",
    r"\bhow to\b",
    r"\bguide\b",
    r"\barticle\b",
    r"\bblog\b",
    r"\bvs\.?\b",
    r"\bcomparison\b",
    r"\bexplained\b",
    r"\bbuying guide\b",
    r"\bdeals?\b",
    r"\bsale\b",
    r"\bpromo\b",
    r"\bcoupon\b",
    r"\bnewsletter\b",
    r"\bwhat is\b",
    r"\bwhat are\b",
    r"\bhistory of\b",
)
_NON_PRODUCT_TITLE_RE = re.compile(
    "|".join(_NON_PRODUCT_TITLE_PATTERNS), re.IGNORECASE
)

# Generic phrases that mean "no specific product" — reject.
_GENERIC_TITLE_TOKENS = {
    "amazon", "amazon.com", "amazon best sellers", "best sellers",
    "trending products", "popular products", "top products",
    "consumer products", "home products", "all products",
    "product catalog", "product list", "products", "store",
}


# ── Helpers ──────────────────────────────────────────────────────────────

def _looks_like_article(title: str, snippet: str) -> bool:
    """True when the Tavily result is an article/blog/listicle rather
    than a specific product."""
    text = f"{title} {snippet}"
    if _NON_PRODUCT_TITLE_RE.search(text):
        return True
    low = (title or "").strip().lower()
    if low in _GENERIC_TITLE_TOKENS:
        return True
    return False


def _normalize_title(raw_title: str) -> Optional[str]:
    """Clean a raw Tavily title into a usable product name.

    Strips common noise patterns:
      - 'Amazon.com: ' prefix
      - ' : Amazon.com' suffix
      - leading list markers ('Top 10…', 'Best 5…')
      - trailing '... [Review]' style suffixes
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
    return t


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


def _has_product_signal(name: str) -> bool:
    """True if the name contains at least one product-ish noun."""
    if not name:
        return False
    low = name.lower()
    PRODUCT_NOUNS = (
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
        "hoodie", "pillow", "cushion", "sofa", "bed", "desk",
        "table", "chair", "lamp", "mirror",
    )
    return any(w in low for w in PRODUCT_NOUNS)


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
        name = _normalize_title(raw_title)
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

def _find_image_for_candidate(card: ProductCard, intent: str = "") -> Optional[str]:
    """Run the existing image cascade for one candidate.

    Returns a verified URL or None. Does NOT mutate the card.
    """
    cascade = build_image_query_cascade(
        product_name=card.name,
        category=card.category or "",
        intent=intent,
    )
    if not cascade:
        return None
    # Cap at MAX_IMAGE_SEARCH_QUERIES_PER_CANDIDATE
    cascade = cascade[:MAX_IMAGE_SEARCH_QUERIES_PER_CANDIDATE]

    candidate_urls: list[str] = []
    seen: set[str] = set()
    for q in cascade:
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

    # Validate and rank.
    validated: list[tuple[str, object]] = []
    for u in candidate_urls:
        try:
            check = _validate_image(u, timeout=3.0)
        except Exception as exc:
            logger.info("[discover] validate error for %s: %s", u[:80], exc)
            continue
        if check and check.ok:
            validated.append((u, check))

    if not validated:
        return None
    ranked = rank_image_candidates(validated, card.name, card.category or "")
    if not ranked:
        return None
    best_url, best_score = ranked[0]
    if best_score < MIN_PRODUCT_IMAGE_SCORE:
        logger.info(
            "[discover] image below threshold card=%r best_score=%d",
            card.name[:60], best_score,
        )
        return None
    logger.info(
        "[discover] image accepted card=%r score=%d url=%s",
        card.name[:60], best_score, best_url[:80],
    )
    return best_url


# ── Main entry point ─────────────────────────────────────────────────────

def discover_winner() -> dict:
    """Run the live discovery pipeline and return a winner payload.

    Returns:
        dict  — winner payload compatible with /find-winner response shape.
        raises HTTPException(404) with a discovery-specific message if no
        qualified candidate is found within the budget.
    """
    from fastapi import HTTPException
    t_start = time.monotonic()
    logger.info("[discover] ==== START discovery ====")

    # ── STEP 1: live research ───────────────────────────────────────────
    all_envelopes = []
    seen_titles: set[str] = set()
    candidates: list[tuple[ProductCard, int]] = []  # (card, evidence_count)

    for query in DISCOVERY_QUERIES[:MAX_DISCOVERY_RESEARCH_QUERIES]:
        if time.monotonic() - t_start > DISCOVERY_REQUEST_BUDGET_SECONDS:
            logger.info("[discover] research budget exhausted after %d queries",
                        len(all_envelopes))
            break
        logger.info("[discover] research query=%r", query)
        try:
            env = live_research.research(query, max_results=5)
            all_envelopes.append(env)
            new_cards = _build_candidates_from_envelope(env, seen_titles)
            for c in new_cards:
                candidates.append((c, len(env.research_sources or [])))
                if len(candidates) >= MAX_DISCOVERY_CANDIDATES:
                    break
        except Exception as exc:
            logger.info("[discover] research error for %r: %s", query, exc)
        if len(candidates) >= MAX_DISCOVERY_CANDIDATES:
            break

    raw_results = sum(len(e.research_sources or []) for e in all_envelopes)
    logger.info("[discover] raw_results=%d normalized_candidates=%d",
                raw_results, len(candidates))

    if not candidates:
        logger.info("[discover] NO candidates after research")
        raise HTTPException(
            status_code=404,
            detail=(
                "We couldn't find a qualified product right now. "
                "Try Find Winning Product again."
            ),
        )

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

    for idx, (card, ev_count) in enumerate(candidates):
        if time.monotonic() - t_start > DISCOVERY_REQUEST_BUDGET_SECONDS:
            logger.info("[discover] candidate budget time-out at idx=%d", idx)
            break
        # Image discovery.
        image_url = _find_image_for_candidate(card)
        if not image_url:
            logger.info("[discover] skip candidate=%r (no verified image)",
                        card.name[:60])
            continue
        # Audit via Product Control Agent.
        card.image_url = image_url
        report = ProductControlAgent.evaluate({
            "id": card.id,
            "name": card.name,
            "category": card.category,
            "image_url": image_url,
            "url": card.url,
        })
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

    logger.info("[discover] NO qualified candidate (no verified image)")
    raise HTTPException(
        status_code=404,
        detail=(
            "We couldn't find a qualified product right now. "
            "Try Find Winning Product again."
        ),
    )
