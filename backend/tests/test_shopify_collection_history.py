"""Shopify collection routes must not recycle a claimed winner.

Shopify storefronts commonly link the same product via /products/<handle>
and /collections/<collection>/products/<handle>. These are presentation
routes; a numeric ?variant still identifies a distinct sellable variant.
"""
import hashlib
import sqlite3

from backend.winner_history import WinnerHistory, identity_keys


def test_root_product_then_collection_route_is_not_fresh(tmp_path):
    db = tmp_path / "history.sqlite3"
    assert WinnerHistory("returning-owner", db).claim(identity_keys(
        "Natural Bamboo Drawer Tray",
        "https://seller.example/products/drawer-tray?utm_source=pinterest",
    ))
    assert not WinnerHistory("returning-owner", db).claim(identity_keys(
        "Wooden Office Storage Organizer",
        "https://m.seller.example/en-us/collections/office/products/drawer-tray?fbclid=click",
    )), "A collection landing page must not recycle an already claimed product"


def test_collection_route_then_root_product_is_not_fresh(tmp_path):
    db = tmp_path / "history.sqlite3"
    assert WinnerHistory("returning-owner", db).claim(identity_keys(
        "Natural Bamboo Drawer Tray",
        "https://seller.example/fr/collections/office/products/drawer-tray",
    ))
    assert not WinnerHistory("returning-owner", db).claim(identity_keys(
        "Wooden Office Storage Organizer",
        "https://seller.example/products/drawer-tray",
    )), "Collection-first winners must exclude later direct product links"


def test_collection_route_matches_legacy_root_url_hash(tmp_path):
    db = tmp_path / "history.sqlite3"
    history = WinnerHistory("returning-owner", db)
    old = "https://seller.example/products/drawer-tray"
    legacy = "url-" + hashlib.sha256(old.encode()).hexdigest()[:24]
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO discovery_seen VALUES (?,?,?)",
                           (history.scope, legacy, 1))
    assert not WinnerHistory("returning-owner", db).claim(identity_keys(
        "Wooden Office Storage Organizer",
        "https://seller.example/collections/office/products/drawer-tray",
    ))


def test_collection_variant_stays_distinct_but_cannot_repeat(tmp_path):
    db = tmp_path / "history.sqlite3"
    history = WinnerHistory("returning-owner", db)
    assert history.claim(identity_keys(
        "Small Bamboo Tray Model A",
        "https://seller.example/collections/office/products/drawer-tray?variant=44332211009988",
    ))
    assert not history.claim(identity_keys(
        "Desk Organizer Compact Model A",
        "https://m.seller.example/products/renamed-drawer-tray?variant=44332211009988",
    )), "The same numeric variant must remain claimed across routes"
    assert history.claim(identity_keys(
        "Large Bamboo Tray Model B",
        "https://seller.example/products/drawer-tray?variant=44332211009989",
    )), "A different variant must remain eligible"
    assert history.claim(identity_keys(
        "Different Merchant Kitchen Tray",
        "https://other.example/collections/office/products/drawer-tray",
    )), "Unrelated merchants must not be collapsed"
    assert history.claim(identity_keys(
        "Another Product Garden Tray",
        "https://seller.example/collections/office/products/garden-tray",
    )), "Different product handles must remain eligible"


def test_collection_unknown_selector_does_not_claim_parent(tmp_path):
    history = WinnerHistory("returning-owner", tmp_path / "history.sqlite3")
    assert history.claim(identity_keys(
        "Desk Tray Narrow Blue Edition",
        "https://seller.example/collections/office/products/drawer-tray?size=small",
    ))
    assert history.claim(identity_keys(
        "Desk Tray Wide Green Edition",
        "https://seller.example/products/drawer-tray?size=large",
    )), "Unknown selectors cannot be merged into an unselected parent"


def test_shopify_view_parameter_does_not_recycle_renamed_winner(tmp_path):
    """Shopify ?view=quick is presentation, not a different sellable product."""
    path = tmp_path / "history.sqlite3"
    first = identity_keys(
        "Natural Bamboo Drawer Tray",
        "https://seller.example/products/drawer-tray",
    )
    renamed = identity_keys(
        "Wooden Office Storage Organizer",
        "https://m.seller.example/en-us/collections/office/products/drawer-tray?view=quick",
    )
    assert WinnerHistory("returning-owner", path).claim(first)
    assert not WinnerHistory("returning-owner", path).claim(renamed)


def test_shopify_view_first_then_direct_link_is_not_fresh(tmp_path):
    """The first claim must also establish the stable product identity."""
    path = tmp_path / "history.sqlite3"
    first = identity_keys(
        "Wooden Office Storage Organizer",
        "https://seller.example/collections/office/products/drawer-tray?view=quick&sort_by=best-selling",
    )
    renamed = identity_keys(
        "Natural Bamboo Drawer Tray",
        "https://seller.example/products/drawer-tray",
    )
    assert WinnerHistory("returning-owner", path).claim(first)
    assert not WinnerHistory("returning-owner", path).claim(renamed)


def test_shopify_presentation_alias_does_not_merge_actual_options(tmp_path):
    """Only known presentation keys may be ignored, never SKU selectors."""
    history = WinnerHistory("returning-owner", tmp_path / "history.sqlite3")
    assert history.claim(identity_keys(
        "Desk Tray Narrow Blue Edition",
        "https://seller.example/products/drawer-tray?view=quick&size=small",
    ))
    assert history.claim(identity_keys(
        "Desk Tray Wide Green Edition",
        "https://seller.example/products/drawer-tray?view=quick&size=large",
    ))
    assert history.claim(identity_keys(
        "Another Merchant Desk Organizer",
        "https://other.example/products/drawer-tray?view=quick",
    ))
