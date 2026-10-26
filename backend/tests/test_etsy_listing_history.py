"""Etsy listing IDs must prevent repeat winners across relistings and click tracking.

Only trusted Etsy hosts may claim the stable listing identity. Distinct
selected variations and unknown product selectors must remain separate.
No live research, purchases, video renders, or paid providers are used.
"""
from backend.winner_history import WinnerHistory, identity_keys


def test_etsy_listing_rename_mobile_host_and_click_tracking_do_not_repeat(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.sqlite3")
    original = identity_keys(
        "Handmade Bamboo Drawer Organizer",
        "https://www.etsy.com/listing/1234567890/handmade-bamboo-organizer"
        "?click_key=ad-click-a&click_sum=111&ref=search_recently_viewed",
    )
    relisted = identity_keys(
        "Wood Office Storage Rack",
        "https://m.etsy.com/listing/1234567890/desk-storage-rack"
        "?click_key=ad-click-b&click_sum=222&ref=shop_home_active_1",
    )
    assert history.claim(original)
    assert not WinnerHistory("returning-browser", path=history.path).claim(relisted)


def test_etsy_selected_variation_stays_excluded_after_listing_rename(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.sqlite3")
    first = identity_keys(
        "Ceramic Planter Blue Small",
        "https://www.etsy.com/listing/9876543210/planter"
        "?variation0=111222333&click_key=first",
    )
    renamed = identity_keys(
        "Small Blue Handmade Pot",
        "https://etsy.com/listing/9876543210/handmade-pot"
        "?click_key=second&variation0=111222333",
    )
    assert history.claim(first)
    assert not history.claim(renamed)


def test_etsy_distinct_variations_and_unknown_selectors_remain_eligible(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.sqlite3")
    assert history.claim(identity_keys(
        "Blue Ceramic Pot",
        "https://etsy.com/listing/9876543210/pot?variation0=111222333",
    ))
    assert history.claim(identity_keys(
        "Red Ceramic Pot",
        "https://etsy.com/listing/9876543210/pot?variation0=111222334",
    ))
    assert history.claim(identity_keys(
        "Small Linen Apron",
        "https://etsy.com/listing/7654321098/apron?custom_size=small",
    ))
    assert history.claim(identity_keys(
        "Large Linen Apron",
        "https://etsy.com/listing/7654321098/apron?custom_size=large",
    ))


def test_etsy_lookalike_domain_cannot_claim_official_listing_identity(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.sqlite3")
    assert history.claim(identity_keys(
        "Handmade Walnut Bookstand",
        "https://etsy.com/listing/1234567890/walnut-bookstand",
    ))
    assert history.claim(identity_keys(
        "Portable Reading Desk Stand",
        "https://etsy-market.example/listing/1234567890/reading-stand",
    ))
