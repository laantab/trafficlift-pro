"""Regression: retailer ounce abbreviations must not recycle a permanent winner.

The test for old hashes simulates a database created before the alias was added.
No migration or expiry reset should be necessary.
"""
import hashlib
import sqlite3

from backend.winner_history import WinnerHistory, identity_keys


def test_ounce_spellings_and_relisted_titles_do_not_repeat(tmp_path):
    history = WinnerHistory("returning-owner", path=tmp_path / "history.sqlite3")
    first = identity_keys(
        "Acme Steel Travel Tumbler 12 oz",
        "https://shop.example/products/tumbler-12oz?utm_source=pin",
    )
    relisted = identity_keys(
        "12-ounce Acme Travel Steel Tumbler",
        "https://other.example/products/steel-cup?ref=social",
    )
    assert history.claim(first)
    assert not history.claim(relisted), "12 oz and 12-ounce are one product size"


def test_historical_ounce_title_hash_still_excludes_new_oz_spelling(tmp_path):
    history = WinnerHistory("returning-owner", path=tmp_path / "history.sqlite3")
    # A pre-upgrade database can contain only this old sorted-title hash.
    old_tokens = ["acme", "steel", "travel", "tumbler", "12", "ounces"]
    legacy_title = "title-" + hashlib.sha256(
        " ".join(sorted(old_tokens)).encode()
    ).hexdigest()[:24]
    with sqlite3.connect(history.path) as db:
        db.execute(
            "INSERT INTO discovery_seen (scope, identity, expires) VALUES (?, ?, ?)",
            (history.scope, legacy_title, 1),
        )
    assert not history.claim(identity_keys(
        "Acme Travel Steel Tumbler 12oz",
        "https://other.example/renamed-cup",
    )), "Existing permanent history must survive spelling normalization"


def test_different_ounce_sizes_remain_distinct(tmp_path):
    history = WinnerHistory("returning-owner", path=tmp_path / "history.sqlite3")
    assert history.claim(identity_keys(
        "Acme Steel Travel Tumbler 12 ounces", "https://shop.example/cup-a"
    ))
    assert history.claim(identity_keys(
        "Acme Steel Travel Tumbler 16 oz", "https://shop.example/cup-b"
    )), "Do not collapse genuinely different capacities"
