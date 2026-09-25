"""
trafficlift_pro/backend/quality_control.py
ProductControlAgent — Autonomous quality-control for product payloads.

Three-layer audit on every product:

  1. Schema completeness — fail loudly if any required field is missing.
  2. Semantic consistency — warn on category/name mismatches (e.g. pet photo
     labelled as "Tech & Gadgets").
  3. Asset integrity — HEAD-probe the image URL to catch dead/redirected
     CDN links before they're served to a real user.

Public API:
    audit_product(product: dict) -> dict
        Audit a single product. Raises ValueError on schema failures,
        logs warnings for semantic/asset issues. Returns the product
        on success so callers can chain.

    audit_catalog(catalog: list[dict]) -> list[dict]
        Audit a full list. Stops at the first schema failure (so a
        single bad product can't silently mask a broken catalog).

The agent never modifies payloads — it only validates and reports.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

import requests

logger = logging.getLogger("ProductControlAgent")

REQUIRED_FIELDS = (
    "id",
    "name",
    "category",
    "image_url",
    "url",
    "pin_title",
    "pin_description",
    "hashtags",
)

# Keyword sanity mapping — substring match against the lowercased product
# category and name. Used to HARD-FAIL cross-category mismatches like a
# pet photo accidentally filed under Tech & Gadgets.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "cleaning":   ["scrub", "clean", "brush", "mop", "vacuum", "wipe",
                   "cleaner", "grout"],
    "tech":       ["charger", "station", "cable", "wireless", "magsafe",
                   "led", "phone", "hub"],
    "pet":        ["dog", "cat", "pet", "bed", "pup", "leash", "grooming"],
    "home decor": ["lamp", "sunset", "projection", "light", "decor",
                   "aesthetic"],
}


class ProductControlAgent:
    """Quality-control agent for product payloads."""

    @staticmethod
    def _semantic_check(category: str, name: str) -> tuple[bool, str | None]:
        """
        Returns (matched, matched_key). `matched=False` with no key means
        we couldn't map the category to any group. `matched=False` with
        a key means the category was recognized but no keyword from that
        group appears in the product name — this is a HARD FAIL in the
        caller, not a warning.
        """
        cat = category.lower()
        matched_key = next(
            (k for k in CATEGORY_KEYWORDS if k in cat),
            None,
        )
        if not matched_key:
            return False, None

        valid_terms = CATEGORY_KEYWORDS[matched_key]
        nme = name.lower()
        if any(term in nme for term in valid_terms):
            return True, matched_key
        return False, matched_key

    @staticmethod
    def _image_reachable(url: str, timeout: float = 3.0) -> tuple[bool, str]:
        """
        Probe `url` with HEAD; returns (reachable, status_or_error).
        Never raises — exceptions are caught and reported.
        """
        try:
            res = requests.head(
                url,
                timeout=timeout,
                allow_redirects=True,
                headers={"User-Agent": "TrafficLiftPro-QualityControl/1.0"},
            )
            if res.status_code >= 400:
                return False, f"HTTP {res.status_code}"
            return True, f"HTTP {res.status_code}"
        except requests.RequestException as exc:
            return False, f"{type(exc).__name__}: {exc}"

    @classmethod
    def audit_product(cls, product: dict) -> dict:
        """
        Validate a single product dict. Raises ValueError on schema
        failures, semantic mismatches, or malformed image URLs. Logs
        warnings for asset-reachability issues (which are non-fatal).
        Returns the product on success.
        """
        product_id = str(product.get("id", "dynamic-product"))

        # 1. Schema & Field Completeness
        for field in REQUIRED_FIELDS:
            value = product.get(field)
            if value is None or value == "" or value == []:
                logger.error(
                    "[Agent Alert] Product %s failed audit: Missing "
                    "mandatory field '%s'.",
                    product_id, field,
                )
                raise ValueError(
                    f"Product Control Violation: Missing mandatory field "
                    f"'{field}' in product payload."
                )

        # 2. Semantic Consistency & Category Guard
        category = product["category"]
        name     = product["name"]
        matched, key = cls._semantic_check(category, name)
        if key and not matched:
            logger.warning(
                "[Agent Warning] Semantic mismatch detected: '%s' does "
                "not align with category '%s'.",
                name, category,
            )
            raise ValueError(
                f"Product Control Violation: Category mismatch between "
                f"'{category}' and product name '{name}'."
            )
        elif not key:
            logger.info(
                "[Agent Notice] %s has unmapped category '%s' — skipping "
                "semantic check.",
                product_id, category,
            )

        # 3. Image URL format validation
        image_url = product.get("image_url", "")
        if not image_url.startswith("http"):
            raise ValueError(
                f"Product Control Violation: Invalid image URL format "
                f"'{image_url}'."
            )

        # 4. Asset Integrity — verify the image URL responds
        ok, status = cls._image_reachable(image_url)
        if not ok:
            logger.warning(
                "[Agent Warning] Image URL returned status %s: %s",
                status, image_url,
            )

        logger.info(
            "[Product Control Agent] Product '%s' successfully audited "
            "and cleared.",
            product_id,
        )
        return product

    @classmethod
    def audit_catalog(cls, catalog: Iterable[dict]) -> list[dict]:
        """
        Audit an entire catalog. First failure (schema, semantic, or
        URL format) raises immediately so a single bad product can't
        silently mask a broken catalog.
        """
        audited: list[dict] = []
        for product in catalog:
            audited.append(cls.audit_product(product))
        return audited
