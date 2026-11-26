"""Owner-only access and anonymous usage limits for the hosted site.

On the owner's own PC (no hosted flags) nothing here restricts anything, so
local use keeps working exactly as before. On the public Render site:

* ``require_owner`` protects private data and paid commands. It accepts the
  same signed owner session cookie (or owner token header) as the Video Studio.
* ``limit_research`` caps how often anonymous visitors can run live product
  research, which spends the owner's research credits. The owner is exempt.
"""
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_LOCK = threading.Lock()
_PER_CLIENT: dict[str, deque] = defaultdict(deque)
_ALL_ANONYMOUS: deque = deque()

PER_MINUTE = 12
DAY = 86400


def hosted() -> bool:
    """True on the public website, false on the owner's own computer."""
    return bool(os.getenv("RENDER_SERVICE_ID")) or os.getenv("TRAFFICLIFT_HOSTED_VIDEO") == "1"


def is_owner(request: Request) -> bool:
    from backend.photo_video_api import valid_video_session
    configured = os.getenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN", "")
    if len(configured) < 32:
        return False
    supplied = request.headers.get("x-trafficlift-video-token", "")
    if supplied and secrets.compare_digest(supplied, configured):
        return True
    return valid_video_session(request.cookies.get("trafficlift_video_session", ""), configured)


def require_owner(request: Request) -> None:
    """Private data and paid commands: owner only on the public site."""
    if not hosted():
        return
    if len(os.getenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN", "")) < 32:
        # Fail closed: never fall back to public access when the code is missing.
        raise HTTPException(503, "Owner access is not configured.")
    if not is_owner(request):
        raise HTTPException(401, "This is private. Unlock the owner area first.",
                            headers={"Cache-Control": "private, no-store"})


def _daily_cap() -> int:
    try:
        return max(1, int(os.getenv("TRAFFICLIFT_ANON_RESEARCH_PER_DAY", "300")))
    except ValueError:
        return 300


def _client(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def limit_research(request: Request) -> None:
    """Cap anonymous live research so strangers cannot drain research credits.

    The per-visitor limit slows one visitor down. The site-wide daily cap is the
    real protection: a forged address cannot get around it.
    """
    if not hosted() or is_owner(request):
        return
    now = time.monotonic()
    client = _client(request)
    with _LOCK:
        while _ALL_ANONYMOUS and _ALL_ANONYMOUS[0] < now - DAY:
            _ALL_ANONYMOUS.popleft()
        calls = _PER_CLIENT[client]
        while calls and calls[0] < now - 60:
            calls.popleft()
        if len(calls) >= PER_MINUTE:
            raise HTTPException(429, "Too many product searches. Wait a minute and try again.",
                                headers={"Retry-After": "60"})
        if len(_ALL_ANONYMOUS) >= _daily_cap():
            raise HTTPException(429, "Product search has reached today's visitor limit. Try again tomorrow.",
                                headers={"Retry-After": "3600"})
        calls.append(now)
        _ALL_ANONYMOUS.append(now)
        if len(_PER_CLIENT) > 5000:
            for key in list(_PER_CLIENT):
                if not _PER_CLIENT[key] or _PER_CLIENT[key][-1] < now - 60:
                    del _PER_CLIENT[key]


def allowed_origins() -> list[str]:
    """Websites allowed to call this app from a browser: only its own addresses."""
    origins = {"https://trafficlift-pro.onrender.com"}
    for value in (os.getenv("RENDER_EXTERNAL_URL", ""), *os.getenv("TRAFFICLIFT_ALLOWED_ORIGINS", "").split(",")):
        value = value.strip().rstrip("/")
        if value.startswith(("https://", "http://")):
            origins.add(value)
    return sorted(origins)


# The owner's own computer, on any port (local launcher and local video bridge).
LOCAL_ORIGIN_REGEX = r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
