"""Bounded same-origin photo bytes for the public Pinterest canvas.

Only known retail image CDNs are permitted; no general-purpose URL proxy.
This endpoint never generates a winner, a video, or paid assets.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from io import BytesIO
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError
import re
from urllib.parse import parse_qsl, urlencode, urlunsplit
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from local_video.trafficlift_video_bridge import _download_product_image, _validate_public_https_url

router = APIRouter()
_LOCK = threading.Lock()
_CACHE: OrderedDict[str, tuple[float, bytes, str]] = OrderedDict()
_CALLS: dict[str, deque[float]] = defaultdict(deque)
_CACHE_BYTES = 0
_MAX_CACHE_BYTES = 48 * 1024 * 1024
_ALLOWED_HOSTS = {
    "cdn.shopify.com", "m.media-amazon.com", "images-na.ssl-images-amazon.com",
    "i.etsystatic.com", "i.ebayimg.com", "i5.walmartimages.com",
    "images.unsplash.com", "static.wixstatic.com", "cdn11.bigcommerce.com",
    "images.thdstatic.com", "target.scene7.com", "media.istockphoto.com",
    "img.ltwebstatic.com", "cdn.shopifycdn.net",
}
_ALLOWED_SUFFIXES = (".cdn.shopify.com", ".etsystatic.com", ".media-amazon.com",
                     ".walmartimages.com", ".bigcommerce.com")


def _allowed(url: str) -> bool:
    if not url or len(url) > 2048:
        return False
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    return (parsed.scheme == "https" and parsed.port in (None, 443)
            and not parsed.username and not parsed.password
            and (host in _ALLOWED_HOSTS or any(host.endswith(s) for s in _ALLOWED_SUFFIXES)))



def _larger_same_photo_urls(url: str) -> list[str]:
    """Only modify sizing directives; never swap a seller image identifier."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    options = []
    if host in {"m.media-amazon.com", "images-na.ssl-images-amazon.com"}:
        # Amazon: same /images/I/<photo ID>, replace only a known size directive.
        path = re.sub(r"\\._(?:AC_)?(?:SL|SX|SY|UL|UX|UY|US|SR|SS)[A-Za-z0-9_,.]*_(?=\\.)",
                      "._AC_SL1500_", parsed.path, flags=re.I)
        if path != parsed.path:
            options.append(urlunsplit(parsed._replace(path=path)))
            # Some amazon sizing modes are better removed entirely.
            raw = re.sub(r"\\._(?:AC_)?(?:SL|SX|SY|UL|UX|UY|US|SR|SS)[A-Za-z0-9_,.]*_(?=\\.)",
                         "", parsed.path, flags=re.I)
            options.append(urlunsplit(parsed._replace(path=raw)))
    elif host == "cdn.shopify.com" or host.endswith(".cdn.shopify.com"):
        # Shopify's size query changes resolution of the *same* image.
        params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                  if k.lower() not in {"width", "height"}]
        params.append(("width", "1600"))
        options.append(urlunsplit(parsed._replace(query=urlencode(params))))
    elif host == "i5.walmartimages.com":
        params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                  if k.lower() not in {"odnheight", "odnwidth"}]
        params.extend([("odnHeight", "1500"), ("odnWidth", "1500")])
        options.append(urlunsplit(parsed._replace(query=urlencode(params))))
    return [candidate for candidate in options if candidate != url and _allowed(candidate)][:2]


def _sharp_enough(data: bytes) -> bool:
    try:
        with Image.open(BytesIO(data)) as image:
            w, h = image.size
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                return False
            # Identical geometry to the browser canvas: 924 x 1004 max
            # hero area, with no more than 1.35x photo enlargement.
            return w >= 1 and h >= 1 and min(924 / w, 1004 / h) <= 1.35
    except (UnidentifiedImageError, OSError, ValueError):
        return False


