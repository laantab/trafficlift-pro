"""Regression: retailer pack/count abbreviations cannot reset a prior winner."""
import hashlib
import sqlite3

from backend.winner_history import WinnerHistory, identity_keys


def test_pack_and_count_abbreviations_do_not_recycle_winners(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.sqlite3")
    assert history.claim(identity_keys(
        "Stackable Storage Bins 12pk", "https://shop.example/old-pack",
    ))
    assert not WinnerHistory("returning-browser", path=history.path).claim(identity_keys(
        "Stackable Storage Bins 12-pack", "https://shop.example/new-pack",
    ))
    assert not history.claim(identity_keys(
        "12 packs Stackable Storage Bins", "https://other.example/offer",
    ))
    assert history.claim(identity_keys(
        "Stackable Storage Bins 16 pack", "https://shop.example/larger",
    )), "Different pack quantities must remain eligible"

    assert history.claim(identity_keys(
        "Reusable Cleaning Cloths 24count", "https://shop.example/old-cloth",
    ))
    assert not history.claim(identity_keys(
        "Reusable Cleaning Cloths 24 count", "https://shop.example/new-cloth",
    ))
    assert history.claim(identity_keys(
        "Reusable Cleaning Cloths 36 count", "https://shop.example/larger-cloth",
    ))


def test_legacy_compact_pack_and_count_hashes_still_exclude_relistings(tmp_path):
    """Existing SQLite rows cannot be rewritten from original product names."""
    path = tmp_path / "old.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    for legacy_title, new_title in (
        ("stackable storage bins 12pk", "Stackable Storage Bins 12 pack"),
        ("reusable cleaning cloths 24count", "Reusable Cleaning Cloths 24 count"),
    ):
        old_key = "name-folded-" + hashlib.sha256(legacy_title.encode()).hexdigest()[:24]
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO discovery_seen VALUES (?,?,?)", (history.scope, old_key, 1))
        assert not history.claim(identity_keys(
            new_title, "https://shop.example/relisted-" + legacy_title.replace(" ", "-"),
        )), "Old compact-unit winner hashes must remain permanently excluded"


def test_pack_and_count_are_not_automatically_equated(tmp_path):
    """Avoid conflating quantities with potentially different selling units."""
    history = WinnerHistory("returning-browser", path=tmp_path / "separate.sqlite3")
    assert history.claim(identity_keys(
        "Storage Bags 12 pack", "https://one.example/item",
    ))
    assert history.claim(identity_keys(
        "Storage Bags 12 count", "https://two.example/item",
    )), "Pack and count are not interchangeable without SKU evidence"
    assert history.claim(identity_keys(
        "Gold Chain 24ct", "https://three.example/item",
    ))
    assert history.claim(identity_keys(
        "Gold Chain 24 count", "https://four.example/item",
    )), "ct can mean carat and must not be silently equated with count"


def test_ampersand_and_written_conjunction_do_not_recycle_winner(tmp_path):
    """Retailers can change '&' to 'and' while renaming and relisting a pack."""
    history = WinnerHistory("returning-browser", path=tmp_path / "conjunction.sqlite3")
    assert history.claim(identity_keys(
        "Bamboo & Steel Drawer Organizer 12 Pack",
        "https://shop.example/old-item?utm_source=pinterest",
    ))
    assert not WinnerHistory("returning-browser", path=history.path).claim(identity_keys(
        "12pk Steel and Bamboo Drawer Organiser",
        "https://other.example/relisted?utm_source=instagram",
    )), "The same product cannot be recommended again after '&' becomes 'and'"
    assert history.claim(identity_keys(
        "16pk Steel and Bamboo Drawer Organiser",
        "https://other.example/larger",
    )), "A genuinely different pack quantity must remain eligible"


