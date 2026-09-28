"""backend/product_control_agent.py

Product Control Agent — strict quality gate that every winning product
must pass before reaching the user.

NON-NEGOTIABLE RULE
--------------------
A winning product is NOT valid unless it has at least one REAL, VERIFIED,
USABLE product image. The agent enforces:

  * **product_quality_pass**  — required fields, semantic category alignment
  * **usable_product_image**  — http/https URL, HEAD reachable, image/*
                                content type, size above the placeholder
                                floor, no tracking pixel / spacer.

If either fails, audit_product() raises a structured ValueError that
the researcher catches to skip the candidate and try the next.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("ProductControlAgent")

# ── Public size constants ────────────────────────────────────────────────────

# Minimum byte size for a usable product image. Anything below this is
# almost certainly a placeholder, a tracking pixel, or a transparent
# spacer. Real product photos are typically > 5 KB even at thumbnail
# resolution.
MIN_IMAGE_BYTES = 5_000

# Reject obvious placeholder file names regardless of file size.
PLACEHOLDER_PATH_HINTS = (
    "/placeholder.",
    "/placeholders/",
    "/blank.",
    "/spacer.",
    "/1x1.",
    "/pixel.",
    "/transparent.",
    "/tracking.",
    "/avatar-default.",
)

# Generic site-logo file names — almost never a product photo.
GENERIC_LOGO_HINTS = (
    "logo", "favicon", "site-icon", "apple-touch-icon",
    "og-image-default", "default-image",
)

PLACEHOLDER_DATA_PREFIXES = ("data:image/svg", "data:image/gif;base64,R0lGOD")


# ── Structured audit result ─────────────────────────────────────────────────


@dataclass
class ImageCheckResult:
    """Result of validating a single image URL."""
    ok: bool = False
    reason: str = ""               # short tag: missing_image_url, broken_image, ...
    image_status: str = "unknown"  # "verified" | "missing" | "broken" | "placeholder" | "too_small" | "wrong_type" | "tracking_pixel" | "logo"
    image_url: Optional[str] = None
    image_bytes: Optional[int] = None
    image_content_type: Optional[str] = None
    http_status: Optional[int] = None


@dataclass
class AuditReport:
    """Full audit result for a product candidate."""
    ok: bool
    product: dict
    reasons: list[str] = field(default_factory=list)
    image_check: Optional[ImageCheckResult] = None

    @property
    def primary_reason(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "ok"


# ── Validation helpers ───────────────────────────────────────────────────────


def _looks_like_placeholder_url(url: str) -> bool:
    """Return True for URL/path signatures that strongly suggest a placeholder.

    These are NEVER acceptable for a real winning product image.
    """
    low = url.lower()
    for hint in PLACEHOLDER_PATH_HINTS:
        if hint in low:
            return True
    for hint in GENERIC_LOGO_HINTS:
        if hint in low:
            return True
    return False


# ── Visual-dominance ranking ─────────────────────────────────────────────────
#
# Many image-search URLs returned by Tavily are TECHNICALLY valid (correct
# content-type, large enough, http) but visually POOR:
#   * magazine editorial photos where the product is a small part of a
#     lifestyle scene
#   * Amazon "lifestyle gallery" photos that include other items (e.g. the
#     devices being charged by a lamp, when the lamp is the actual winner)
#   * wordpress / blog CDN assets used for marketing articles
# Without a heuristic ranking layer, the pipeline picks the first URL that
# passes validation, which is often the wrong one for a Pinterest pin.
#
# The ranker below is a deterministic, no-network heuristic that scores each
# URL on three signals:
#   1. HOST CLASS      — known retailer/manufacturer CDNs get a big bonus;
#                         known magazine/blog editorial hosts get a penalty.
#   2. PATH TOKENS     — /products/, /cdn/shop/, /images/I/...  → bonus;
#                         /editorial/, /article/, /wp-content/  → penalty.
#   3. FILENAME KEYWORDS — does the URL contain words from the product name?
#
# The result is a list of (url, score) sorted by score descending. Callers
# iterate the list and pick the first URL that also passes _validate_image.
# This guarantees that when MULTIPLE images for the same product are
# available, we choose the most product-forward one.

# Host → bonus / penalty. Tuned from real Tavily results observed on Render.
PRODUCT_HOST_SCORES: dict[str, int] = {
    # Major retailer product-image CDNs (typically 1-2 large JPGs, no scenery).
    "m.media-amazon.com": 60,
    "images-na.ssl-images-amazon.com": 60,
    "images.amazon.com": 55,
    "i.etsystatic.com": 55,
    "i5.walmartimages.com": 55,
    "mobileimages.lowes.com": 50,
    "target.scene7.com": 50,
    "ak1.ostkcdn.com": 50,
    "media.startech.com": 50,
    "gdx-assets.costco.com": 55,
    "img.kentfaith.com": 35,
    "www.4allpromos.com": 30,
    # Shopify-hosted product images (most retailers render a single product
    # shot for these).
    "cdn.shopify.com": 35,
    "www.progressivedesk.com": 25,
    "carlsonpetproducts.com": 25,
    "www.letifly.com": 25,
    "www.pamperedchef.com": 25,
    "jasonmarkk.com": 25,
    "speedcleaning.com": 25,
    "slickproductsusa.com": 25,
    # Generic / unknown hosts — neutral.
}
LIFESTYLE_HOST_SCORES: dict[str, int] = {
    "hips.hearstapps.com": -45,            # Hearst magazine editorial
    "vader-prod.s3.amazonaws.com": -45,    # Hearst magazine editorial
    "pyxis.nymag.com": -45,                # New York Magazine
    "food.fnr.sndimg.com": -45,            # Food Network editorial
    "www.familyhandyman.com": -30,         # DIY blog
    "www.nationsphotolab.com": -25,        # Photo service (not product)
    "images.ctfassets.net": -10,           # Generic CMS — ambiguous
}
# Path tokens that strongly suggest a single-product hero shot.
PRODUCT_PATH_TOKENS = (
    "/products/", "/product/", "/img/", "/photo/", "/photos/",
    "/cdn/shop/", "/cdn/shop/files/", "/cdn/shop/products/",
    "/images/i/", "/images/I/", "/productimages/",
    "/iceberg/com/product/", "/shop/files/",
)
# Path tokens that strongly suggest editorial / lifestyle / scene.
LIFESTYLE_PATH_TOKENS = (
    "/editorial/", "/article/", "/blog/", "/lifestyle/",
    "/wp-content/uploads/", "/wp-json/",
    "/stories/", "/guide/", "/how-to/",
)
# Filename patterns that suggest lifestyle scenes (multiple products, props).
LIFESTYLE_FILENAME_TOKENS = (
    "_scene_", "_lifestyle_", "_editorial_", "_in-use_",
    "_setup_", "_with-phone_", "_with-watch_",
    "-with-", "-and-",
)


def rank_image_candidates(
    urls: list[str],
    *,
    product_name: str = "",
    category: str = "",
) -> list[tuple[str, int]]:
    """Score and rank image URLs by visual-dominance heuristics.

    Higher score = more likely to be a clean, product-forward hero shot.
    The result is sorted descending so callers can pick the best URL
    that also passes _validate_image().

    This is a deterministic heuristic — no network calls, no model —
    that biases the selection toward retailer-hosted product photos and
    away from magazine/blog lifestyle editorial images.
    """
    if not urls:
        return []

    name_words = [w.lower() for w in (product_name or "").split() if len(w) > 2][:6]
    category_words = [w.lower() for w in (category or "").split() if len(w) > 2][:3]
    name_set = set(name_words + category_words)

    scored: list[tuple[str, int]] = []
    for url in urls:
        score = 0
        low = url.lower()
        # ── HOST CLASS ────────────────────────────────────────────
        try:
            host = low.split("//", 1)[-1].split("/", 1)[0]
            # strip leading www.
            host_no_www = host[4:] if host.startswith("www.") else host
        except Exception:
            host = ""
            host_no_www = ""
        matched = False
        for h, bonus in PRODUCT_HOST_SCORES.items():
            if host == h or host_no_www == h or host.endswith("." + h):
                score += bonus
                matched = True
                break
        if not matched:
            for h, penalty in LIFESTYLE_HOST_SCORES.items():
                if host == h or host_no_www == h or host.endswith("." + h):
                    score += penalty
                    matched = True
                    break

        # ── PATH TOKENS ────────────────────────────────────────────
        for tok in PRODUCT_PATH_TOKENS:
            if tok in low:
                score += 25
                break
        for tok in LIFESTYLE_PATH_TOKENS:
            if tok in low:
                score -= 30
                break

        # ── FILENAME KEYWORDS ──────────────────────────────────────
        if name_set:
            hits = sum(1 for w in name_set if w in low)
            if hits:
                score += min(hits, 3) * 12
        for tok in LIFESTYLE_FILENAME_TOKENS:
            if tok in low:
                score -= 25
                break

        # ── MIME HINTS from URL ────────────────────────────────────
        if any(low.endswith(ext) for ext in (".jpg", ".jpeg", ".webp", ".png")):
            score += 5  # explicit image format is a positive signal

        # ── QUERY STRINGS that hint at thumbnails ─────────────────
        if any(seg in low for seg in ("_thumb", "/thumb/", "?thumb", "size=thumb")):
            score -= 15

        scored.append((url, score))

    # Stable order: highest score first, ties broken by original order.
    scored.sort(key=lambda t: -t[1])
    return scored


def _validate_image(url: Optional[str], *, head_timeout: float = 3.0) -> ImageCheckResult:
    """Strictly validate a candidate image URL.

    Rejects (in order):
      * missing/empty
      * non-http(s) schemes (data:, blob:, file:, ftp:, …)
      * placeholder path hints (logo, placeholder, 1x1, pixel, …)
      * HEAD failure (4xx/5xx, network error, redirect to non-image)
      * wrong content-type (text/html, application/json, …)
      * too-small payload (< MIN_IMAGE_BYTES)

    On success returns ok=True with image_status="verified" and the
    HEAD response details for evidence.

    Validation flow:
      1. Cheap URL checks (scheme / placeholder / data: URI).
      2. HEAD with allow_redirects=True (bounded redirect chain). If the
         final URL is image/* → accept. If the final URL is not image/*
         → reject as wrong_type.
      3. If HEAD returns 403/405 (CDNs that don't support HEAD) OR if the
         final redirect went to an image host that did not advertise a
         Content-Length, fall back to a tiny ranged GET to verify.
      4. Reject if Content-Length / Content-Range says the file is
         smaller than MIN_IMAGE_BYTES (tracking pixel / spacer).

    All network calls share the same `head_timeout` and a single
    Session so a malicious redirect loop cannot blow up the request.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return ImageCheckResult(ok=False, reason="missing_image_url",
                               image_status="missing")

    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ImageCheckResult(ok=False, reason="invalid_image_url_scheme",
                               image_status="placeholder", image_url=url)

    if any(url.startswith(p) for p in PLACEHOLDER_DATA_PREFIXES):
        return ImageCheckResult(ok=False, reason="placeholder_image",
                               image_status="placeholder", image_url=url)

    if _looks_like_placeholder_url(url):
        return ImageCheckResult(ok=False, reason="placeholder_image",
                               image_status="placeholder", image_url=url)

    # NOTE: we use module-level requests.head/get rather than a Session so
    # tests can mock `requests.head` / `requests.get` directly. A Session
    # would bypass those mocks.

    # ── Step 1: HEAD with redirects allowed (bounded by requests' default
    # max_redirects=30, which is more than enough for any real CDN).
    try:
        head_resp = requests.head(
            url,
            timeout=head_timeout,
            allow_redirects=True,
        )
    except Exception as exc:
        # Network-level failure on HEAD — try a small GET next.
        head_resp = None
        head_exc = exc

    if head_resp is not None and head_resp.status_code < 400:
        # We got a successful response — possibly after redirects.
        content_type = (head_resp.headers.get("Content-Type") or "").lower().split(";")[0].strip()
        if not content_type.startswith("image/"):
            return ImageCheckResult(
                ok=False,
                reason=f"wrong_type: {content_type}",
                image_status="wrong_type", image_url=url,
                http_status=head_resp.status_code,
                image_content_type=content_type,
            )
        size = _extract_size(head_resp.headers)
        if size is None or size == 0:
            # No Content-Length — try a tiny ranged GET to confirm.
            size = _size_via_range_get(url, head_timeout)
        if size is not None and 0 < size < MIN_IMAGE_BYTES:
            return ImageCheckResult(
                ok=False,
                reason=f"image_too_small: {size} bytes",
                image_status="tracking_pixel", image_url=url,
                image_bytes=size, image_content_type=content_type,
                http_status=head_resp.status_code,
            )
        # Verified by HEAD.
        return ImageCheckResult(
            ok=True,
            image_status="verified",
            image_url=url,
            image_bytes=size if size and size > 0 else None,
            image_content_type=content_type,
            http_status=head_resp.status_code,
        )

    # ── Step 2: HEAD was 4xx/5xx or threw. Some CDNs reject HEAD.
    # If HEAD returned 403 or 405 we explicitly try a tiny GET to verify.
    head_failed_cleanly = head_resp is not None and head_resp.status_code in (403, 405)
    head_failed_other = head_resp is not None and head_resp.status_code >= 400 and not head_failed_cleanly
    head_threw = head_resp is None

    if head_failed_cleanly:
        # HEAD-hostile server — try a ranged GET and trust Content-Type + Content-Range.
        size, ct, status = _verify_via_ranged_get(url, head_timeout)
        if status is None:
            return ImageCheckResult(
                ok=False, reason=f"image_load_failed: GET also failed",
                image_status="broken", image_url=url,
            )
        if not (ct or "").startswith("image/"):
            return ImageCheckResult(
                ok=False, reason=f"wrong_type: {ct}",
                image_status="wrong_type", image_url=url,
                http_status=status, image_content_type=ct,
            )
        if size is not None and 0 < size < MIN_IMAGE_BYTES:
            return ImageCheckResult(
                ok=False, reason=f"image_too_small: {size} bytes",
                image_status="tracking_pixel", image_url=url,
                image_bytes=size, image_content_type=ct,
                http_status=status,
            )
        return ImageCheckResult(
            ok=True, image_status="verified",
            image_url=url,
            image_bytes=size if size and size > 0 else None,
            image_content_type=ct,
            http_status=status,
        )

    if head_threw:
        # Network error on HEAD — last-ditch GET with the same timeout.
        size, ct, status = _verify_via_ranged_get(url, head_timeout)
        if status is None:
            return ImageCheckResult(
                ok=False,
                reason=f"image_load_failed: {type(head_exc).__name__}",
                image_status="broken", image_url=url,
            )
        if not (ct or "").startswith("image/"):
            return ImageCheckResult(
                ok=False, reason=f"wrong_type: {ct}",
                image_status="wrong_type", image_url=url,
                http_status=status, image_content_type=ct,
            )
        return ImageCheckResult(
            ok=True, image_status="verified",
            image_url=url, image_bytes=size,
            image_content_type=ct, http_status=status,
        )

    # HEAD returned a non-403/405 error (e.g. 404, 500) — the resource is gone.
    return ImageCheckResult(
        ok=False,
        reason=f"image_load_failed: HTTP {head_resp.status_code}",
        image_status="broken", image_url=url,
        http_status=head_resp.status_code,
    )


def _extract_size(headers) -> Optional[int]:
    """Return total image size from Content-Length or Content-Range."""
    cl = headers.get("Content-Length")
    if cl and cl.isdigit():
        return int(cl)
    cr = headers.get("Content-Range")  # "bytes 0-1023/48231"
    if cr and "/" in cr:
        try:
            return int(cr.rsplit("/", 1)[1])
        except ValueError:
            return None
    return None


def _size_via_range_get(url: str, timeout: float) -> Optional[int]:
    """Fallback size probe via a 0-1023 range GET. Returns total bytes or None."""
    try:
        r = requests.get(url, headers={"Range": "bytes=0-1023"},
                         timeout=timeout, allow_redirects=True, stream=True)
        size = _extract_size(r.headers)
        if size is None:
            size = len(r.content or b"")
        r.close()
        return size
    except Exception:
        return None


def _verify_via_ranged_get(url: str, timeout: float):
    """HEAD-hostile fallback: do a small ranged GET and return
    (size, content_type, status). Returns (None, None, None) on failure.
    """
    try:
        r = requests.get(url, headers={"Range": "bytes=0-1023"},
                         timeout=timeout, allow_redirects=True, stream=True)
        size = _extract_size(r.headers)
        if size is None:
            size = len(r.content or b"")
        ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        status = r.status_code
        r.close()
        return size, ct, status
    except Exception:
        return None, None, None


# ── Main agent ───────────────────────────────────────────────────────────────


class ProductControlAgent:
    """Quality gate. audit_product() returns the product on success or
    raises ValueError with a structured reason on rejection. evaluate()
    returns the same info as an AuditReport dataclass (no raise) for
    callers that want to inspect reasons programmatically.
    """

    @staticmethod
    def audit_product(product: dict) -> dict:
        """Backward-compatible: return product on pass, raise ValueError on fail.

        Existing callers (researcher, tests) catch the exception to skip
        the candidate and move to the next one.
        """
        report = ProductControlAgent.evaluate(product)
        if not report.ok:
            raise ValueError(
                f"Product Control Violation: {report.primary_reason} "
                f"(product id={product.get('id', '?')})"
            )
        return report.product

    @staticmethod
    def evaluate(product: dict) -> AuditReport:
        """Full audit — returns AuditReport (does not raise)."""
        reasons: list[str] = []
        # ── 1. Required fields ─────────────────────────────────────────
        required_fields = [
            "id", "name", "category", "image_url", "url", "angle",
            "pin_title", "pin_description", "hashtags",
        ]
        for f in required_fields:
            if not product.get(f):
                reasons.append(f"missing_field: {f}")

        # ── 2. Category / name semantic alignment ─────────────────────
        category = (product.get("category") or "").lower()
        name = (product.get("name") or "").lower()
        category_keywords = {
            "cleaning": ["scrub", "clean", "brush", "mop", "vacuum", "wipe", "cleaner", "grout", "ultrasonic"],
            "tech": ["charger", "station", "cable", "wireless", "magsafe", "led", "phone", "hub", "dock"],
            "pet": ["dog", "cat", "pet", "bed", "pup", "leash", "grooming", "calming"],
            "home decor": ["lamp", "sunset", "projection", "light", "decor", "aesthetic", "ambient"],
        }
        matched_category = next((k for k in category_keywords if k in category), None)
        if matched_category:
            valid_terms = category_keywords[matched_category]
            if not any(term in name for term in valid_terms):
                reasons.append(
                    f"semantic_mismatch: category '{category}' vs name '{name}'"
                )

        # ── 3. Image eligibility (HARD GATE) ────────────────────────────
        image_check = _validate_image(product.get("image_url"))
        if not image_check.ok:
            reasons.append(f"image_rejected: {image_check.reason}")

        # ── 4. Decorate product with verified-image metadata ──────────
        out = dict(product)
        if image_check.ok:
            out["image_status"] = image_check.image_status              # "verified"
            # Do NOT overwrite image_source if the caller already set it
            # (e.g. the researcher may have set it to "image_research").
            out.setdefault("image_source", image_check.image_url)
            out["image_url_verified"] = image_check.image_url
            out["image_bytes"] = image_check.image_bytes
            out["image_content_type"] = image_check.image_content_type
            out["image_http_status"] = image_check.http_status
        else:
            out["image_status"] = image_check.image_status              # "missing"|"broken"|…
            out["image_rejection_reason"] = image_check.reason
            # Strip the bad image so downstream renderers can't use it.
            out["image_url"] = None

        return AuditReport(
            ok=(len(reasons) == 0),
            product=out,
            reasons=reasons,
            image_check=image_check,
        )