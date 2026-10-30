"""Bounded same-origin photo bytes for the public Pinterest canvas.

Only known retail image CDNs are permitted; no general-purpose URL proxy.
This endpoint never generates a winner, a video, or paid assets.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from io import BytesIO
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
def pin_image(url: str, request: Request):
    """Return actual retailer photo bytes for a 1000x1500 canvas export."""
    global _CACHE_BYTES
    if not _allowed(url):
        raise HTTPException(422, "This product photo host is not supported for safe pin export.")
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(url)
        if cached and cached[0] > now:
            _CACHE.move_to_end(url)
            return Response(content=cached[1], media_type=cached[2],
                            headers={"Cache-Control": "public, max-age=3600"})
    _rate_limit(request.client.host if request.client else "unknown", now)
    try:
        _validate_public_https_url(url)
        with tempfile.TemporaryDirectory(prefix="trafficlift-pin-") as directory:
            photo = _download_product_image(url, Path(directory))
            body = photo.read_bytes()
            media_type = {".jpg": "image/jpeg", ".png": "image/png",
                          ".webp": "image/webp"}.get(photo.suffix)
        if not media_type or len(body) < 1024 or len(body) > 12 * 1024 * 1024:
            raise ValueError("Product photo is empty, invalid, or too large.")
    except (ValueError, OSError, TimeoutError) as exc:
        raise HTTPException(502, "The exact product photo could not be retrieved. Try generating the pin again.") from exc
    with _LOCK:
        while _CACHE and _CACHE_BYTES + len(body) > _MAX_CACHE_BYTES:
            _, (_, old, _) = _CACHE.popitem(last=False)
            _CACHE_BYTES -= len(old)
        _CACHE[url] = (now + 3600, body, media_type)
        _CACHE_BYTES += len(body)
    return Response(content=body, media_type=media_type,
                    headers={"Cache-Control": "public, max-age=3600"})
