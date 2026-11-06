"""TrafficLift Product URL Finder V2.

A single-purpose resolver for exact product links. The resolver does not search
for substitutes. It first tries a browser-impersonated fetch so Amazon short
links can resolve through the same redirect/metadata paths a normal browser
sees. If Amazon still serves a generic shopping page, product-specific metadata
from that exact response may establish identity; otherwise resolution fails.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
from fastapi import HTTPException


logger = logging.getLogger(__name__)


_AMAZON_HOST_RE = re.compile(
    r"(?:^|\.)(?:amazon\.(?:com|ca|co\.uk|com\.au|de|fr|it|es|co\.jp|in|com\.mx)|link\.amazon|amzn\.to|amzn\.asia)$",
    re.I,
)
_ASIN_PATH_RE = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?#]|$)",
    re.I,
)
_SHORT_HOSTS = {"link.amazon", "amzn.to", "amzn.asia"}
_GENERIC_TITLE_BITS = (
    "amazon.com: online shopping",
    "amazon.com. spend less",
    "amazon shopping",
    "shop online",
    "department",
    "search results",
    "robot check",
    "sorry! something went wrong",
    "page not found",
    "sign in",
    "redirecting",
    "just a moment",
)
_BAD_IMAGE_BITS = ("amazon-logo", "nav-sprite", "favicon", "/logo", "sprite")


@dataclass
class FetchSnapshot:
    final_url: str
    html: str
    status_code: int
    history_urls: list[str]


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _is_amazon_url(url: str) -> bool:
    return bool(_AMAZON_HOST_RE.search(_host(url)))


def _is_short_link(url: str) -> bool:
    return _host(url) in _SHORT_HOSTS


def _asin_from_url(url: str) -> Optional[str]:
    if not _is_amazon_url(url):
        return None
    match = _ASIN_PATH_RE.search(urlsplit(url).path)
    if match:
        return match.group(1).upper()
    query = parse_qs(urlsplit(url).query)
    for key in ("asin", "ASIN"):
        value = (query.get(key) or [""])[0]
        if re.fullmatch(r"[A-Z0-9]{10}", value, re.I):
            return value.upper()
    return None


def _browser_get(url: str, timeout: float = 12.0) -> FetchSnapshot:
    """Fetch with a real browser TLS/HTTP fingerprint, then fall back to requests."""
    history_urls: list[str] = []
    try:
        from curl_cffi import requests as cffi_requests

        reply = cffi_requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            impersonate="chrome",
            headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
            },
        )
        history_urls = [str(getattr(item, "url", "") or "") for item in (getattr(reply, "history", None) or [])]
        return FetchSnapshot(
            final_url=str(getattr(reply, "url", "") or url),
            html=str(getattr(reply, "text", "") or ""),
            status_code=int(getattr(reply, "status_code", 0) or 0),
            history_urls=[u for u in history_urls if u],
        )
    except Exception:
        import requests

        reply = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/152.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        history_urls = [str(getattr(item, "url", "") or "") for item in (getattr(reply, "history", None) or [])]
        return FetchSnapshot(
            final_url=str(reply.url or url),
            html=reply.text or "",
            status_code=int(reply.status_code),
            history_urls=[u for u in history_urls if u],
        )


def _javascript_get(url: str, timeout: float = 30.0) -> FetchSnapshot:
    """Render one URL with JavaScript, used only as a short-link fallback."""
    import brimp

    with brimp.Session() as session:
        reply = session.get(url, timeout=timeout)
        html = str(
            getattr(reply, "html", "")
            or getattr(reply, "text", "")
            or ""
        )
        final_url = str(getattr(reply, "url", "") or url)
        status_code = int(getattr(reply, "status_code", 0) or 0)
        return FetchSnapshot(
            final_url=final_url,
            html=html,
            status_code=status_code,
            history_urls=[],
        )


def _mission_debug(html: str, base_url: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    section = soup.select_one("#previously-viewed-content")
    links = []
    data_asins = []
    if section:
        for link in section.select("a[href]"):
            href = str(link.get("href") or "")
            match = _ASIN_PATH_RE.search(urlsplit(urljoin(base_url, href)).path)
            if match:
                links.append(match.group(1).upper())
        for node in section.select("[data-asin]"):
            value = str(node.get("data-asin") or "").strip().upper()
            if re.fullmatch(r"[A-Z0-9]{10}", value):
                data_asins.append(value)
    label = soup.select_one("#mission-page-label")
    return {
        "html_len": len(html or ""),
        "has_previous": bool(section),
        "previous_asin_count": len(links),
        "first_previous_asin": links[0] if links else "",
        "data_asin_count": len(data_asins),
        "first_data_asin": data_asins[0] if data_asins else "",
        "mission_label": " ".join(label.stripped_strings)[:80] if label else "",
    }


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _safe_json(value: str):
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _walk_products(obj):
    if isinstance(obj, list):
        for item in obj:
            yield from _walk_products(item)
    elif isinstance(obj, dict):
        raw_type = obj.get("@type")
        types = [raw_type] if isinstance(raw_type, str) else (raw_type or [])
        if "Product" in types:
            yield obj
        for value in obj.values():
            if isinstance(value, (dict, list)):
                yield from _walk_products(value)


def _looks_specific_title(title: str) -> bool:
    low = " ".join(title.lower().split())
    if len(low) < 5:
        return False
    if any(bit in low for bit in _GENERIC_TITLE_BITS):
        return False
    if low in {"amazon", "amazon.com", "amazon.com services llc"}:
        return False
    return True


def _looks_product_image(url: str) -> bool:
    low = (url or "").lower()
    if not low.startswith(("http://", "https://")):
        return False
    if any(bit in low for bit in _BAD_IMAGE_BITS):
        return False
    return True

def _upgrade_amazon_image(url: str) -> str:
    if not url or "m.media-amazon.com/images/I/" not in url:
        return url
    # Remove Amazon thumbnail/rendering modifiers and request a large image.
    # Example: 51abc._MCnd_AC_.jpg -> 51abc._AC_SL1500_.jpg
    upgraded = re.sub(r"\._[^./]+_(?=\.(?:jpg|jpeg|png|webp)$)", "", url, flags=re.I)
    upgraded = re.sub(
        r"\.(jpg|jpeg|png|webp)$",
        r"._AC_SL1500_.\1",
        upgraded,
        flags=re.I,
    )
    return upgraded


def _extract_mission_primary_product(html: str, base_url: str) -> Optional[dict]:
    """Extract Amazon's first 'Previously viewed' product from a mission page.

    Amazon renders this section in more than one DOM shape. Some versions put
    the ASIN in /dp/<ASIN> hrefs; others expose it only through data-asin.
    In both cases we accept only the FIRST product identity inside Amazon's
    Previously viewed section and never select from recommendation carousels.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    section = soup.select_one("#previously-viewed-content")
    if not section:
        return None

    identity_node = None
    asin = None

    # Preferred shape: an explicit product URL inside Previously viewed.
    for link in section.select("a[href]"):
        href = str(link.get("href") or "")
        match = _ASIN_PATH_RE.search(urlsplit(urljoin(base_url, href)).path)
        if match:
            identity_node = link
            asin = match.group(1).upper()
            break

    # Render/JS shape: Amazon may omit href product URLs but retain data-asin.
    if not asin:
        for node in section.select("[data-asin]"):
            value = str(node.get("data-asin") or "").strip().upper()
            if re.fullmatch(r"[A-Z0-9]{10}", value):
                identity_node = node
                asin = value
                break

    if not identity_node or not asin:
        return None

    # Find the smallest ancestor that contains both useful product text and an
    # image. This avoids depending on Amazon's generated CSS class names.
    container = identity_node
    for _ in range(10):
        has_image = (
            getattr(container, "name", None) == "img"
            or bool(container.find("img"))
        )
        text = " ".join(getattr(container, "stripped_strings", []))
        if has_image and len(text) >= 12:
            break
        parent = getattr(container, "parent", None)
        if not parent or parent is section:
            if parent is section:
                container = parent
            break
        container = parent

    image_node = (
        container
        if getattr(container, "name", None) == "img"
        else container.find("img") if container else None
    )
    image = ""
    if image_node:
        image = _first_text(
            image_node.get("data-old-hires"),
            image_node.get("data-a-hires"),
            image_node.get("data-src"),
            image_node.get("src"),
        )

    # Prefer title text attached to the same ASIN. If that DOM shape is absent,
    # Amazon image alt text is product-specific and safer than the whole card's
    # price/review text.
    title = ""
    for link in section.select("a[href]"):
        href = str(link.get("href") or "")
        if asin not in href:
            continue
        text = " ".join(link.stripped_strings).strip()
        if _looks_specific_title(text) and len(text) > len(title):
            title = text

    if not title and image_node:
        alt = str(image_node.get("alt") or "").strip()
        if _looks_specific_title(alt):
            title = alt

    if not title and container:
        card_text = " ".join(container.stripped_strings).strip()
        # Avoid accepting obvious price-only/ratings-only card fragments.
        if _looks_specific_title(card_text) and len(card_text) >= 15:
            title = card_text[:500]

    if not _looks_specific_title(title) or not _looks_product_image(image):
        return None

    return {
        "name": title,
        "image_url": _upgrade_amazon_image(urljoin(base_url, image)),
        "asin": asin,
        "description": "",
        "final_url": f"https://www.amazon.com/dp/{asin}",
        "source": "amazon_mission_previously_viewed",
    }