def test_old_written_and_title_hash_blocks_new_ampersand_listing(tmp_path):
    """Pre-upgrade permanent SQLite keys must exclude a renamed listing."""
    path = tmp_path / "legacy-conjunction.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    old_tokens = "bamboo and steel drawer organizer 12 pack".split()
    old_key = "title-" + hashlib.sha256(
        " ".join(sorted(old_tokens)).encode()
    ).hexdigest()[:24]
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO discovery_seen VALUES (?,?,?)", (history.scope, old_key, 1))
    assert not history.claim(identity_keys(
        "12 Pack Steel & Bamboo Drawer Organizer",
        "https://shop.example/new-item",
    )), "Legacy conjunction-based title keys must still exclude the winner"



def test_feet_foot_and_ft_relistings_do_not_recycle_winners(tmp_path):
    """A retailer's 4ft/4-foot/4 feet spelling changes cannot reset history."""
    path = tmp_path / "feet.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    assert history.claim(identity_keys(
        "Foldable Storage Shelf 4ft", "https://seller.example/old-4ft",
    ))
    for title, url in (
        ("Foldable Storage Shelf 4 feet", "https://seller.example/new-feet"),
        ("4-foot Foldable Storage Shelf", "https://other.example/4-foot"),
        ("Foldable Storage Shelf 4 ft", "https://other.example/4-ft"),
    ):
        assert not WinnerHistory("returning-browser", path=path).claim(
            identity_keys(title, url)
        ), "A unit spelling change must not yield another winner: " + title
    assert history.claim(identity_keys(
        "Foldable Storage Shelf 5 feet", "https://seller.example/5-feet",
    )), "Different sizes must remain eligible"
    assert history.claim(identity_keys(
        "Foldable Storage Shelf 4 inches", "https://seller.example/4-inch",
    )), "Feet and inches are different measurements"


def test_legacy_foot_unit_hash_excludes_relisted_product(tmp_path):
    """Older permanent rows store hashes only, not recoverable product titles."""
    path = tmp_path / "legacy-feet.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    old_title = "foldable storage shelf 4ft"
    old_key = "name-folded-" + hashlib.sha256(old_title.encode()).hexdigest()[:24]
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO discovery_seen VALUES (?,?,?)", (history.scope, old_key, 1))
    assert not WinnerHistory("returning-browser", path=path).claim(identity_keys(
        "Foldable Storage Shelf 4 feet", "https://seller.example/new-4-feet",
    )), "Legacy 4ft winner history must not be recycled"


def test_feet_normalization_does_not_merge_other_units(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "distinct-units.sqlite3")
    assert history.claim(identity_keys("Wall Shelf 4ft", "https://one.example/4ft"))
    assert history.claim(identity_keys("Wall Shelf 4 cm", "https://two.example/4cm"))
    assert history.claim(identity_keys("Wall Shelf 4in", "https://three.example/4in"))


def test_retailer_size_query_key_casing_does_not_recycle_winner(tmp_path):
    """Retailer redirects can change SIZE to size without changing the selected SKU."""
    path = tmp_path / "query-size.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    assert history.claim(identity_keys(
        "Adjustable Desk Lamp Blue Large",
        "https://seller.example/products/desk-lamp?SIZE=Large&utm_source=pin",
    ))
    assert not WinnerHistory("returning-browser", path=path).claim(identity_keys(
        "Rechargeable Reading Light Large",
        "https://seller.example/products/desk-lamp?size=Large&gclid=another-click",
    )), "A query key casing change must not create a fresh winner"
    assert history.claim(identity_keys(
        "Rechargeable Reading Light Small",
        "https://seller.example/products/desk-lamp?size=Small",
    )), "Different selected sizes remain distinct"


