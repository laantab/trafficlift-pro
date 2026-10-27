"""Shopify mobile/desktop links without a variant must not recycle winners.

A renamed listing with the same /products/<slug> URL can arrive through
m.<merchant> on mobile and <merchant> on desktop. This is the same
unselected product, even when no numeric Shopify variant was supplied.
"""
import hashlib
import sqlite3

from backend.winner_history import WinnerHistory, identity_keys


def test_unselected_shopify_product_does_not_repeat_across_mobile_host(tmp_path):
    path = tmp_path / "history.sqlite3"
    original = identity_keys(
        "Bamboo Drawer Divider Compact",
        "https://seller.example/products/drawer-tray?utm_source=pinterest",
    )
    renamed = identity_keys(
        "Wood Office Storage Tray",
        "https://m.seller.example/products/drawer-tray?ref=mobile",
    )
    assert WinnerHistory("returning-owner", path=path).claim(original)
    assert not WinnerHistory("returning-owner", path=path).claim(renamed)


def test_mobile_first_unselected_product_does_not_repeat_on_desktop(tmp_path):
    path = tmp_path / "history.sqlite3"
    first = identity_keys(
        "Wood Desk Organizer",
        "https://m.seller.example/products/drawer-tray?utm_campaign=spring",
    )
    second = identity_keys(
        "Bamboo Storage Tray",
        "https://seller.example/products/drawer-tray?fbclid=another",
    )
    assert WinnerHistory("returning-owner", path=path).claim(first)
    assert not WinnerHistory("returning-owner", path=path).claim(second)


def test_mobile_alias_matches_legacy_desktop_url_only_history(tmp_path):
    """Old permanent rows contain URL hashes, not original listing details."""
    path = tmp_path / "history.sqlite3"
    history = WinnerHistory("returning-owner", path=path)
    old_url = "https://seller.example/products/drawer-tray"
    old_key = "url-" + hashlib.sha256(old_url.encode()).hexdigest()[:24]
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO discovery_seen VALUES (?,?,?)",
            (history.scope, old_key, 1),
        )
    assert not WinnerHistory("returning-owner", path=path).claim(identity_keys(
        "Renamed Organizer Tray",
        "https://m.seller.example/products/drawer-tray?utm_source=pin",
    ))


def test_mobile_alias_keeps_other_products_and_variants_distinct(tmp_path):
    history = WinnerHistory("returning-owner", path=tmp_path / "history.sqlite3")
    assert history.claim(identity_keys(
        "Bamboo Drawer Tray",
        "https://seller.example/products/drawer-tray?variant=44332211009988",
    ))
    assert history.claim(identity_keys(
        "Steel Drawer Rack",
        "https://m.seller.example/products/drawer-tray?variant=44332211009989",
    )), "A different selected variant is a distinct sellable item"
    assert history.claim(identity_keys(
        "Desk Lamp Model B",
        "https://m.seller.example/products/desk-lamp",
    )), "Different Shopify product slugs remain eligible"
    assert history.claim(identity_keys(
        "Other Merchant Tray",
        "https://m.other.example/products/drawer-tray",
    )), "Different merchants must never share a URL identity"