def _enhance_verified_photo(data: bytes) -> bytes | None:
    """Conservatively improve the SAME photo; never invent product details.

    Tiny or featureless source images must still fail; upscaling alone cannot
    reconstruct authentic product details.
    """
    try:
        with Image.open(BytesIO(data)) as original:
            if original.format not in {"JPEG", "PNG", "WEBP"}:
                return None
            w, h = original.size
            if w * h > 24_000_000 or min(w, h) < 480:
                return None
            image = ImageOps.exif_transpose(original).convert("RGB")
        sample = image.copy()
        sample.thumbnail((384, 384), Image.Resampling.LANCZOS)
        if ImageStat.Stat(ImageOps.grayscale(sample)).stddev[0] < 5:
            return None
        factor = max(1.0, 924 / (1.35 * image.width), 1004 / (1.35 * image.height))
        if factor > 2:
            return None
        if factor > 1:
            image = image.resize(
                (int(image.width * factor + .5), int(image.height * factor + .5)),
                Image.Resampling.LANCZOS,
            )
        image = ImageEnhance.Contrast(image).enhance(1.04)
        image = image.filter(ImageFilter.UnsharpMask(radius=1.1, percent=115, threshold=3))
        output = BytesIO()
        image.save(output, format="JPEG", quality=93, optimize=True)
        result = output.getvalue()
        return result if len(result) <= 12 * 1024 * 1024 and _sharp_enough(result) else None
    except (UnidentifiedImageError, OSError, ValueError, OverflowError):
        return None


def _rate_limit(client: str, now: float) -> None:
    with _LOCK:
        calls = _CALLS[client]
        while calls and calls[0] < now - 60:
            calls.popleft()
        if len(calls) >= 15:
            raise HTTPException(429, "Too many product-photo requests. Wait a minute and retry the pin.")
        calls.append(now)
        if len(_CALLS) > 5000:
            for key in list(_CALLS):
                if not _CALLS[key] or _CALLS[key][-1] < now - 60:
                    del _CALLS[key]


@router.get("/api/v1/pin-image")
def pin_image(url: str, request: Request, quality: str = "standard"):
    """Return actual retailer photo bytes for a 1000x1500 canvas export."""
    global _CACHE_BYTES
    if not _allowed(url):
        raise HTTPException(422, "This product photo host is not supported for safe pin export.")
    if quality not in {"standard", "high"}:
        raise HTTPException(422, "Unknown product photo quality mode.")
    now = time.monotonic()
    cache_key = quality + ":" + url
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached[0] > now:
            _CACHE.move_to_end(cache_key)
            return Response(content=cached[1], media_type=cached[2],
                            headers={"Cache-Control": "public, max-age=3600"})
    _rate_limit(request.client.host if request.client else "unknown", now)
    body = None
    media_type = None
    best_low_resolution = None
    best_source_pixels = 0
    try:
        with tempfile.TemporaryDirectory(prefix="trafficlift-pin-") as directory:
            candidates = ([*_larger_same_photo_urls(url), url] if quality == "high" else [url])
            for n, candidate in enumerate(candidates):
                try:
                    _validate_public_https_url(candidate)
                    target_dir = Path(directory) / str(n)
                    target_dir.mkdir()
                    photo = _download_product_image(candidate, target_dir)
                    data = photo.read_bytes()
                    kind = {".jpg": "image/jpeg", ".png": "image/png",
                            ".webp": "image/webp"}.get(photo.suffix)
                    if not kind or not (1024 <= len(data) <= 12 * 1024 * 1024):
                        continue
                    if quality == "high" and not _sharp_enough(data):
                        # Every candidate preserves the seller image identifier.
                        # Retry the best actual photo only after larger URLs fail.
                        try:
                            with Image.open(BytesIO(data)) as original:
                                area = original.width * original.height
                                if area > best_source_pixels:
                                    best_source_pixels = area
                                    best_low_resolution = data
                        except (UnidentifiedImageError, OSError, ValueError):
                            pass
                        continue
                    body, media_type = data, kind
                    break
                except (ValueError, OSError, TimeoutError):
                    continue
    except OSError as exc:
        raise HTTPException(502, "The exact product photo could not be retrieved.") from exc
    if body is None and quality == "high" and best_low_resolution is not None:
        enhanced = _enhance_verified_photo(best_low_resolution)
        if enhanced is not None:
            body, media_type = enhanced, "image/jpeg"
    if body is None:
        if quality == "high":
            raise HTTPException(422, "The exact product photo still does not meet pin quality requirements after checking larger seller images and conservative enhancement. Supply a clearer photo of this same product.")
        raise HTTPException(502, "The exact product photo could not be retrieved. Try generating the pin again.")
    with _LOCK:
        while _CACHE and _CACHE_BYTES + len(body) > _MAX_CACHE_BYTES:
            _, (_, old, _) = _CACHE.popitem(last=False)
            _CACHE_BYTES -= len(old)
        _CACHE[cache_key] = (now + 3600, body, media_type)
        _CACHE_BYTES += len(body)
    return Response(content=body, media_type=media_type,
                    headers={"Cache-Control": "public, max-age=3600"})
