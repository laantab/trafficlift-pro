"""Regression: unselected Shopify locale URLs identify the same product.

A merchant may redirect a root /products/<slug> URL through localized
/en-us/products/<slug> or /fr/products/<slug> storefront paths. For an
unselected product, a renamed listing must not bypass durable winner history.
Do not merge selected variants, unrelated products, or unrelated merchants.
"""
import hashlib
import sqlite3

from backend.winner_history import WinnerHistory, identity_keys


def test_unselected_product_cannot_repeat_across_locale_and_mobile(tmp_path):
    path = tmp_path / "discovery.sqlite3"
    owner = "returning-owner"
    assert WinnerHistory(owner, path).claim(identity_keys(
        "Natural Bamboo Drawer Tray",
        "https://m.seller.example/en-us/products/drawer-tray?utm_source=pin",
    ))
    assert not WinnerHistory(owner, path).claim(identity_keys(
        "Wood Office Supply Organizer",
        "https://seller.example/fr/products/drawer-tray?fbclid=abc",
    )), "Locale and mobile redirects must not recycle the same unselected winner"


def test_localized_link_matches_legacy_root_url_only_history(tmp_path):
    path = tmp_path / "discovery.sqlite3"
    owner = WinnerHistory("returning-owner", path)
    legacy_url = "https://seller.example/products/drawer-tray"
    legacy_key = "url-" + hashlib.sha256(legacy_url.encode()).hexdigest()[:24]
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO discovery_seen VALUES (?,?,?)",
            (owner.scope, legacy_key, 1),
        )
    assert not WinnerHistory("returning-owner", path).claim(identity_keys(
        "Renamed Bathroom Storage Organizer",
        "https://m.seller.example/en/products/drawer-tray?utm_campaign=launch",
    )), "Pre-upgrade URL-only history must remain effective"


def test_shopify_locale_alias_does_not_merge_other_variants_or_merchants(tmp_path):
    history = WinnerHistory("returning-owner", tmp_path / "discovery.sqlite3")
    assert history.claim(identity_keys(
        "Drawer Tray Size Small",
        "https://seller.example/en/products/drawer-tray?variant=44332211009988",
    ))
    assert history.claim(identity_keys(
        "Drawer Tray Size Large",
        "https://m.seller.example/fr/products/drawer-tray?variant=44332211009989",
    )), "Selected variant IDs must remain distinct"
    assert history.claim(identity_keys(
        "Wood Desk Organizer With Wide Tray",
        "https://seller.example/en/products/drawer-tray?size=large",
    ))
    assert history.claim(identity_keys(
        "Wood Desk Organizer With Narrow Tray",
        "https://m.seller.example/fr/products/drawer-tray?size=small",
    )), "Unknown selectors must not be collapsed into a parent identity"
    assert history.claim(identity_keys(
        "Kitchen Cabinet Storage Baskets",
        "https://seller.example/en/products/kitchen-baskets",
    )), "Other product slugs must remain eligible"
    assert history.claim(identity_keys(
        "Garden Pot Plant Display Stand",
        "https://other.example/fr/products/drawer-tray",
    )), "Another merchant must remain eligible"
