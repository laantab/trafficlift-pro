"""URL → Product resolver.

TrafficLift Pro must accept user-pasted product URLs (Amazon share /
short links, redirect links, plain Amazon product URLs) and resolve
them into a usable product record before they enter the keyword /
candidate pipeline. Real Amazon product pages routinely have no
product name in the slug (share / redirect links), so the previous
"keyword" path that scored the raw URL was guaranteed to fail.

This module:
    1. detects whether an input is a URL
    2. follows redirects safely (http/https only, timeout-bounded,
       capped redirect count, no private-network targets)
    3. extracts the ASIN from any of the common Amazon URL shapes
       (dp/<asin>, gp/product/<asin>, link.amazon/<asin>,
       shortened share links)
    4. pulls the product title from page metadata when the page is
       reachable
    5. falls back to Tavily research using the ASIN when the page is
       blocked (Amazon bot detection returns 404 to most non-browser
       clients) so we still get the real title
    6. returns a structured record that the rest of the pipeline
       treats identically to a keyword-derived product

All functions are pure-Python and side-effect-free except for the
network calls inside ``resolve_url_to_product``, which are bounded by
both ``timeout`` and a hard wall-clock budget.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

# Amazon ASIN = 10 chars: B + 9 alphanumeric, OR pure 10-char alphanumeric.
# We accept 9-10 chars because real share/short links sometimes include
# a 9-char variant (e.g. ``link.amazon/B0fIJWu2r`` is exactly 9 chars).
_ASIN_REGEX = re.compile(r"/(?:dp|gp/product|product)/([A-Z0-9]{9,10})(?:[/?#]|$)", re.IGNORECASE)

# Short link forms: link.amazon/<token>, amzn.to/<token>.
# A short-link token may BE the ASIN (10 chars) or a redirect id; we
# keep it as-is if 9-10 alphanumeric chars and treat it as an ASIN
# candidate. Real ASINs always appear in the resolved final URL after
# the redirect chain completes.
_SHORT_LINK_TOKEN_REGEX = re.compile(
    r"(?:link\.amazon|amzn\.to|amzn\.asia)/([A-Z0-9]{9,12})",
    re.IGNORECASE,
)

# Hostnames we trust for redirect resolution. We follow any https://
# URL but log a warning for non-Amazon targets; this is cheap defense
# against open-redirect tricks on user-supplied links.
_AMAZON_HOST_HINTS = (
    "amazon.",
    "amzn.",
    "link.amazon",
    "amzn.to",
    "amzn.asia",
)


# ── Data class ───────────────────────────────────────────────────────────

@dataclass
class ResolvedProduct:
    """A product record derived from a user-pasted URL.

    Fields are populated best-effort; missing data is None / empty.
    """

    original_url: str = ""
    final_url: str = ""
    asin: Optional[str] = None
    title: Optional[str] = None
    source: str = ""           # "page_meta", "tavily", "asin_only", "url_only"
    notes: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def resolved(self) -> bool:
        """True iff we have enough info to feed the rest of the pipeline."""
        return bool(self.title) or bool(self.asin)

    def to_dict(self) -> dict:
        return {
            "original_url": self.original_url,
            "final_url": self.final_url,
            "asin": self.asin,
            "title": self.title,
            "source": self.source,
            "notes": list(self.notes),
            "error": self.error,
        }


# ── Pure helpers ─────────────────────────────────────────────────────────

_URL_SCHEME_PREFIXES = ("http://", "https://")


def detect_url(text: str) -> bool:
    """True iff ``text`` starts with an http:// or https:// scheme.

    We deliberately keep this strict: a bare domain like "amazon.com"
    without a scheme is treated as a keyword (it would be too easy to
    misclassify the URL-detection branch otherwise).
    """
    if not text:
        return False
    t = text.strip().lower()
    return t.startswith(_URL_SCHEME_PREFIXES)


def extract_asin(url: str) -> Optional[str]:
    """Extract an Amazon ASIN from any common Amazon URL shape.

    Handles:
        - amazon.com/dp/B0XXXXXXXX
        - amazon.com/gp/product/B0XXXXXXXX
        - amazon.com/Slug-Name/dp/B0XXXXXXXX
        - amzn.to/abc123 (best-effort)
        - link.amazon/B0XXXXXXXX (token IS the ASIN)

    Returns the 10-char uppercase ASIN, or None.
    """
    if not url:
        return None
    m = _ASIN_REGEX.search(url)
    if m:
        return m.group(1).upper()
    # Short-link fallback: the path token after the host IS the ASIN.
    m = _SHORT_LINK_TOKEN_REGEX.search(url)
    if m:
        token = m.group(1).upper()
        if 9 <= len(token) <= 10:
            return token
        # Some amzn.to tokens are 8 chars (Base64 of a redirect id).
        # We can't confidently call those an ASIN; return None and let
        # the redirect chain resolve to the real ASIN.
    return None


def extract_product_title_from_html(html: str) -> Optional[str]:
    """Pull a clean product title from Amazon HTML page metadata.

    Tries, in order:
        1. JSON-LD Product.name
        2. <meta property="og:title">
        3. <title> (with the "Amazon.com:" prefix and trailing "|
                   Amazon.com" suffix stripped)

    Rejects generic error-page titles (404, "Not Found", "Error", etc.)
    so a redirect to a 404 page never produces a fake "product title"
    that breaks the rest of the pipeline.

    Returns a single best-effort title, or None.
    """
    if not html:
        return None

    # Generic error-page patterns to reject
    BAD_TITLE_PATTERNS = (
        "404", "not found", "page not found", "site not found",
        "error", "forbidden", "access denied", "service unavailable",
        "redirecting", "please wait", "just a moment",
    )

    def _looks_like_error(t: str) -> bool:
        low = t.lower().strip()
        if not low or len(low) < 4:
            return True
        for pat in BAD_TITLE_PATTERNS:
            if low == pat or low.startswith(pat) or f" {pat} " in f" {low} ":
                return True
        return False

    # 1. JSON-LD Product block
    m = re.search(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.+?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        try:
            import json
            payload = json.loads(m.group(1))
            if isinstance(payload, dict):
                name = payload.get("name")
                if isinstance(name, str) and name.strip() and not _looks_like_error(name):
                    return name.strip()
            elif isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        name = item.get("name")
                        if isinstance(name, str) and name.strip() and not _looks_like_error(name):
                            return name.strip()
        except Exception:
            pass

    # 2. og:title
    m = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        title = m.group(1).strip()
        if title and not _looks_like_error(title):
            return title

    # 3. <title> with prefix/suffix cleanup
    m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        raw = m.group(1).strip()
        # "Amazon.com: Foo Bar : Amazon.com" → "Foo Bar"
        cleaned = re.sub(r"^Amazon\.com\s*:\s*", "", raw, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*[\|:]\s*Amazon\.com.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if cleaned and not _looks_like_error(cleaned):
            return cleaned

    return None


def _is_safe_redirect_target(url: str) -> bool:
    """Reject redirects to private/internal networks.

    Defensive — only the user-supplied URL is resolved, but an
    attacker could craft a URL whose first hop redirects to a
    metadata IP. Block private/loopback/link-local targets.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        # Reject obvious private/loopback names.
        if host in ("localhost", "0.0.0.0"):
            return False
        if host.startswith("127.") or host.startswith("10.") or host.startswith("192.168."):
            return False
        if host.startswith("169.254.") or host.startswith("::1"):
            return False
        if host.startswith("fc") or host.startswith("fd"):
            # IPv6 unique-local
            return False
        return True
    except Exception:
        return False


def _is_amazon_target(url: str) -> bool:
    """Heuristic: does this URL look like an Amazon destination?

    We log a warning when we follow redirects to a non-Amazon host so
    ops can spot abuse, but we do not block — many Amazon share links
    use 3rd-party click-trackers before the final Amazon redirect.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(hint in host for hint in _AMAZON_HOST_HINTS)


# ── Network helpers ──────────────────────────────────────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _safe_get(url: str, *, timeout: float = 5.0) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """HEAD-follows GET that respects redirect caps and scheme rules.

    Returns ``(final_url, body, error)``. Either final_url and body
    are both set on success, or error is set on failure.
    """
    import requests
    if not _is_safe_redirect_target(url):
        return None, None, "unsafe_redirect_target"
    try:
        # Use GET with stream=True so we don't download megabytes of
        # Amazon HTML — we only need the head + a few KB of metadata.
        r = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            stream=True,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        # Cap response body at 256 KB — that's plenty for the head.
        try:
            body_bytes = b""
            for chunk in r.iter_content(chunk_size=16 * 1024):
                body_bytes += chunk
                if len(body_bytes) > 256 * 1024:
                    break
            body = body_bytes.decode("utf-8", errors="replace")
        finally:
            r.close()
        if r.status_code >= 400:
            return r.url, body, f"http_{r.status_code}"
        return r.url, body, None
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"


# ── Tavily fallback for title resolution ─────────────────────────────────

def _resolve_title_via_tavily(asin: str, *, timeout: float = 8.0) -> Optional[str]:
    """Last-resort title resolution: ask Tavily for the ASIN's product.

    Tavily is configured on Render and returns real Amazon product
    pages for ASIN queries. We pull the title from the first result
    whose URL is on an Amazon domain.
    """
    try:
        from backend import live_research
        envelope = live_research.research(
            f"Amazon product ASIN {asin}",
            max_results=5,
        )
        if envelope.research_status not in ("live", "partial"):
            return None
        # Prefer results whose URL is on amazon.
        for src in envelope.research_sources or []:
            url = (src.get("url") or "").lower()
            if "amazon." in url:
                t = (src.get("title") or "").strip()
                if t:
                    # Strip "Amazon.com: " prefix if present.
                    t = re.sub(r"^Amazon\.com\s*:\s*", "", t, flags=re.IGNORECASE)
                    if t and len(t) < 200:
                        return t
        # Fall back to the first result with a usable title.
        for src in envelope.research_sources or []:
            t = (src.get("title") or "").strip()
            if t and "Amazon" not in t.lower() or "amazon." in (src.get("url") or "").lower():
                t = re.sub(r"^Amazon\.com\s*:\s*", "", t, flags=re.IGNORECASE)
                if t and len(t) < 200:
                    return t
    except Exception as exc:
        logger.info("Tavily title fallback failed for ASIN %s: %s", asin, exc)
    return None


# ── Main entry point ─────────────────────────────────────────────────────

def resolve_url_to_product(
    url: str,
    *,
    timeout: float = 5.0,
    tavily_timeout: float = 8.0,
) -> ResolvedProduct:
    """Resolve a user-pasted URL into a ResolvedProduct.

    Pipeline:
        1. Parse + scheme check.
        2. Try to extract ASIN from the raw URL.
        3. Follow redirects (bounded) to get the final URL.
        4. Re-extract ASIN from the final URL.
        5. Try to fetch the final page; if 200, parse the title from
           page metadata.
        6. If the page fetch failed (Amazon bot detection returns 404
           to most non-browser clients) AND we have an ASIN, ask Tavily
           for the title.
        7. If we still have no title but have an ASIN, use
           "Amazon Product <ASIN>" as a last-resort placeholder.

    The function never raises — all errors are captured in
    ``ResolvedProduct.error`` and the ``notes`` list so callers can
    log them for diagnostics.
    """
    rp = ResolvedProduct(original_url=url)

    if not detect_url(url):
        rp.error = "not_a_url"
        return rp

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        rp.error = f"unsafe_scheme:{parsed.scheme}"
        return rp

    # 1. Pre-redirect ASIN extraction
    pre_asin = extract_asin(url)
    if pre_asin:
        rp.asin = pre_asin
        rp.notes.append(f"asin_pre_redirect={pre_asin}")

    # 2. Follow redirects to get the final URL.
    final_url, body, err = _safe_get(url, timeout=timeout)
    if err and not body:
        rp.notes.append(f"redirect_error:{err}")
    if final_url:
        rp.final_url = final_url
        if not _is_amazon_target(final_url):
            rp.notes.append(f"non_amazon_target:{final_url[:80]}")

    # 3. Post-redirect ASIN extraction (most reliable after link.amazon
    #    resolves to /dp/<ASIN>).
    if final_url:
        post_asin = extract_asin(final_url)
        if post_asin:
            rp.asin = post_asin
            rp.notes.append(f"asin_post_redirect={post_asin}")
        elif pre_asin:
            rp.asin = pre_asin  # keep pre-redirect guess

    # 4. Title from page metadata
    if body:
        title = extract_product_title_from_html(body)
        if title:
            rp.title = title
            rp.source = "page_meta"

    # 5. Tavily fallback when page fetch failed or had no metadata
    if not rp.title and rp.asin:
        title = _resolve_title_via_tavily(rp.asin, timeout=tavily_timeout)
        if title:
            rp.title = title
            rp.source = "tavily"
            rp.notes.append("title_from_tavily")

    # 6. Last-resort placeholder so the rest of the pipeline has
    # SOMETHING to score against. Better than failing silently.
    if not rp.title and rp.asin:
        rp.title = f"Amazon Product {rp.asin}"
        rp.source = "asin_only"
        rp.notes.append("placeholder_title_from_asin")
    elif not rp.title and rp.final_url:
        rp.title = f"Product from {parsed.hostname or url[:60]}"
        rp.source = "url_only"
        rp.notes.append("placeholder_title_from_url")

    return rp


# ── Intent derivation ────────────────────────────────────────────────────

def derive_search_intent(title: str) -> str:
    """Turn a noisy product title into a clean image-search intent.

    Delegates to the existing ``_normalize_product_title`` so the same
    rules apply as for pool candidates.
    """
    try:
        from backend.product_control_agent import _normalize_product_title
        return _normalize_product_title(title or "")
    except Exception:
        # Fallback: lowercase + collapse whitespace.
        if not title:
            return ""
        import re as _re
        return _re.sub(r"\s+", " ", title.lower()).strip()


__all__ = [
    "detect_url",
    "extract_asin",
    "extract_product_title_from_html",
    "resolve_url_to_product",
    "derive_search_intent",
    "ResolvedProduct",
]
