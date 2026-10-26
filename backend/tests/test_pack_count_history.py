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
