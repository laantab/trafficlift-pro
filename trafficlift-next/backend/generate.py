"""
trafficlift_pro/backend/generate.py
AI Content Generation Module — v2.0

Generates marketing assets for Organic ($0) and Paid Ad modes across:
  Pinterest, TikTok/Reels, YouTube Shorts, Twitter/X Threads, Meta Ads, TikTok Ads.

AI backends:
  OpenAI GPT-4o  — when OPENAI_API_KEY is set
  MiniMax API    — when MINIMAX_API_KEY is set (avatar / t2v script params)
  Template engine — intelligent fallback when no keys are present
"""

from __future__ import annotations

import os
import re
import json
import random
import logging
from typing import Optional
from dataclasses import dataclass, field

from backend.scrape import ScrapedProduct

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PinterestSEOEngine:
    """Pinterest-specific SEO asset package."""
    board_title: str
    pin_title: str
    optimized_description: str
    recommended_keywords: list[str] = field(default_factory=list)
    suggested_board_names: list[str] = field(default_factory=list)
    optimal_pin_dimensions: str = "1000 x 1500 px (2:3 vertical ratio)"
    pin_cta_suggestion: str = "Tap the link to shop now"


@dataclass
class ShortFormVideoBlueprint:
    """TikTok / Instagram Reels script."""
    hook_0_3s: str
    problem_3_10s: str
    solution_10_25s: str
    call_to_action: str
    suggested_hashtags: list[str] = field(default_factory=list)
    estimated_duration: str = "~25 seconds"
    hook_type: str = "Pattern interrupt"


@dataclass
class YouTubeShortsBlueprint:
    """YouTube Shorts video asset package."""
    video_title: str
    hook_segment: str
    body_segment: str
    call_to_action: str
    description_template: str
    title_tags: list[str] = field(default_factory=list)
    suggested_thumbnails: list[str] = field(default_factory=list)
    estimated_duration: str = "~30 seconds"
    shorts_hashtags: list[str] = field(default_factory=list)
    seo_tips: list[str] = field(default_factory=list)


@dataclass
class TwitterViralThread:
    """Twitter/X 5-part promotional thread."""
    thread_theme: str
    tweets: list[str] = field(default_factory=list)
    suggested_hashtags: list[str] = field(default_factory=list)
    cta_final_tweet: str = ""
    optimal_posting_time: str = "Tue-Thu, 8-10am or 5-7pm local time"


@dataclass
class AdCopyVariations:
    """Ad copy for a single paid ad platform."""
    headline_v1: str
    headline_v2: str
    primary_text: str
    description: str
    cta_label: str


@dataclass
class PaidAdPackage:
    """Full paid ad scaling package across platforms."""
    allocated_daily_budget: float
    ad_copy: dict[str, AdCopyVariations] = field(default_factory=dict)
    audience_suggestions: list[str] = field(default_factory=list)
    platform_recommendations: list[str] = field(default_factory=list)
    creative_specs: dict[str, str] = field(default_factory=dict)


@dataclass
class CompiledPackage:
    """Union of all generated assets."""
    pinterest_seo_engine: Optional[PinterestSEOEngine] = None
    short_form_video_blueprint: Optional[ShortFormVideoBlueprint] = None
    youtube_shorts_blueprint: Optional[YouTubeShortsBlueprint] = None
    twitter_viral_thread: Optional[TwitterViralThread] = None
    paid_ad_package: Optional[PaidAdPackage] = None


# ─────────────────────────────────────────────────────────────────────────────
# Keyword Banks (template fallback mode)
# ─────────────────────────────────────────────────────────────────────────────

BENEFIT_BANK: dict[str, list[str]] = {
    "skin":      ["glowing", "radiant", "clear", "flawless", "hydrated", "refreshed"],
    "hair":      ["silky", "shiny", "thick", "volumizing", "nourishing", "smooth"],
    "fitness":   ["toned", "strong", "energized", "lean", "confident", "powerful"],
    "home":      ["cozy", "organized", "stylish", "minimalist", "warm", "inviting"],
    "tech":      ["fast", "seamless", "intuitive", "powerful", "smart", "sleek"],
    "fashion":   ["trendy", "chic", "stunning", "versatile", "elegant", "bold"],
    "beauty":    ["flawless", "radiant", "glowing", "stunning", "natural", "effortless"],
    "kitchen":   ["easy", "delicious", "fresh", "quick", "healthy", "flavorful"],
    "pet":       ["happy", "healthy", "playful", "loved", "active", "pampered"],
    "wellness":  ["calm", "balanced", "mindful", "energized", "rested", "peaceful"],
    "default":   ["amazing", "life-changing", "essential", "premium", "top-rated", "must-have"],
}

HOOK_TEMPLATES = [
    "Wait for this {emoji} #{benefit}",
    "POV: You just discovered {product_name} and your {audience} will never believe it #{benefit}",
    "If I had found {product_name} sooner, I would have saved {time_saved}",
    "Nobody is talking about {product_name}, but they should be. Here is why",
    "Stop scrolling. This {product_name} is about to change your {life_area} #{benefit}",
    "Testing {product_name} for 7 days - honest review (no filter) #{benefit}",
    "Why is {product_name} suddenly everywhere? I had to see for myself",
    "POV: Your {audience} asks where you got it and you say '{product_name}'",
]

