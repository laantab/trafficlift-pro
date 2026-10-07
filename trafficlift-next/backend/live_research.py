"""backend/live_research.py
Live Internet research layer for the winning-product picker.

Three providers, no fabrication:

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
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import unquote

import requests

logger = logging.getLogger("live_research")
_DIAGNOSTICS = threading.local()

def _problem(message):
    errors = getattr(_DIAGNOSTICS, 'errors', None)
    if errors is not None: errors.append(message)



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
    image_urls: list[str] = field(default_factory=list)


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

    research_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ── Provider helpers ─────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_tavily_configured() -> bool:
    return bool(os.getenv("TAVILY_API_KEY", "").strip())


def _query_tavily(query: str, max_results: int) -> tuple[list[ResearchSource], list[str]]:
    """Hit the Tavily API. Returns (results, image_urls).

    The image_urls come from Tavily's `images` response field (enabled by
    `include_images: True`). This is the PRIMARY way real product photos
    get into our pipeline — article URLs in `results` rarely end in
    image extensions, so we MUST extract the dedicated `images` field.

    Returns ([], []) on any failure (caller maps to fallback).
    """
    if not _is_tavily_configured():
        return [], []
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    try:
        # One bounded retry for transient transport failure. Authentication,
        # quota and billing responses are returned without retries.
        for attempt in range(2):
            try:
                resp = requests.post(
                    TAVILY_ENDPOINT,
                    json={
                        "api_key": api_key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                        "include_answer": False,
                        "include_images": True,
                        "include_image_descriptions": True,
                        "topic": "general",
                    },
                    timeout=(3, 10),
                )
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt:raise
        if resp.status_code != 200:
            logger.warning("Tavily HTTP %s", resp.status_code)
            _problem(f"Tavily HTTP {resp.status_code}")
            return [], []
        data = resp.json()
        results = data.get("results") or []
        out: list[ResearchSource] = []
        for r in results:
            out.append(
                ResearchSource(
                    title=(r.get("title") or "").strip()[:200],
                    snippet=(r.get("content") or "").strip()[:3000],
                    url=(r.get("url") or "").strip(),
                    provider="tavily",
                    image_urls=[(image if isinstance(image,str) else image.get("url", "")) for image in (r.get("images") or []) if isinstance(image,(str,dict))],
                )
            )
        # Extract the dedicated `images` field. Tavily returns it as a
        # list of plain URL strings (newer API) OR a list of
        # ``{url, description}`` dicts (older API). Handle both.
        images: list[str] = []
        for img in (data.get("images") or []):
            u: Optional[str] = None
            if isinstance(img, str):
                u = img.strip()
            elif isinstance(img, dict):
                for k in ("url", "image", "src", "image_url"):
                    v = img.get(k)
                    if isinstance(v, str) and v.strip():
                        u = v.strip()
                        break
            if u and u.startswith(("http://", "https://")) and u not in images:
                images.append(u)
        return out, images
    except Exception as exc:
        logger.warning("Tavily request failed (%s)", type(exc).__name__)
        _problem("Tavily request failed: " + type(exc).__name__)
        return [], []


def _query_duckduckgo(query: str, max_results: int) -> tuple[list[ResearchSource], list[str]]:
    """Zero-key DuckDuckGo HTML scrape. Returns (results, image_urls)."""
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
            _problem(f"DuckDuckGo HTTP {resp.status_code}")
            logger.warning("DDG HTTP %s", resp.status_code)
            return [], []

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
        # DDG HTML rarely exposes direct image URLs in the result list,
        # but some result URLs DO end in image extensions. Defer to the
        # same defensive scan we use for Tavily sources.
        if not out: _problem("DuckDuckGo returned no parsed results")
        image_urls = _collect_image_urls_from_sources(out)
        return out, image_urls
    except Exception as exc:
        _problem("DuckDuckGo request failed: " + type(exc).__name__)
        logger.warning("DDG request failed: %s", exc)
        return [], []


def _query_brave(query, max_results):
    """Public web results with observed title/snippet selectors; no paid API."""
    try:
        response = requests.get('https://search.brave.com/search',
                                params={'q': query, 'source': 'web'},
                                headers={'User-Agent': 'Mozilla/5.0'}, timeout=9)
        if response.status_code != 200 or len(response.content) > 2_000_000:
            _problem(f'Brave HTTP {response.status_code}')
            return [], []
        from bs4 import BeautifulSoup
        from urllib.parse import urlsplit
        soup = BeautifulSoup(response.text, 'html.parser')
        results = []
        seen = set()
        for card in soup.select('.result-content'):
            title = card.select_one('.search-snippet-title')
            link = card.select_one('a[href]')
            if not title or not link:
                continue
            url = link.get('href','')
            parsed = urlsplit(url)
            if parsed.scheme not in {'http','https'} or not parsed.hostname or url in seen:
                continue
            if parsed.hostname in {'search.brave.com','brave.com'}:
                continue
            description = card.select_one('.generic-snippet')
            results.append(ResearchSource(title=(title.get('title') or title.get_text(' ',strip=True))[:200],
                snippet=description.get_text(' ',strip=True)[:500] if description else '',
                url=url, provider='brave_web'))
            seen.add(url)
            if len(results) >= max_results:
                break
        if not results:
            _problem('Brave returned no parsed web result cards')
        return results, []
    except Exception as exc:
        _problem('Brave request failed: ' + type(exc).__name__)
        return [], []


def _query_bing_html(query, max_results):
    """Use actual web result cards when the RSS feed loses query restrictions."""
    try:
        response = requests.get('https://www.bing.com/search', params={'q': query},
                                headers={'User-Agent': DEFAULT_USER_AGENT}, timeout=4)
        if response.status_code != 200 or len(response.content) > 2_000_000:
            _problem(f'Bing web HTTP {response.status_code}')
            return [], []
        from bs4 import BeautifulSoup
        from urllib.parse import urlsplit, parse_qs
        import base64
        soup = BeautifulSoup(response.text, 'html.parser')
        results = []
        for card in soup.select('li.b_algo'):
            link = card.select_one('h2 a[href]')
            if not link:
                continue
            url = link.get('href', '')
            parsed = urlsplit(url)
            # Bing's click wrapper stores the destination as URL-safe base64.
            if parsed.hostname in {'bing.com', 'www.bing.com'} and parsed.path == '/ck/a':
                encoded = parse_qs(parsed.query).get('u', [''])[0]
                if not encoded.startswith('a1'):
                    continue
                encoded = encoded[2:]
                try:
                    url = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode('utf-8')
                except (ValueError, UnicodeError):
                    continue
            if urlsplit(url).scheme not in {'http','https'}:
                continue
            snippet = card.select_one('.b_caption p') or card.select_one('p')
            results.append(ResearchSource(title=link.get_text(' ',strip=True)[:200],url=url,
                                          snippet=snippet.get_text(' ',strip=True)[:500] if snippet else '',
                                          provider='bing_web'))
            if len(results) >= max_results:
                break
        if not results:
            _problem('Bing web returned no parsed result cards')
        return results, _collect_image_urls_from_sources(results)
    except Exception as exc:
        _problem('Bing web request failed: ' + type(exc).__name__)
        return [], []


def _filter_search_hits(query, hits):
    """Never label off-site RSS results as matches to a merchant query."""
    from urllib.parse import urlsplit
    constraints = re.findall(r'\bsite:([^\s]+)', query, re.I)
    if not constraints:
        return hits
    filtered = []
    for hit in hits:
        parsed = urlsplit(hit.url)
        for constraint in constraints:
            domain, _, path = constraint.partition('/')
            host = (parsed.hostname or '').lower()
            if (host == domain.lower() or host.endswith('.'+domain.lower())) and (not path or parsed.path.startswith('/'+path)):
                filtered.append(hit)
                break
    if len(filtered) < len(hits):
        _problem(f'Excluded {len(hits)-len(filtered)} results outside requested seller listing paths')
    return filtered


def _query_bing_rss(query: str, max_results: int):
    """Independent zero-key RSS fallback. Empty/error feeds are not evidence."""
    try:
        response = requests.get('https://www.bing.com/search',
                                params={'q': query, 'format': 'rss'},
                                headers={'User-Agent': DEFAULT_USER_AGENT}, timeout=4)
        if response.status_code != 200:
            _problem(f'Bing RSS HTTP {response.status_code}')
            return [], []
        if len(response.content) > 2_000_000:
            _problem('Bing RSS response too large')
            return [], []
        from backend.safe_rss import parse_rss
        root = parse_rss(response.content)
        if root.tag != 'rss':
            _problem('Bing did not return an RSS feed')
            return [], []
        out = []
        from urllib.parse import urlsplit
        from bs4 import BeautifulSoup
        for item in root.findall('./channel/item')[:max_results]:
            title = (item.findtext('title') or '').strip()
            url = (item.findtext('link') or '').strip()
            parsed = urlsplit(url)
            if not title or parsed.scheme not in {'http','https'} or not parsed.hostname:
                continue
            snippet = BeautifulSoup(item.findtext('description') or '', 'html.parser').get_text(' ',strip=True)
            out.append(ResearchSource(title=title[:200],snippet=snippet[:500],url=url,provider='bing_rss'))
        if not out: _problem('Bing RSS returned no results')
        return out, _collect_image_urls_from_sources(out)
    except Exception as exc:
        _problem('Bing RSS request failed: ' + type(exc).__name__)
        logger.warning('Bing RSS request failed (%s)', type(exc).__name__)
        return [], []


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

    The returned envelope's ``research_image_urls`` is populated from the
    provider's dedicated images field (Tavily `images`, DDG URL-extension
    scan) — NOT just from URLs embedded in the text result URLs.
    """
    _DIAGNOSTICS.errors = []
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
        tav_hits, tav_images = _query_tavily(q, max_results)
        tav_hits = _filter_search_hits(q, tav_hits)
        # Accept the Tavily response when EITHER results OR images are
        # non-empty. Some Tavily responses return images without text
        # results (or vice versa) and we still want the data we got.
        if tav_hits or tav_images:
            # Defensive: also pick up any image URLs hidden in result URLs
            # (covers rare older Tavily responses without a dedicated
            # images field).
            for u in _collect_image_urls_from_sources(tav_hits):
                if u not in tav_images:
                    tav_images.append(u)
            return ResearchEnvelope(
                research_status="live" if len(tav_hits) >= 3 else "partial",
                research_timestamp=_now_iso(),
                research_provider="tavily",
                research_sources=[asdict(s) for s in tav_hits],
                research_summary=_summarize_sources(q, tav_hits) if tav_hits
                                else f"Tavily returned {len(tav_images)} image(s) for '{q}'.",
                research_query=q,
                research_image_urls=tav_images,
            )
        # A configured provider failure must not be hidden by public scraping.
        errors = list(_DIAGNOSTICS.errors)
        if not errors: errors.append('Tavily returned no usable results')
        return ResearchEnvelope(research_status='fallback', research_timestamp=_now_iso(),
            research_provider='tavily', research_sources=[], research_image_urls=[],
            research_summary='Configured research provider did not return usable evidence.',
            research_query=q, research_errors=errors)

    # Prefer the public provider that actually returns constrained listing
    # results. DDG often serves a 202 challenge and Bing RSS can lose intent.
    brave_hits, brave_images = _query_brave(q, max_results)
    brave_hits = _filter_search_hits(q, brave_hits)
    if brave_hits:
        return ResearchEnvelope(research_status='live' if len(brave_hits)>=3 else 'partial',
            research_timestamp=_now_iso(), research_provider='brave_web',
            research_sources=[asdict(source) for source in brave_hits],
            research_summary=_summarize_sources(q,brave_hits), research_query=q,
            research_image_urls=brave_images, research_errors=list(_DIAGNOSTICS.errors))

    # 2. DuckDuckGo fallback
    ddg_hits, ddg_images = _query_duckduckgo(q, max_results)
    ddg_hits = _filter_search_hits(q, ddg_hits)
    if ddg_hits:
        return ResearchEnvelope(
            research_status="live" if len(ddg_hits) >= 3 else "partial",
            research_timestamp=_now_iso(),
            research_provider="duckduckgo",
            research_sources=[asdict(s) for s in ddg_hits],
            research_summary=_summarize_sources(q, ddg_hits),
            research_query=q,
            research_image_urls=ddg_images,
        )

    # Prefer full web results; RSS sometimes drops site/path constraints.
    web_hits, web_images = _query_bing_html(q, max_results)
    web_hits = _filter_search_hits(q, web_hits)
    if web_hits:
        return ResearchEnvelope(research_status='live' if len(web_hits)>=3 else 'partial',
            research_timestamp=_now_iso(), research_provider='bing_web',
            research_sources=[asdict(source) for source in web_hits],
            research_summary=_summarize_sources(q,web_hits), research_query=q,
            research_image_urls=web_images, research_errors=list(_DIAGNOSTICS.errors))

    # 3. An independent public feed; no API key or paid calls.
    bing_hits, bing_images = _query_bing_rss(q, max_results)
    bing_hits = _filter_search_hits(q, bing_hits)
    if bing_hits:
        return ResearchEnvelope(research_status='live' if len(bing_hits)>=3 else 'partial',
            research_timestamp=_now_iso(), research_provider='bing_rss',
            research_sources=[asdict(source) for source in bing_hits],
            research_summary=_summarize_sources(q,bing_hits), research_query=q,
            research_image_urls=bing_images, research_errors=list(_DIAGNOSTICS.errors))

    # 3. No evidence anywhere
    return ResearchEnvelope(
        research_status="fallback",
        research_timestamp=_now_iso(),
        research_provider="none",
        research_summary=f"Live research unavailable for '{q}'.",
        research_errors=list(_DIAGNOSTICS.errors),
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

    NOTE on Tavily response shape: as of 2025, Tavily's ``include_images``
    flag returns ``images`` as a list of PLAIN URL STRINGS (not
    ``[{url, description}, ...]``). Earlier versions returned dicts.
    We accept BOTH shapes defensively.
    """
    q = (query or "").strip()
    if not q:
        return []
    urls: list[str] = []

    def _extract_url(img) -> str:
        """Accept either a plain URL string OR a dict with a 'url' key.

        Returns the empty string if no usable URL can be extracted.
        """
        if isinstance(img, str):
            return img.strip()
        if isinstance(img, dict):
            for k in ("url", "image", "src", "image_url"):
                v = img.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return ""

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
                # Tavily: `images` is a list of URL strings (newer API) OR
                # `[{url, description}, ...]` (older API). Handle both.
                for img in (data.get("images") or []):
                    u = _extract_url(img)
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