def _extract_metadata(html: str, base_url: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")

    title_node = soup.select_one("#productTitle")
    og_title = soup.select_one('meta[property="og:title"]')
    twitter_title = soup.select_one('meta[name="twitter:title"]')
    title = _first_text(
        title_node.get_text(" ", strip=True) if title_node else "",
        og_title.get("content") if og_title else "",
        twitter_title.get("content") if twitter_title else "",
    )

    image = ""
    landing = soup.select_one("#landingImage, #imgBlkFront, img[data-a-dynamic-image]")
    if landing:
        image = _first_text(landing.get("data-old-hires"), landing.get("src"))
        if not image:
            variants = _safe_json(landing.get("data-a-dynamic-image") or "")
            if isinstance(variants, dict) and variants:
                try:
                    image = max(
                        variants,
                        key=lambda key: (variants[key][0] or 0) * (variants[key][1] or 0),
                    )
                except (TypeError, IndexError, KeyError):
                    image = next(iter(variants), "")

    og_image = soup.select_one('meta[property="og:image"]')
    twitter_image = soup.select_one('meta[name="twitter:image"]')
    if not image:
        image = _first_text(
            og_image.get("content") if og_image else "",
            twitter_image.get("content") if twitter_image else "",
        )

    description_node = soup.select_one(
        'meta[property="og:description"], meta[name="description"]'
    )
    description = _first_text(description_node.get("content") if description_node else "")

    asin = _asin_from_url(base_url)
    asin_input = soup.select_one('input#ASIN, input[name="ASIN"]')
    if not asin and asin_input:
        value = str(asin_input.get("value") or "").strip().upper()
        if re.fullmatch(r"[A-Z0-9]{10}", value):
            asin = value

    product_obj = None
    for script in soup.select('script[type="application/ld+json"]'):
        payload = _safe_json(script.string or script.get_text() or "")
        if payload is None:
            continue
        for product in _walk_products(payload):
            product_obj = product
            break
        if product_obj:
            break

    if product_obj:
        title = _first_text(product_obj.get("name"), title)
        raw_image = product_obj.get("image")
        if not image:
            if isinstance(raw_image, list):
                raw_image = raw_image[0] if raw_image else ""
            if isinstance(raw_image, dict):
                raw_image = raw_image.get("url") or raw_image.get("contentUrl")
            if isinstance(raw_image, str):
                image = raw_image
        description = _first_text(product_obj.get("description"), description)
        product_url = product_obj.get("url")
        if isinstance(product_url, str) and not asin:
            asin = _asin_from_url(urljoin(base_url, product_url))

    for node in soup.select(
        'link[rel="canonical"], meta[property="og:url"], meta[name="twitter:url"]'
    ):
        value = node.get("href") or node.get("content") or ""
        target = urljoin(base_url, str(value).strip())
        if not asin:
            asin = _asin_from_url(target)

    image = urljoin(base_url, image) if image else ""
    return {
        "name": title.strip(),
        "image_url": image,
        "asin": asin,
        "description": description.strip(),
    }


def _asin_from_redirect_chain(snapshot: FetchSnapshot) -> Optional[str]:
    for candidate in [*snapshot.history_urls, snapshot.final_url]:
        asin = _asin_from_url(candidate)
        if asin:
            return asin
        query = parse_qs(urlsplit(candidate).query)
        for key, values in query.items():
            if key.lower() not in {
                "url", "u", "redirect", "redirecturl", "destination", "dest"
            }:
                continue
            for value in values[:3]:
                nested = unquote(value)
                if nested.startswith("/"):
                    nested = urljoin("https://www.amazon.com", nested)
                asin = _asin_from_url(nested)
                if asin:
                    return asin
    return None


def _pin_factory_fallback(url: str) -> Optional[dict]:
    import requests

    endpoint = os.getenv(
        "PIN_FACTORY_RESOLVER_URL",
        "https://pin-factory-pro.onrender.com/api/text",
    )
    try:
        reply = requests.post(
            endpoint,
            json={"input": url, "niche": "home", "tone": "high-energy"},
            timeout=20,
            headers={"User-Agent": "TrafficLift-Pro/2.0"},
        )
        if reply.status_code != 200:
            return None
        payload = reply.json()
    except (requests.RequestException, ValueError, TypeError):
        return None

    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    name = str(payload.get("productName") or "").strip()
    image = str(payload.get("productImage") or "").strip()
    if not _looks_specific_title(name) or not _looks_product_image(image):
        return None
    return {
        "name": name,
        "image_url": image,
        "asin": _asin_from_url(str(payload.get("finalUrl") or "")),
        "description": "",
        "final_url": str(payload.get("finalUrl") or payload.get("url") or url),
        "source": "pin_factory_exact_url",
    }


def resolve_product_identity(url: str) -> dict:
    clean = (url or "").strip()
    if not clean.startswith(("http://", "https://")):
        raise ValueError("A full product URL is required.")
    if not _is_amazon_url(clean):
        raise ValueError("Product URL Finder V2 currently handles Amazon product links.")

    snapshot = _browser_get(clean)
    if _is_short_link(clean):
        logger.info(
            "Product URL Finder static short-link status=%s diagnostics=%s",
            snapshot.status_code,
            _mission_debug(snapshot.html, snapshot.final_url or clean),
        )
    if snapshot.status_code >= 500 or not snapshot.html:
        if _is_short_link(clean):
            try:
                rendered = _javascript_get(clean)
                logger.info(
                    "Product URL Finder JS short-link status=%s diagnostics=%s",
                    rendered.status_code,
                    _mission_debug(rendered.html, rendered.final_url or clean),
                )
                mission = _extract_mission_primary_product(
                    rendered.html,
                    rendered.final_url or clean,
                )
                if mission:
                    logger.info(
                        "Product URL Finder JS resolved mission asin=%s",
                        mission.get("asin"),
                    )
                    mission["source"] = "amazon_mission_js_previously_viewed"
                    return mission
            except Exception as exc:
                logger.warning(
                    "Product URL Finder JS short-link failed error=%s:%s",
                    type(exc).__name__,
                    str(exc)[:300],
                )
        fallback = _pin_factory_fallback(clean)
        if fallback:
            return fallback
        raise ValueError("The product link could not be resolved.")

    chain_asin = _asin_from_redirect_chain(snapshot)
    metadata = _extract_metadata(snapshot.html, snapshot.final_url or clean)
    asin = chain_asin or metadata.get("asin")

    # If an exact ASIN appeared anywhere in the redirect chain, normalize to
    # the canonical product page and re-read metadata with the browser client.
    if asin:
        canonical = f"https://www.amazon.com/dp/{asin}"
        try:
            canonical_snapshot = _browser_get(canonical)
            canonical_meta = _extract_metadata(
                canonical_snapshot.html, canonical_snapshot.final_url or canonical
            )
            if (
                canonical_snapshot.status_code < 400
                and _looks_specific_title(canonical_meta.get("name", ""))
                and _looks_product_image(canonical_meta.get("image_url", ""))
            ):
                canonical_meta.update(
                    asin=asin,
                    final_url=canonical,
                    source="amazon_browser_exact",
                )
                return canonical_meta
        except Exception:
            pass

    # Amazon share/mission pages sometimes hide the ASIN but still return
    # product-specific OpenGraph/JSON-LD metadata for the exact submitted link.
    # Accept that same-response identity only when BOTH the title and image are
    # concrete product signals. Generic Amazon shopping pages fail these gates.
    if (
        _is_short_link(clean)
        and _looks_specific_title(metadata.get("name", ""))
        and _looks_product_image(metadata.get("image_url", ""))
    ):
        metadata.update(
            asin=asin,
            final_url=snapshot.final_url or clean,
            source="amazon_share_metadata",
        )
        return metadata

    # For Amazon "Keep shopping for" mission pages, use only the first item in
    # Amazon's own Previously viewed section. This is the item that seeded the
    # mission and is materially different from choosing a recommendation.
    if _is_short_link(clean):
        mission = _extract_mission_primary_product(
            snapshot.html,
            snapshot.final_url or clean,
        )
        used_javascript = False

        # Amazon may return the mission shell to server-side HTTP clients while
        # populating "Previously viewed" only after JavaScript runs. Render the
        # same submitted URL only when the static response did not identify the
        # seeded product.
        if not mission:
            try:
                rendered = _javascript_get(clean)
                logger.info(
                    "Product URL Finder JS mission render status=%s diagnostics=%s",
                    rendered.status_code,
                    _mission_debug(
                        rendered.html,
                        rendered.final_url or snapshot.final_url or clean,
                    ),
                )
                mission = _extract_mission_primary_product(
                    rendered.html,
                    rendered.final_url or snapshot.final_url or clean,
                )
                used_javascript = bool(mission)
                if mission:
                    logger.info(
                        "Product URL Finder JS mission candidate asin=%s",
                        mission.get("asin"),
                    )
            except Exception as exc:
                logger.warning(
                    "Product URL Finder JS mission render failed error=%s:%s",
                    type(exc).__name__,
                    str(exc)[:300],
                )
                mission = None

        if mission:
            if used_javascript:
                mission["source"] = "amazon_mission_js_previously_viewed"

            # Try to enrich from the canonical /dp/ page, but keep the mission
            # card as the verified fallback if Amazon still serves a generic page.
            try:
                canonical_snapshot = _browser_get(mission["final_url"])
                canonical_meta = _extract_metadata(
                    canonical_snapshot.html,
                    canonical_snapshot.final_url or mission["final_url"],
                )
                if (
                    canonical_snapshot.status_code < 400
                    and _looks_specific_title(canonical_meta.get("name", ""))
                    and _looks_product_image(canonical_meta.get("image_url", ""))
                ):
                    canonical_meta.update(
                        asin=mission["asin"],
                        final_url=mission["final_url"],
                        source=(
                            "amazon_mission_js_canonical"
                            if used_javascript
                            else "amazon_mission_canonical"
                        ),
                    )
                    return canonical_meta
            except Exception:
                pass
            return mission

    fallback = _pin_factory_fallback(clean)
    if fallback:
        return fallback

    raise ValueError("The product link did not resolve to one verified product.")


def analyze_product_url_v2(url: str) -> dict:
    """Resolve identity, then feed the verified item through TrafficLift gates."""
    try:
        data = resolve_product_identity(url)
    except ValueError as exc:
        raise HTTPException(
            422,
            "This product link could not be resolved to one verified item.",
        ) from exc

    from backend.product_research import _build_url_candidate, _card_category
    from backend.product_scout import get_researcher
    from backend.product_control_agent import ProductControlAgent
    from backend.url_resolver import ResolvedProduct

    resolution = ResolvedProduct(
        original_url=url,
        final_url=data.get("final_url") or url,
        asin=data.get("asin"),
        title=data["name"],
        source=data.get("source") or "product_url_finder_v2",
    )
    card = _build_url_candidate(resolution)
    winner = get_researcher()._materialize(card, _card_category(card))
    winner.update(
        image_url=data["image_url"],
        image_source=data.get("final_url") or url,
        description=data.get("description", ""),
        source=data.get("source") or "product_url_finder_v2",
        url_resolution=resolution.to_dict(),
    )

    report = ProductControlAgent.evaluate(winner)
    if not report.ok:
        raise HTTPException(
            422,
            "The exact product was found, but its primary photo did not pass validation.",
        )

    result = report.product
    result.update(
        source=data.get("source") or "product_url_finder_v2",
        product_identity_verified=True,
        url_resolution=resolution.to_dict(),
        description=data.get("description", ""),
        research_status="not_requested",
        trend_signals=[],
        trend_score=0,
        margin_estimate="Not established",
        affiliate_candidate_url=url,
    )
    return result
