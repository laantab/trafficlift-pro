"""
trafficlift_pro/backend/scrape.py
URL Scraping & Metadata Extraction Module

Handles fetching product/landing page content and extracting
structured metadata (title, description, images, price, keywords).
"""

from __future__ import annotations

import re
import logging
from typing import Optional
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScrapedProduct:
    """Structured product/landing page metadata."""
    url: str
    title: str
    description: str
    primary_image: Optional[str] = None
    additional_images: list[str] = field(default_factory=list)
    price: Optional[str] = None
    site_name: Optional[str] = None
    raw_keywords: list[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "primary_image": self.primary_image,
            "additional_images": self.additional_images,
            "price": self.price,
            "site_name": self.site_name,
            "raw_keywords": self.raw_keywords,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Scraper
# ─────────────────────────────────────────────────────────────────────────────

class ProductScraper:
    """
    Fetches and extracts structured metadata from any product URL.
    Supports Open Graph, Twitter Cards, Schema.org JSON-LD, and HTML meta tags.
    """

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    TIMEOUT_SECONDS = 15

    def scrape(self, url: str) -> ScrapedProduct:
        """
        Primary entry point. Fetch + extract in one call.
        Returns a ScrapedProduct with whatever fields were found.
        """
        html = self._fetch_html(url)
        soup = BeautifulSoup(html, "lxml")

        # Strip noise elements that add no value to text analysis
        self._remove_noise(soup)

        # Extract all metadata
        og = self._extract_opengraph(soup)
        twitter = self._extract_twitter_cards(soup)
        ld_json = self._extract_json_ld(soup)
        meta = self._extract_meta_tags(soup)
        raw_text = self._extract_text_content(soup)
        keywords = self._extract_keywords(soup, raw_text)

        # Merge sources — OG > Twitter > LD-JSON > Meta
        title = (
            og.get("og:title")
            or twitter.get("twitter:title")
            or ld_json.get("name")
            or meta.get("title")
            or meta.get("description", "Untitled Product")
        )
        description = (
            og.get("og:description")
            or twitter.get("twitter:description")
            or ld_json.get("description")
            or meta.get("description")
            or ""
        )
        image = (
            og.get("og:image")
            or twitter.get("twitter:image")
            or ld_json.get("image")
            or None
        )
        site_name = og.get("og:site_name") or meta.get("application-name") or self._extract_domain(url)
        price = ld_json.get("offers", {}).get("price") or meta.get("product:price:amount")
        additional_images = self._extract_gallery_images(soup, og.get("og:image"))

        return ScrapedProduct(
            url=url,
            title=self._clean_text(title),
            description=self._clean_text(description),
            primary_image=self._absolutize_url(image, url) if image else None,
            additional_images=[
                self._absolutize_url(img, url) for img in additional_images[:8]
            ],
            price=self._format_price(price),
            site_name=site_name,
            raw_keywords=keywords,
            raw_text=raw_text[:3000],  # cap for token safety
        )

    # ─── Private Helpers ──────────────────────────────────────────────────────

    def _fetch_html(self, url: str) -> str:
        """Download the target URL and return raw HTML."""
        try:
            resp = requests.get(
                url,
                headers=self.DEFAULT_HEADERS,
                timeout=self.TIMEOUT_SECONDS,
                allow_redirects=True,
            )
            resp.raise_for_status()
            # Preserve encoding from server
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.RequestException as exc:
            logger.warning("Fetch failed for %s: %s", url, exc)
            # Return minimal HTML so extraction still runs with fallback logic
            return "<html><head><title>Product</title></head><body></body></html>"

    @staticmethod
    def _remove_noise(soup: BeautifulSoup) -> None:
        """Drop nav, script, style, noscript, and ad containers."""
        for tag in soup.find_all(
            ["nav", "header", "footer", "script", "style", "noscript",
             "svg", "iframe", "form", "button"]
        ):
            tag.decompose()
        # Remove common ad/cookie banners
        for cls in ["ad", "ads", "cookie", "popup", "modal", "nav"]:
            for tag in soup.find_all(class_=re.compile(rf"\b{cls}\b", re.I)):
                tag.decompose()

    @staticmethod
    def _extract_opengraph(soup: BeautifulSoup) -> dict[str, str]:
        out = {}
        for tag in soup.find_all("meta", property=re.compile(r"^og:")):
            prop = tag.get("property", "")[3:]  # strip "og:"
            out[prop] = tag.get("content", "")
        return out

    @staticmethod
    def _extract_twitter_cards(soup: BeautifulSoup) -> dict[str, str]:
        out = {}
        for tag in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
            name = tag.get("name", "")[8:]  # strip "twitter:"
            out[name] = tag.get("content", "")
        for tag in soup.find_all("meta", property=re.compile(r"^twitter:")):
            prop = tag.get("property", "")[8:]
            out[prop] = tag.get("content", "")
        return out

    @staticmethod
    def _extract_json_ld(soup: BeautifulSoup) -> dict:
        """Parse the first JSON-LD <script type=application/ld+json> block."""
        import json
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
                # Handle @graph arrays (common in Shopify/Google)
                if isinstance(data, dict):
                    if data.get("@graph"):
                        data = data["@graph"][0]
                    return data
                elif isinstance(data, list) and data:
                    return data[0]
            except (json.JSONDecodeError, TypeError):
                continue
        return {}

    @staticmethod
    def _extract_meta_tags(soup: BeautifulSoup) -> dict[str, str]:
        out = {}
        for tag in soup.find_all("meta"):
            name = tag.get("name") or tag.get("property", "")
            content = tag.get("content", "")
            if name and content:
                out[name.lower()] = content
        return out

    @staticmethod
    def _extract_text_content(soup: BeautifulSoup) -> str:
        """Pull visible body text for keyword extraction."""
        # Prefer article/main body; fall back to full body
        body = (
            soup.find("article")
            or soup.find("main")
            or soup.find("div", class_=re.compile(r"product|content|main", re.I))
            or soup.body
            or soup
        )
        return " ".join(body.get_text(separator=" ").split())

    @staticmethod
    def _extract_keywords(soup: BeautifulSoup, raw_text: str) -> list[str]:
        """Infer keywords from the page: meta keywords + title + high-frequency terms."""
        keywords: list[str] = []

        # 1. Meta keywords tag
        meta_kw = soup.find("meta", attrs={"name": re.compile(r"keywords", re.I)})
        if meta_kw:
            keywords.extend([
                k.strip().lower()
                for k in meta_kw.get("content", "").split(",")
                if k.strip()
            ])

        # 2. Title words (filter stopwords, keep 3+-char words)
        title_tag = soup.find("title")
        if title_tag:
            title_words = [
                w.strip("-|–").lower()
                for w in title_tag.get_text().split()
                if len(w) >= 3
            ]
            stopwords = {
                "the", "and", "for", "with", "from", "your", "you", "are",
                "this", "that", "have", "been", "will", "their", "what",
                "about", "which", "when", "make", "like", "just", "more",
                "official", "online", "shop", "store", "com", "www",
            }
            keywords.extend([w for w in title_words if w not in stopwords])

        # 3. Dedup and return top 15
        seen = set()
        unique = []
        for kw in keywords:
            kw_clean = re.sub(r"[^\w\s-]", "", kw).strip()
            if kw_clean and kw_clean not in seen and len(kw_clean) >= 2:
                seen.add(kw_clean)
                unique.append(kw_clean)
        return list(dict.fromkeys(unique))[:15]

    @staticmethod
    def _extract_gallery_images(soup: BeautifulSoup, primary: Optional[str]) -> list[str]:
        """Find additional product images beyond the primary OG image."""
        images = []
        seen = {primary} if primary else set()

        # Look for common gallery/container patterns
        for tag in soup.find_all("img"):
            src = (
                tag.get("src")
                or tag.get("data-src")
                or tag.get("data-lazy-src")
                or tag.get("data-original")
            )
            if not src or "data:image" in src or "placeholder" in src.lower():
                continue
            src = re.sub(r"\?.*$", "", src)  # strip query params
            w = tag.get("width", "0")
            h = tag.get("height", "0")
            try:
                if int(w) < 100 or int(h) < 100:
                    continue
            except (ValueError, TypeError):
                pass
            if src not in seen:
                seen.add(src)
                images.append(src)
        return images

    @staticmethod
    def _clean_text(text: str) -> str:
        """Trim whitespace and normalize line breaks."""
        return " ".join(text.split()).strip()

    @staticmethod
    def _extract_domain(url: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.removeprefix("www.")
        return domain.split(":")[0]

    @staticmethod
    def _absolutize_url(path: str, base_url: str) -> str:
        """Convert relative or protocol-relative URLs to absolute."""
        from urllib.parse import urljoin, urlparse
        if path.startswith(("http://", "https://")):
            return path
        if path.startswith("//"):
            return "https:" + path
        return urljoin(base_url, path)

    @staticmethod
    def _format_price(price: Optional[str]) -> Optional[str]:
        if not price:
            return None
        try:
            val = float(price)
            return f"${val:,.2f}"
        except (ValueError, TypeError):
            return price.strip()
