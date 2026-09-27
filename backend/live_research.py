"""backend/live_research.py
Live Internet research layer for the winning-product picker.

Two providers, no fabrication:

* **Tavily** — primary. Used when ``TAVILY_API_KEY`` is configured.
  Endpoint: https://api.tavily.com/search. Returns clean structured hits.

* **DuckDuckGo HTML** — zero-key fallback. Scrape https://html.duckduckgo.com/html/
  via ``requests`` + BeautifulSoup. Works without any API key, but the parser
  is more brittle and can be rate-limited.

The module returns a standardized envelope so callers never need to know
which provider ran:

.. code-block:: python

    {
        "research_status":   "live" | "partial" | "fallback",
        "research_timestamp": "2026-09-26T17:38:00Z",
        "research_provider": "tavily" | "duckduckgo" | "none",
        "research_sources":  [{"title", "snippet", "url", "provider"}, ...],
        "research_summary":  "short text summary of the evidence",
    }

``research_status`` semantics:
    * ``live``    — provider returned usable, in-window evidence.
    * ``partial`` — provider ran but yielded little/no usable evidence.
    * ``fallback``— provider unavailable (no key, network error, parse fail).
                   Callers MUST surface this explicitly to the user.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import unquote

import requests

logger = logging.getLogger("live_research")


# ── Constants ────────────────────────────────────────────────────────────────

TAVILY_ENDPOINT = "https://api.tavily.com/search"
DDG_ENDPOINT = "https://html.duckduckgo.com/html/"

DEFAULT_TIMEOUT = 5  # seconds per HTTP call (was 12 — too long)
DEFAULT_MAX_RESULTS = 6
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ── Data shape ───────────────────────────────────────────────────────────────


@dataclass
class ResearchSource:
    title: str
    snippet: str
    url: str
    provider: str  # "tavily" | "duckduckgo"


@dataclass
class ResearchEnvelope:
    research_status: str  # "live" | "partial" | "fallback"
    research_timestamp: str
    research_provider: str
    research_sources: list[dict] = field(default_factory=list)
    research_summary: str = ""
    research_query: str = ""
    # Image URLs discovered alongside the text research. Used by the
    # researcher to enrich candidates with real product photos.
    research_image_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ── Provider helpers ─────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_tavily_configured() -> bool:
    return bool(os.getenv("TAVILY_API_KEY", "").strip())


def _query_tavily(query: str, max_results: int) -> list[ResearchSource]:
    """Hit the Tavily API. Returns [] on any failure (caller maps to fallback)."""
    if not _is_tavily_configured():
        return []
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    try:
        resp = requests.post(
            TAVILY_ENDPOINT,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_images": True,   # also surface image URLs for candidate enrichment
                "topic": "general",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("Tavily HTTP %s: %s", resp.status_code, resp.text[:200])
            return []
        data = resp.json()
        results = data.get("results") or []
        out: list[ResearchSource] = []
        for r in results:
            out.append(
                ResearchSource(
                    title=(r.get("title") or "").strip()[:200],
                    snippet=(r.get("content") or "").strip()[:500],
                    url=(r.get("url") or "").strip(),
                    provider="tavily",
                )
            )
        return out
    except Exception as exc:
        logger.warning("Tavily request failed: %s", exc)
        return []


def _query_duckduckgo(query: str, max_results: int) -> list[ResearchSource]:
    """Zero-key DuckDuckGo HTML scrape. Returns [] on any failure."""
    try:
        resp = requests.post(
            DDG_ENDPOINT,
            data={"q": query, "kl": "us-en"},
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("DDG HTTP %s", resp.status_code)
            return []

        from bs4 import BeautifulSoup  # local import — keep top-level clean

        soup = BeautifulSoup(resp.text, "html.parser")
        out: list[ResearchSource] = []
        for result in soup.select("div.result"):
            if len(out) >= max_results:
                break
            link_el = result.select_one("a.result__a")
            snippet_el = result.select_one(".result__snippet")
            if not link_el:
                continue
            href = link_el.get("href", "")
            # DDG wraps real URLs in //duckduckgo.com/l/?uddg=<encoded>
            if isinstance(href, str) and "uddg=" in href:
                from urllib.parse import parse_qs, urlparse

                try:
                    parsed = urlparse(href)
                    qs = parse_qs(parsed.query)
                    real_url = qs.get("uddg", [""])[0]
                    href = unquote(real_url) if real_url else href
                except Exception:
                    pass
            title = link_el.get_text(" ", strip=True)
            snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
            if not title or not href:
                continue
            out.append(
                ResearchSource(
                    title=title[:200],
                    snippet=snippet[:500],
                    url=href,
                    provider="duckduckgo",
                )
            )
        return out
    except Exception as exc:
        logger.warning("DDG request failed: %s", exc)
        return []


def _summarize_sources(query: str, sources: list[ResearchSource]) -> str:
    """Produce a short human-readable summary of what was found."""
    if not sources:
        return f"No usable evidence for '{query}'."
    snippets = []
    for s in sources[:3]:
        s_txt = (s.snippet or "").strip().rstrip(".")
        if s_txt and len(s_txt) > 20:
            snippets.append(s_txt[:160])
    if not snippets:
        return f"Found {len(sources)} sources for '{query}'."
    return " · ".join(snippets)


# ── Public entry point ───────────────────────────────────────────────────────


def _collect_image_urls_from_sources(sources: list[ResearchSource]) -> list[str]:
    """Best-effort: extract likely image URLs from research sources.

    Research sources are search result pages, not images, but many
    publishers embed product imagery in the page. We extract URLs whose
    file extension looks like an image. The product control agent will
    HEAD-validate each candidate before accepting it.
    """
    out: list[str] = []
    image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    for s in sources:
        url = (s.url or "").lower()
        if any(url.endswith(ext) for ext in image_exts):
            out.append(s.url)
    return out


def research(
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> ResearchEnvelope:
    """Run live research for a query.

    Strategy:
        1. If Tavily is configured, try it first.
        2. If Tavily returns nothing OR is not configured, fall back to DuckDuckGo.
        3. If neither yields usable evidence, return ``fallback`` envelope.

    Never fabricates sources or summary text — ``research_summary`` is always
    derived from real returned snippets.
    """
    q = (query or "").strip()
    if not q:
        return ResearchEnvelope(
            research_status="fallback",
            research_timestamp=_now_iso(),
            research_provider="none",
            research_summary="Empty query.",
            research_query=q,
        )

    # 1. Tavily
    if _is_tavily_configured():
        tav_hits = _query_tavily(q, max_results)
        if tav_hits:
            tav_images = _collect_image_urls_from_sources(tav_hits)
            return ResearchEnvelope(
                research_status="live" if len(tav_hits) >= 3 else "partial",
                research_timestamp=_now_iso(),
                research_provider="tavily",
                research_sources=[asdict(s) for s in tav_hits],
                research_summary=_summarize_sources(q, tav_hits),
                research_query=q,
                research_image_urls=tav_images,
            )
        logger.info("Tavily returned no hits, falling back to DuckDuckGo")

    # 2. DuckDuckGo fallback
    ddg_hits = _query_duckduckgo(q, max_results)
    if ddg_hits:
        ddg_images = _collect_image_urls_from_sources(ddg_hits)
        return ResearchEnvelope(
            research_status="live" if len(ddg_hits) >= 3 else "partial",
            research_timestamp=_now_iso(),
            research_provider="duckduckgo",
            research_sources=[asdict(s) for s in ddg_hits],
            research_summary=_summarize_sources(q, ddg_hits),
            research_query=q,
            research_image_urls=ddg_images,
        )

    # 3. No evidence anywhere
    return ResearchEnvelope(
        research_status="fallback",
        research_timestamp=_now_iso(),
        research_provider="none",
        research_summary=f"Live research unavailable for '{q}'.",
        research_query=q,
    )


def research_images(
    query: str,
    *,
    max_results: int = 3,
) -> list[str]:
    """Image-focused research: returns a list of candidate image URLs.

    Used by the product researcher to enrich candidates with real
    product photos when the pool fallback is a data: URI placeholder.
    The product control agent validates each URL via HEAD + content-type
    before accepting it.

    Bounded by both per-request timeout (DEFAULT_TIMEOUT) and a maximum
    number of URLs returned, so a single call cannot hang the
    /find-winner pipeline.
    """
    q = (query or "").strip()
    if not q:
        return []
    urls: list[str] = []

    # 1. Tavily image search (only if configured — else skip to keep budget tight)
    if _is_tavily_configured():
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        try:
            resp = requests.post(
                TAVILY_ENDPOINT,
                json={
                    "api_key": api_key,
                    "query": f"{q} product photo",
                    "max_results": max_results,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_images": True,
                    "topic": "general",
                },
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                # Tavily returns `images` as [{url, description}, ...]
                for img in (data.get("images") or []):
                    u = (img.get("url") or "").strip()
                    if u.startswith(("http://", "https://")) and u not in urls:
                        urls.append(u)
                        if len(urls) >= max_results:
                            break
        except Exception as exc:
            logger.warning("Tavily image search failed: %s", exc)

    # 2. DuckDuckGo image-search HTML fallback. NOTE: this endpoint often
    #    returns a JS shell that doesn't actually contain image tiles; we
    #    cap the parse time and move on. Failure is silent.
    if len(urls) < max_results:
        try:
            resp = requests.post(
                "https://duckduckgo.com/",
                data={"q": f"{q} product image", "iax": "images", "ia": "images"},
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html"},
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.status_code == 200:
                from bs4 import BeautifulSoup  # local import
                soup = BeautifulSoup(resp.text, "html.parser")
                for tile in soup.select("a.tile--img"):
                    href = tile.get("href") or ""
                    if href.startswith(("http://", "https://")) and href not in urls:
                        urls.append(href)
                    if len(urls) >= max_results:
                        break
        except Exception as exc:
            logger.warning("DDG image search failed: %s", exc)

    return urls[:max_results]


def is_configured() -> bool:
    """Returns True if any provider is reachable in this environment."""
    return _is_tavily_configured() or True  # DDG needs no key