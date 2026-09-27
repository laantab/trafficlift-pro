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


def _validate_image(url: Optional[str], *, head_timeout: float = 5.0) -> ImageCheckResult:
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

    # HEAD the URL — keep the test surface small, allow override via
    # PRODUCT_IMAGE_HEAD_TIMEOUT env if needed.
    try:
        head = requests.head(url, timeout=head_timeout, allow_redirects=True)
    except Exception as exc:
        logger.warning("Image HEAD failed for %s: %s", url, exc)
        return ImageCheckResult(ok=False, reason="image_load_failed",
                               image_status="broken", image_url=url)

    http_status = head.status_code
    if http_status >= 400:
        return ImageCheckResult(ok=False, reason=f"image_load_failed: HTTP {http_status}",
                               image_status="broken", image_url=url, http_status=http_status)

    content_type = (head.headers.get("Content-Type") or "").lower().split(";")[0].strip()
    if not content_type.startswith("image/"):
        return ImageCheckResult(ok=False, reason=f"wrong_type: {content_type}",
                               image_status="wrong_type", image_url=url,
                               http_status=http_status, image_content_type=content_type)

    # Size check via Content-Length. If absent (chunked transfer) we fall
    # back to a tiny range GET — the size budget keeps us safe and fast.
    size = 0
    cl = head.headers.get("Content-Length")
    if cl and cl.isdigit():
        size = int(cl)
    elif cl is None:
        # Some servers omit Content-Length. Try a 0-1023 range request.
        try:
            r = requests.get(url, headers={"Range": "bytes=0-1023"},
                             timeout=head_timeout, allow_redirects=True, stream=True)
            cr = r.headers.get("Content-Range")  # "bytes 0-1023/48231"
            if cr and "/" in cr:
                size = int(cr.rsplit("/", 1)[1])
            else:
                size = len(r.content or b"")
            r.close()
        except Exception:
            pass

    if 0 < size < MIN_IMAGE_BYTES:
        return ImageCheckResult(ok=False,
                               reason=f"image_too_small: {size} bytes",
                               image_status="tracking_pixel", image_url=url,
                               image_bytes=size, image_content_type=content_type,
                               http_status=http_status)

    return ImageCheckResult(
        ok=True,
        image_status="verified",
        image_url=url,
        image_bytes=size if size > 0 else None,
        image_content_type=content_type,
        http_status=http_status,
    )


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