def test_retailer_color_query_key_casing_does_not_recycle_winner(tmp_path):
    """Color and COLOR select the same value; color values remain distinct."""
    path = tmp_path / "query-color.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    assert history.claim(identity_keys(
        "Kitchen Organizer Blue",
        "https://seller.example/products/organizer?Color=Blue&fbclid=first",
    ))
    assert not WinnerHistory("returning-browser", path=path).claim(identity_keys(
        "Compact Drawer Divider Blue",
        "https://seller.example/products/organizer?color=Blue&fbclid=second",
    )), "Query selector capitalization is not a new product variant"
    assert history.claim(identity_keys(
        "Compact Drawer Divider Green",
        "https://seller.example/products/organizer?color=Green",
    )), "Distinct color variants must remain eligible"


def test_legacy_uppercase_variant_query_url_hash_still_blocks_relisting(tmp_path):
    """Old permanent SQLite URL keys must survive a retailer's key casing change."""
    path = tmp_path / "legacy-query-case.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    old_url = "https://seller.example/products/desk-lamp?SIZE=Large"
    old_key = "url-" + hashlib.sha256(old_url.encode()).hexdigest()[:24]
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO discovery_seen VALUES (?,?,?)", (history.scope, old_key, 1))
    assert not WinnerHistory("returning-browser", path=path).claim(identity_keys(
        "Completely Renamed Large Reading Lamp",
        "https://seller.example/products/desk-lamp?size=Large&gclid=another-click",
    )), "Old uppercase URL hashes must remain effective after the upgrade"


def test_identical_repeated_size_selectors_are_one_variant(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "repeat-size.sqlite3")
    assert history.claim(identity_keys(
        "Large Adjustable Lamp",
        "https://seller.example/products/lamp?SIZE=Large&size=Large",
    ))
    assert not history.claim(identity_keys(
        "Portable Reading Light",
        "https://seller.example/products/lamp?size=Large",
    )), "Duplicated identical variant selectors cannot reset winner history"
    assert history.claim(identity_keys(
        "Portable Reading Light Small",
        "https://seller.example/products/lamp?size=Small",
    )), "Different selector values must stay distinct"


def test_conflicting_size_selector_values_do_not_collapse(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "conflicting-size.sqlite3")
    assert history.claim(identity_keys(
        "Large Adjustable Lamp",
        "https://seller.example/products/lamp?size=Large",
    ))
    assert history.claim(identity_keys(
        "Ambiguous Selection",
        "https://seller.example/products/lamp?SIZE=Small&size=Large",
    )), "Conflicting variant selectors must not be treated as one unambiguous SKU"


def test_legacy_piece_abbreviation_hash_still_excludes_relisted_winner(tmp_path):
    """Pre-normalization SQLite rows contain only the old title hash.

    A relisted 18-piece item must not be returned again if its prior record
    used '18 pcs' and the merchant changed both title order and URL.
    """
    path = tmp_path / "legacy-piece.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    old_title = "criusia drawer organizer 18 pcs set"
    old_key = "name-folded-" + hashlib.sha256(old_title.encode()).hexdigest()[:24]
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO discovery_seen VALUES (?,?,?)", (history.scope, old_key, 1))
    assert not WinnerHistory("returning-browser", path=path).claim(identity_keys(
        "18-piece Criusia Drawer Organiser Set", "https://other.example/new-item",
    )), "A pre-upgrade 'pcs' winner must remain excluded after relisting"


def test_legacy_compact_piece_title_hash_and_distinct_quantity(tmp_path):
    """A prior sorted title hash using 12pc must survive URL/name changes."""
    path = tmp_path / "legacy-piece-title.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    old_tokens = "acme storage drawer organizer 12pc".split()
    old_key = "title-" + hashlib.sha256(
        " ".join(sorted(old_tokens)).encode()
    ).hexdigest()[:24]
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO discovery_seen VALUES (?,?,?)", (history.scope, old_key, 1))
    assert not history.claim(identity_keys(
        "12 pieces Acme Drawer Storage Organiser", "https://shop.example/relisted",
    )), "An old compact piece-count title key must still exclude the same winner"
    assert history.claim(identity_keys(
        "20 pieces Acme Drawer Storage Organiser", "https://shop.example/20-piece",
    )), "Different package quantities must remain eligible"