CTA_TEMPLATES = [
    "Tap the link in my bio to grab yours!",
    "Shop the link in my bio before it sells out!",
    "Comment 'YES' and I will drop the link!",
    "Save this for later and share with someone who needs this!",
    "Follow for more finds like this - this one goes fast!",
    "Link in bio, go go go!",
    "DM me if you want one - limited stock!",
    "Shop now, thank me later!",
]

TWITTER_THREAD_TWEETS = [
    ("Hook", "A shocking stat or bold claim about {product_name} that stops the scroll."),
    ("Problem", "What most people get wrong about {kw} - and why it costs them {cost}."),
    ("Insight", "The real reason {product_name} works better than what you are using now."),
    ("Proof", "I have been using {product_name} for {duration} and here is what happened:"),
    ("CTA", "Save this thread. Follow for more. Link in bio to grab yours."),
]


# ─────────────────────────────────────────────────────────────────────────────
# MiniMax Client
# ─────────────────────────────────────────────────────────────────────────────

class MiniMaxClient:
    """
    MiniMax API client for video/avatar script parameter generation.

    MiniMax API base: https://api.minimax.chat/v1
    Document: https://www.minimaxi.com/document/Guides

    Endpoints used here:
      POST /text/chatcompletion_pro  — structured text generation
      POST /video_generation         — video generation parameters (seed)

    Requires environment variable:
      MINIMAX_API_KEY  — your MiniMax API key
      MINIMAX_GROUP_ID — your Group ID
    """

    BASE_URL = "https://api.minimax.chat/v1"

    def __init__(self, api_key: str, group_id: str):
        self.api_key = api_key
        self.group_id = group_id

    def chat_completion(self, prompt: str, model: str = "MiniMax-Text-01") -> str:
        """
        Call MiniMax ChatCompletion Pro for structured text generation.
        Returns the raw response text.
        """
        import httpx

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert marketing strategist. "
                        "Return ONLY valid JSON - no markdown fences, no explanations."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.85,
            "max_tokens": 1200,
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self.BASE_URL}/text/chatcompletion_pro",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        # MiniMax returns choices[0].text or choices[0].message.content
        choices = data.get("choices", [{}])
        if choices and isinstance(choices[0], dict):
            return choices[0].get("text") or choices[0].get("message", {}).get("content", "")
        return ""

    def generate_video_params(
        self,
        product: ScrapedProduct,
        platform: str = "tiktok",
    ) -> dict:
        """
        Generate video generation seed parameters via MiniMax.
        Returns a dict with prompt_text, aspect_ratio, duration, and style hints.

        This lets users feed the parameters directly into MiniMax video generation tools
        (e.g. MiniMax Hailuo AI, or any compatible t2v pipeline).
        """
        import httpx

        aspect_ratios = {
            "tiktok":   "9:16",
            "youtube":   "9:16",
            "instagram": "4:5",
            "meta":      "1:1",
        }
        ar = aspect_ratios.get(platform, "9:16")

        # Build a rich text prompt for video generation
        product_desc = product.description or product.title
        keywords = ", ".join(product.raw_keywords[:5])
        prompt_text = (
            f"Product: {product.title}. "
            f"Key benefits: {keywords}. "
            f"Description: {product_desc[:200]}. "
            f"Style: energetic, authentic, modern lifestyle, UGC aesthetic."
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "MiniMax-Video",
            "prompt": prompt_text,
            "duration": 6,
            "aspect_ratio": ar,
            "cfg_strength": 7.5,
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{self.BASE_URL}/video_generation",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                # MiniMax returns { video_id, status, ... }
                return {
                    "video_params": payload,
                    "raw_response": data,
                    "video_id": data.get("video_id", ""),
                    "status": data.get("status", "submitted"),
                }
        except httpx.HTTPStatusError as exc:
            logger.warning("MiniMax video_params API error: %s", exc)
            # Return the parameters even if the API call failed
            # (user can paste them into the MiniMax web UI manually)
            return {
                "video_params": payload,
                "raw_response": None,
                "video_id": None,
                "status": "manual",
                "note": "Paste the video_params.prompt into MiniMax Hailuo AI manually",
            }

    @staticmethod
    def is_configured() -> bool:
        key = os.getenv("MINIMAX_API_KEY", "").strip()
        gid = os.getenv("MINIMAX_GROUP_ID", "").strip()
        return bool(key and key not in ("", "your-minimax-key") and gid)


# ─────────────────────────────────────────────────────────────────────────────
# Generator Engine
# ─────────────────────────────────────────────────────────────────────────────

class TrafficLiftGenerator:

    def __init__(self):
        self._openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        self._model = os.getenv("OPENAI_MODEL", "gpt-4o")

        # MiniMax — init lazily on first use
        self._minimax: Optional[MiniMaxClient] = None
        if MiniMaxClient.is_configured():
            try:
                self._minimax = MiniMaxClient(
                    api_key=os.getenv("MINIMAX_API_KEY", "").strip(),
                    group_id=os.getenv("MINIMAX_GROUP_ID", "").strip(),
                )
                logger.info("MiniMax client configured and ready")
            except Exception as exc:
                logger.warning("MiniMax init failed: %s", exc)

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate(
        self,
        product: ScrapedProduct,
        mode: str,
        target_channels: list[str],
        daily_budget: float = 25.0,
    ) -> CompiledPackage:
        """
        Generate all requested marketing assets for a product.

        Args:
            product:       ScrapedProduct with metadata
            mode:          'organic' or 'paid'
            target_channels: list of channel slugs
            daily_budget:  daily ad spend in USD (paid mode only)
        """
        package = CompiledPackage()

        if mode == "organic":
            if "pinterest" in target_channels:
                package.pinterest_seo_engine = self._generate_pinterest(product)

            if "tiktok_organic" in target_channels:
                package.short_form_video_blueprint = self._generate_tiktok_script(product)

            if "youtube_shorts" in target_channels:
                package.youtube_shorts_blueprint = self._generate_youtube_shorts(product)

            if "twitter_threads" in target_channels:
                package.twitter_viral_thread = self._generate_twitter_thread(product)

        elif mode == "paid":
            channels_for_ads = []
            if "meta_ads" in target_channels:
                channels_for_ads.append("meta")
            if "tiktok_ads" in target_channels:
                channels_for_ads.append("tiktok")

            package.paid_ad_package = self._generate_paid_package(
                product, channels_for_ads, daily_budget
            )

        return package

    def minimax_video_params(
        self,
        product: ScrapedProduct,
        platform: str = "tiktok",
    ) -> dict:
        """Generate MiniMax video parameters if configured, else return a placeholder."""
        if self._minimax:
            return self._minimax.generate_video_params(product, platform)
        return {
            "video_params": None,
            "status": "unavailable",
            "note": "Set MINIMAX_API_KEY and MINIMAX_GROUP_ID in .env to enable",
        }

    @property
    def ai_modes(self) -> dict:
        """Report which AI backends are active."""
        return {
            "openai": self._has_openai,
            "minimax": self._minimax is not None,
        }

    # ── Pinterest ──────────────────────────────────────────────────────────────

    def _generate_pinterest(self, product: ScrapedProduct) -> PinterestSEOEngine:
        if self._has_openai:
            return self._ai_generate_pinterest(product)

        title = product.title
        keywords = product.raw_keywords
        benefit_kw = self._detect_benefit_category(keywords)
        benefits = BENEFIT_BANK.get(benefit_kw, BENEFIT_BANK["default"])

        board_adj = random.choice(["Best", "Top", "Ultimate", "Curated", "Must-Have"])
        board_noun = random.choice(["Finds", "Picks", "Guide", "Collection", "Inspo"])
        category_kw = keywords[0].title() if keywords else "Products"
        board_title = f"{board_adj} {category_kw} {board_noun}"

        pin_adj = random.choice(benefits).title()
        pin_title = f"{pin_adj} {title}".strip()
        if len(pin_title) > 100:
            pin_title = f"{random.choice(benefits).title()} {title[:60]}"

        kw_str = " | ".join(keywords[:6])
        desc = (
            f"{product.description[:180]}. "
            f"Keywords: {kw_str}. "
            f"Perfect for anyone looking for {keywords[0] if keywords else 'something special'}. "
            f"Save this pin and shop the link for exclusive access!"
        )

        suggested = list(dict.fromkeys([board_title] + [
            f"{board_adj} {kw.title()} Products"
            for kw in random.sample(keywords[:4] or ["product"], min(4, len(keywords) or 1))
        ]))[:3]

        return PinterestSEOEngine(
            board_title=board_title,
            pin_title=pin_title,
            optimized_description=desc[:500],
            recommended_keywords=keywords[:12],
            suggested_board_names=suggested,
            optimal_pin_dimensions="1000 x 1500 px (2:3 vertical ratio)",
            pin_cta_suggestion="Shop now | Limited stock available",
        )

    def _ai_generate_pinterest(self, product: ScrapedProduct) -> PinterestSEOEngine:
        prompt = (
            "You are an expert Pinterest SEO strategist. Generate a complete Pinterest "
            "marketing package for the following product. Return ONLY valid JSON (no markdown) "
            "with these exact fields:\n"
            '{"board_title","pin_title","optimized_description","recommended_keywords",'
            '"suggested_board_names","optimal_pin_dimensions","pin_cta_suggestion"}\n\n'
            f"Product title: {product.title}\n"
            f"Description: {product.description}\n"
            f"Price: {product.price or 'N/A'}\n"
            f"Site: {product.site_name}\n"
            f"Keywords: {', '.join(product.raw_keywords[:15])}\n\n"
            "- board_title: catchy, keyword-rich board name (max 50 chars)\n"
            "- pin_title: scroll-stopping, benefit-driven headline (max 100 chars)\n"
            "- optimized_description: 200-300 chars, naturally include keywords\n"
            "- recommended_keywords: 10-12 comma-separated keywords\n"
            "- suggested_board_names: 3 alternative board names\n"
            "- optimal_pin_dimensions: '1000 x 1500 px (2:3 vertical ratio)'\n"
            "- pin_cta_suggestion: short action-oriented CTA (max 50 chars)"
        )
        raw = self._call_openai(prompt)
        data = self._parse_json(raw)
        return PinterestSEOEngine(
            board_title=data.get("board_title", product.title),
            pin_title=data.get("pin_title", product.title),
            optimized_description=data.get("optimized_description", product.description or ""),
            recommended_keywords=data.get("recommended_keywords", product.raw_keywords[:12]),
            suggested_board_names=data.get("suggested_board_names", [])[:3],
            optimal_pin_dimensions=data.get("optimal_pin_dimensions", "1000 x 1500 px"),
            pin_cta_suggestion=data.get("pin_cta_suggestion", "Shop now"),
        )

    # ── TikTok / Reels ────────────────────────────────────────────────────────

    def _generate_tiktok_script(self, product: ScrapedProduct) -> ShortFormVideoBlueprint:
        if self._has_openai:
            return self._ai_generate_tiktok(product)

        title = product.title
        keywords = product.raw_keywords
        benefit_kw = self._detect_benefit_category(keywords)
        benefits = BENEFIT_BANK.get(benefit_kw, BENEFIT_BANK["default"])
        benefit = random.choice(benefits)
        audience = self._audience_for_category(benefit_kw)
        life_area = self._life_area_for_category(benefit_kw)

        emoji_map = {"default": "wait", "beauty": "glow", "tech": "wow", "fitness": "fire"}
        emoji = emoji_map.get(benefit_kw, "wait")

        hook_tpl = random.choice(HOOK_TEMPLATES)
        hook = hook_tpl.format(
            product_name=title[:40],
            benefit=benefit,
            audience=audience,
            time_saved=random.choice(["hours", "weeks", "$500", "a ton of stress"]),
            emoji=emoji,
        )

        problem = (
            f"You know that feeling when you search everywhere for {keywords[0] if keywords else 'something great'} "
            f"and nothing really delivers? You are not alone. {random.choice(benefits).title()} options "
            f"are hard to find, and most stuff online is just hype - not actual results."
        )

        solution = (
            f"Introducing {title[:50]}. This is the real deal - "
            f"designed to give you {benefit} results without the guesswork. "
            f"{random.choice(['Thousands are already seeing the difference.', 'Honest review below.'])} "
            f"Link in bio to get yours before it is gone."
        )

        cta = random.choice(CTA_TEMPLATES)

        hashtags = [
            f"#{keywords[0].replace(' ', '')}" if keywords else "#finds",
            f"#{benefit.replace(' ', '')}",
            f"#{benefit_kw}" if benefit_kw != "default" else "#musttry",
            "#productreview", "#fyp", "#viral", "#shopsmall",
            f"#{product.site_name.replace(' ', '') if product.site_name else 'onlineshopping'}",
        ]
        hashtags = [re.sub(r"[^#\w]", "", h) for h in hashtags if h]
        hashtags = list(dict.fromkeys(hashtags))[:10]

        return ShortFormVideoBlueprint(
            hook_0_3s=hook,
            problem_3_10s=problem,
            solution_10_25s=solution,
            call_to_action=cta,
            suggested_hashtags=hashtags,
            estimated_duration="~25 seconds",
            hook_type="Pattern interrupt / Relatable pain point",
        )

    def _ai_generate_tiktok(self, product: ScrapedProduct) -> ShortFormVideoBlueprint:
        prompt = (
            "You are an expert short-form video content strategist for TikTok, Instagram Reels, "
            "and YouTube Shorts. Generate a complete video script for the product below. "
            "Return ONLY valid JSON (no markdown) with these exact fields:\n"
            '{"hook_0_3s","problem_3_10s","solution_10_25s","call_to_action",'
            '"suggested_hashtags","estimated_duration","hook_type"}\n\n'
            f"Product: {product.title}\n"
            f"Description: {product.description}\n"
            f"Price: {product.price or 'N/A'}\n"
            f"Keywords: {', '.join(product.raw_keywords[:10])}\n\n"
            "- hook_0_3s: ultra-engaging first 3 seconds\n"
            "- problem_3_10s: 5-7 seconds, relatable pain / desire gap\n"
            "- solution_10_25s: 15 seconds, product as the clear solution\n"
            "- call_to_action: 2-3 seconds, strong direct CTA\n"
            "- suggested_hashtags: 8-10 relevant hashtags\n"
            "- estimated_duration: '~25 seconds'\n"
            "- hook_type: one of 'Pattern interrupt', 'Relatable pain point', 'Curiosity gap', 'Before/After'"
        )
        raw = self._call_openai(prompt)
        data = self._parse_json(raw)
        return ShortFormVideoBlueprint(
            hook_0_3s=data.get("hook_0_3s", "Wait for this..."),
            problem_3_10s=data.get("problem_3_10s", "..."),
            solution_10_25s=data.get("solution_10_25s", "..."),
            call_to_action=data.get("call_to_action", "Shop the link!"),
            suggested_hashtags=data.get("suggested_hashtags", [])[:10],
            estimated_duration=data.get("estimated_duration", "~25 seconds"),
            hook_type=data.get("hook_type", "Pattern interrupt"),
        )

    # ── YouTube Shorts ────────────────────────────────────────────────────────

    def _generate_youtube_shorts(self, product: ScrapedProduct) -> YouTubeShortsBlueprint:
        if self._has_openai:
            return self._ai_generate_youtube_shorts(product)

        title = product.title
        keywords = product.raw_keywords
        benefit_kw = self._detect_benefit_category(keywords)
        benefits = BENEFIT_BANK.get(benefit_kw, BENEFIT_BANK["default"])
        benefit = random.choice(benefits)

        # Title — SEO-optimized with keyword at front
        primary_kw = keywords[0] if keywords else "review"
        video_title = (
            f"{benefit.title()} {primary_kw.title()}: "
            f"{title[:45]} {random.choice(['Test', 'Review', 'Unboxing', 'Honest Review'])}"
        )

        # Title tags — 15 tags for YouTube SEO
        title_tags = [primary_kw]
        title_tags.extend([kw for kw in keywords[:7] if kw != primary_kw])
        title_tags.extend([
            f"{benefit} results", "shorts",
            f"{benefit_kw} tips" if benefit_kw != "default" else "product review",
            "viral", "must have", "honest review",
            f"{product.site_name or 'shop'} haul",
        ])
        title_tags = list(dict.fromkeys(title_tags))[:15]

        # Hook — YouTube Shorts needs a punchy opener within 1-2s
        hook_segment = (
            f"If you are still using the OLD way to get {benefit} results, stop what you are doing. "
            f"This is about to change everything."
        )

        # Body — quick benefit showcase
        body_segment = (
            f"{title} delivers {benefit} results that actually stick. "
            f"Unlike typical products that overpromise and underdeliver, "
            f"this one has the formula right. "
            f"Link in description to grab yours."
        )

        # CTA
        call_to_action = (
            "Like if this helped! Subscribe for more honest reviews and product deep-dives."
        )

        # Description template
        description_template = (
            f"{product.description[:150]}\n\n"
            f"Products mentioned: {title}\n"
            f"{'Price: ' + product.price if product.price else ''}\n\n"
            f"{' | '.join(['#' + t for t in title_tags[:5]])}\n"
            f"Title Tags: {', '.join(title_tags[:8])}"
        )

        # Suggested thumbnails (text overlay recommendations)
        suggested_thumbnails = [
            f"BOLD TEXT: '{benefit.upper()}' + product photo + surprised face emoji",
            f"HOOK TEXT: 'Is this the BEST {primary_kw}?' + product close-up",
            f"BEFORE/AFTER: split screen with bold '{benefit.upper()}' overlay",
        ]

        shorts_hashtags = [
            f"#{primary_kw.replace(' ', '')}" if primary_kw else "#review",
            f"#{benefit.replace(' ', '')}",
            f"#{benefit_kw}" if benefit_kw != "default" else "#shorts",
            "#shorts", "#youtubeshorts", "#viral", "#fyp", "#mustwatch",
        ]
        shorts_hashtags = [re.sub(r"[^#\w]", "", h) for h in shorts_hashtags if h]
        shorts_hashtags = list(dict.fromkeys(shorts_hashtags))[:8]

        # SEO tips
        seo_tips = [
            "Upload at 9:16 (1080x1920) — YouTube Shorts requirement",
            "First 3 frames are critical — use bold text overlay on face/object",
            "Keep video 15-60 seconds for maximum Shorts distribution",
            "End screen: hold on product + CTA for last 3 seconds",
            "Post 3-5 Shorts per week during the algorithm learning phase",
            "Pin a comment with product link — drives consistent click-through",
        ]

        return YouTubeShortsBlueprint(
            video_title=video_title,
            title_tags=title_tags,
            hook_segment=hook_segment,
            body_segment=body_segment,
            call_to_action=call_to_action,
            description_template=description_template,
            suggested_thumbnails=suggested_thumbnails,
            estimated_duration="~30 seconds",
            shorts_hashtags=shorts_hashtags,
            seo_tips=seo_tips,
        )

    def _ai_generate_youtube_shorts(self, product: ScrapedProduct) -> YouTubeShortsBlueprint:
        prompt = (
            "You are an expert YouTube Shorts strategist. Generate a complete YouTube Shorts "
            "asset package for the product below. Return ONLY valid JSON (no markdown) with:\n"
            '{"video_title","title_tags","hook_segment","body_segment","call_to_action",'
            '"description_template","suggested_thumbnails","estimated_duration",'
            '"shorts_hashtags","seo_tips"}\n\n'
            f"Product: {product.title}\n"
            f"Description: {product.description}\n"
            f"Price: {product.price or 'N/A'}\n"
            f"Keywords: {', '.join(product.raw_keywords[:10])}\n\n"
            "- video_title: SEO-optimized title with primary keyword first (max 70 chars)\n"
            "- title_tags: 12-15 comma-separated YouTube SEO tags\n"
            "- hook_segment: 1-2 second punchy opener text\n"
            "- body_segment: 15-20 second narration script\n"
            "- call_to_action: 2-3 second end CTA\n"
            "- description_template: full YouTube description with product info + tags\n"
            "- suggested_thumbnails: 3 thumbnail text overlay ideas\n"
            "- estimated_duration: '~30 seconds'\n"
            "- shorts_hashtags: 7-8 YouTube Shorts hashtags\n"
            "- seo_tips: 5-6 upload and optimization tips"
        )
        raw = self._call_openai(prompt)
        data = self._parse_json(raw)
        return YouTubeShortsBlueprint(
            video_title=data.get("video_title", product.title),
            title_tags=data.get("title_tags", [])[:15],
            hook_segment=data.get("hook_segment", "..."),
            body_segment=data.get("body_segment", "..."),
            call_to_action=data.get("call_to_action", "Like and subscribe!"),
            description_template=data.get("description_template", ""),
            suggested_thumbnails=data.get("suggested_thumbnails", [])[:3],
            estimated_duration=data.get("estimated_duration", "~30 seconds"),
            shorts_hashtags=data.get("shorts_hashtags", [])[:8],
            seo_tips=data.get("seo_tips", [])[:6],
        )

    # ── Twitter/X Viral Threads ───────────────────────────────────────────────

    def _generate_twitter_thread(self, product: ScrapedProduct) -> TwitterViralThread:
        if self._has_openai:
            return self._ai_generate_twitter_thread(product)

        title = product.title
        keywords = product.raw_keywords
        benefit_kw = self._detect_benefit_category(keywords)
        benefits = BENEFIT_BANK.get(benefit_kw, BENEFIT_BANK["default"])
        benefit = random.choice(benefits)
        primary_kw = keywords[0] if keywords else "product"

        thread_theme = f"How {title[:50]} changed my approach to {primary_kw} (thread)"

        # Build 5 tweets
        tweets = []
        tweets.append(
            f"I spent {random.choice(['3 weeks', '2 months', '30 days'])} trying to "
            f"find the best {primary_kw}. Then I found {title[:40]}. "
            f"A {benefit} thread:"
        )
        tweets.append(
            f"Most people get {primary_kw} completely wrong.\n\n"
            f"They focus on {random.choice(['price', 'quantity', 'brand names', 'trends'])} "
            f"instead of what actually works: {random.choice(benefits)} results.\n\n"
            f"Here is the difference:"
        )
        tweets.append(
            f"{title} is built differently.\n\n"
            f"- Delivers {benefit} outcomes\n"
            f"- {random.choice(['Premium quality materials', 'Backed by real science', 'Thousands of happy users'])}\n"
            f"- Worth every penny\n\n"
            f"Most alternatives? Overpriced and underperforming."
        )
        tweets.append(
            f"I have been using {title[:40]} for {random.choice(['3 weeks', 'a month', '2 months'])}.\n\n"
            f"Results: {random.choice(['Noticeable change within days', 'Consistent improvement week over week', 'Better than anything I tried before'])}.\n\n"
            f"Not a sponsored post. Just real results."
        )
        tweets.append(
            f"If you want to try {title[:40]}:\n\n"
            f"Link in bio.\n\n"
            f"Saves this thread and follow for more unfiltered reviews."
        )

        suggested_hashtags = [
            f"#{primary_kw.replace(' ', '')}" if primary_kw else "#product",
            f"#{benefit.replace(' ', '')}",
            "#review", "#fyp", "#thread", "#recommendation",
            f"#{product.site_name.replace(' ', '') if product.site_name else 'onlineshopping'}",
            "#honestreview",
        ]
        suggested_hashtags = [re.sub(r"[^#\w]", "", h) for h in suggested_hashtags if h]
        suggested_hashtags = list(dict.fromkeys(suggested_hashtags))[:8]

        return TwitterViralThread(
            thread_theme=thread_theme,
            tweets=tweets,
            suggested_hashtags=suggested_hashtags,
            cta_final_tweet=tweets[-1],
            optimal_posting_time="Tue-Thu, 8-10am or 5-7pm local time",
        )

    def _ai_generate_twitter_thread(self, product: ScrapedProduct) -> TwitterViralThread:
        prompt = (
            "You are an expert Twitter/X growth strategist. Generate a 5-tweet viral promotional "
            "thread for the product below. Return ONLY valid JSON (no markdown) with:\n"
            '{"thread_theme","tweets","suggested_hashtags","cta_final_tweet",'
            '"optimal_posting_time"}\n\n'
            f"Product: {product.title}\n"
            f"Description: {product.description}\n"
            f"Price: {product.price or 'N/A'}\n"
            f"Keywords: {', '.join(product.raw_keywords[:8])}\n\n"
            "- thread_theme: one-line thread premise (max 80 chars)\n"
            "- tweets: array of exactly 5 tweets in order, each under 260 chars\n"
            "  Tweet 1: scroll-stopping hook - shocking claim or stat\n"
            "  Tweet 2: the problem / what most people do wrong\n"
            "  Tweet 3: the solution / what makes this product different\n"
            "  Tweet 4: social proof / your personal experience\n"
            "  Tweet 5: CTA with link-in-bio call\n"
            "- suggested_hashtags: 6-8 relevant hashtags (no duplicates)\n"
            "- cta_final_tweet: repeat of tweet 5 (for easy copy)\n"
            "- optimal_posting_time: best posting window recommendation"
        )
        raw = self._call_openai(prompt)
        data = self._parse_json(raw)
        tweets = data.get("tweets", [])
        return TwitterViralThread(
            thread_theme=data.get("thread_theme", f"About {product.title}"),
            tweets=tweets[:5],
            suggested_hashtags=data.get("suggested_hashtags", [])[:8],
            cta_final_tweet=data.get("cta_final_tweet", tweets[-1] if tweets else ""),
            optimal_posting_time=data.get(
                "optimal_posting_time", "Tue-Thu, 8-10am or 5-7pm local time"
            ),
        )

    # ── Paid Ads ──────────────────────────────────────────────────────────────

    def _generate_paid_package(
        self,
        product: ScrapedProduct,
        platforms: list[str],
        daily_budget: float,
    ) -> PaidAdPackage:
        if self._has_openai:
            return self._ai_generate_paid_package(product, platforms, daily_budget)

        title = product.title
        keywords = product.raw_keywords
        benefit_kw = self._detect_benefit_category(keywords)
        benefits = BENEFIT_BANK.get(benefit_kw, BENEFIT_BANK["default"])
        benefit = random.choice(benefits)
        audience = self._audience_for_category(benefit_kw)

        ad_copy: dict[str, AdCopyVariations] = {}

        if "meta" in platforms:
            ad_copy["meta_ads"] = AdCopyVariations(
                headline_v1=f"{title[:40]} - {benefit.title()} Results",
                headline_v2=f"Discover {title[:35]} | Shop Now",
                primary_text=(
                    f"Ready for {benefit}? {title[:45]} is designed for {audience} "
                    f"who want real results. "
                    f"{random.choice(['Limited stock available.', 'Free shipping today.', 'Rated 5 stars by thousands.'])} "
                    f"Shop the link before they are gone!"
                ),
                description=product.description[:200] if product.description else f"Premium quality {title}.",
                cta_label=random.choice(["Shop Now", "Learn More", "Get Yours"]),
            )

        if "tiktok" in platforms:
            ad_copy["tiktok_ads"] = AdCopyVariations(
                headline_v1=f"This {title[:30]} actually works",
                headline_v2=f"If you love {keywords[0] if keywords else 'great products'}, you need this",
                primary_text=(
                    f"Not a drill - {title[:40]} is trending for a reason. "
                    f"{benefit.title()} results, {random.choice(['no fluff', 'real ingredients', 'premium build'])}. "
                    f"Comment 'INFO' or tap to shop!"
                ),
                description=f"Shop {title[:60]} - Fast shipping - Click to explore",
                cta_label=random.choice(["Shop Now", "Tap to Buy", "Get Offer"]),
            )

        audiences = [
            f"People interested in {keywords[i] if i < len(keywords) else 'productivity'}"
            for i in range(min(3, len(keywords) or 1))
        ]
        audiences.extend([
            f"{audience} aged 25-44",
            "Engaged shoppers (past 30-day buyers)",
            "Lookalike audiences of recent converters",
        ])

        plat_recs = []
        if "meta" in platforms:
            plat_recs.extend([
                "Meta Advantage+ Shopping Campaign",
                "Instagram Reels Ads",
                "Facebook Carousel - 3 product angles",
            ])
        if "tiktok" in platforms:
            plat_recs.extend([
                "TikTok Branded Mission",
                "TikTok Spark Ads (creator UGC)",
                "TopView + In-Feed combo",
            ])
        if daily_budget >= 50:
            plat_recs.append("Retargeting layer - website visitors (7-day window)")
        if daily_budget >= 100:
            plat_recs.append("Cross-platform creative testing - 20% to Google Performance Max")

        specs = {
            "Meta Feed": "1:1 square (1080x1080) or 4:5 (1080x1350) - MP4 or Carousel",
            "Meta Stories": "9:16 vertical (1080x1920) - 15s max - Muted autoplay",
            "TikTok In-Feed": "9:16 (1080x1920) - 15-60s - No hard cuts first 3s",
            "TikTok TopView": "9:16 (1080x1920) - 5s in-view before skip - Full sound",
        }

        return PaidAdPackage(
            allocated_daily_budget=daily_budget,
            ad_copy=ad_copy,
            audience_suggestions=audiences[:6],
            platform_recommendations=plat_recs,
            creative_specs=specs,
        )

    def _ai_generate_paid_package(
        self,
        product: ScrapedProduct,
        platforms: list[str],
        daily_budget: float,
    ) -> PaidAdPackage:
        plat_str = " & ".join(platforms).upper()
        prompt = (
            f"You are a performance marketing strategist specializing in {plat_str} paid ads. "
            "Generate a complete paid ad campaign package. Return ONLY valid JSON (no markdown):\n"
            '{"allocated_daily_budget","ad_copy":{"meta_ads":{"headline_v1","headline_v2","primary_text","description","cta_label"},'
            '"tiktok_ads":{"headline_v1","headline_v2","primary_text","description","cta_label"}},'
            '"audience_suggestions","platform_recommendations","creative_specs"}\n\n'
            f"Product: {product.title}\n"
            f"Description: {product.description}\n"
            f"Price: {product.price or 'N/A'}\n"
            f"Keywords: {', '.join(product.raw_keywords[:10])}\n"
            f"Daily budget: ${daily_budget}\n\n"
            "- allocated_daily_budget: echo the passed budget\n"
            "- ad_copy: generate for each platform (meta, tiktok)\n"
            "  headline_v1: punchy under 40 chars\n"
            "  headline_v2: different angle under 40 chars\n"
            "  primary_text: 90-125 chars\n"
            "  description: under 30 chars\n"
            "  cta_label: Shop Now / Learn More / Get Yours / Tap to Buy\n"
            "- audience_suggestions: 5-6 descriptions\n"
            "- platform_recommendations: 4-6 campaign types\n"
            "- creative_specs: recommended dimensions per placement"
        )
        raw = self._call_openai(prompt)
        data = self._parse_json(raw)

        ad_copy: dict[str, AdCopyVariations] = {}
        raw_copy = data.get("ad_copy", {})
        for plat in platforms:
            rc = raw_copy.get(f"{plat}_ads", {})
            ad_copy[f"{plat}_ads"] = AdCopyVariations(
                headline_v1=rc.get("headline_v1", ""),
                headline_v2=rc.get("headline_v2", ""),
                primary_text=rc.get("primary_text", ""),
                description=rc.get("description", ""),
                cta_label=rc.get("cta_label", "Shop Now"),
            )

        return PaidAdPackage(
            allocated_daily_budget=data.get("allocated_daily_budget", daily_budget),
            ad_copy=ad_copy,
            audience_suggestions=data.get("audience_suggestions", [])[:6],
            platform_recommendations=data.get("platform_recommendations", [])[:6],
            creative_specs=data.get("creative_specs", {}),
        )

    # ── AI Backends ───────────────────────────────────────────────────────────

    @property
    def _has_openai(self) -> bool:
        return bool(self._openai_key and self._openai_key != "sk-REPLACE-ME")

    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI and return the raw text response."""
        from openai import OpenAI
        client = OpenAI(api_key=self._openai_key)
        resp = client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a world-class marketing strategist. "
                        "Return ONLY valid JSON - no markdown code fences, no explanations."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.85,
            max_tokens=1400,
        )
        return resp.choices[0].message.content or "{}"

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Robust JSON extraction — handles markdown fences and partial strings."""
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning("JSON parse failed. Raw: %s", raw[:200])
            return {}

    # ── Keyword Intelligence ───────────────────────────────────────────────────

    @staticmethod
    def _detect_benefit_category(keywords: list[str]) -> str:
        kw_str = " ".join(keywords).lower()
        category_signals = {
            "beauty":  ["makeup", "skincare", "cosmetic", "glow", "serum", "moisturizer", "lip", "eye", "foundation"],
            "hair":    ["shampoo", "conditioner", "hair", "styling", "curl", "straighten", "treatment"],
            "fitness": ["workout", "gym", "fitness", "exercise", "training", "muscle", "cardio", "yoga"],
            "tech":    ["smart", "wireless", "bluetooth", "usb", "app", "digital", "display", "screen"],
            "home":    ["home", "decor", "furniture", "kitchen", "bedroom", "living room", "organizer"],
            "fashion": ["dress", "shoe", "bag", "wear", "outfit", "style", "jeans", "jacket"],
            "skin":    ["skin", "face", "cream", "sunscreen", "spf", "anti-aging", "wrinkle"],
            "kitchen": ["cook", "bake", "recipe", "food", "blender", "pan", "knife", "utensil"],
            "pet":     ["pet", "dog", "cat", "animal", "puppy", "kitten", "treat", "collar"],
            "wellness":["yoga", "meditation", "supplement", "vitamin", "cbd", "relax", "sleep"],
        }
        for cat, signals in category_signals.items():
            if any(sig in kw_str for sig in signals):
                return cat
        return "default"

    @staticmethod
    def _audience_for_category(category: str) -> str:
        return {
            "beauty":  "beauty lovers", "hair":    "hair enthusiasts",
            "fitness": "fitness-focused people", "tech":  "tech-savvy consumers",
            "home":    "home decor lovers", "fashion": "fashion-forward shoppers",
            "skin":    "skincare-conscious individuals", "kitchen": "home cooks",
            "pet":     "pet parents", "wellness": "wellness-minded people",
            "default": "online shoppers",
        }.get(category, "online shoppers")

    @staticmethod
    def _life_area_for_category(category: str) -> str:
        return {
            "beauty":  "morning routine", "hair":    "hair goals",
            "fitness": "fitness journey", "tech":   "workflow",
            "home":    "living space", "fashion":  "wardrobe",
            "skin":    "self-care routine", "kitchen": "kitchen setup",
            "pet":     "pet care routine", "wellness": "daily wellness",
            "default": "daily life",
        }.get(category, "daily life")
