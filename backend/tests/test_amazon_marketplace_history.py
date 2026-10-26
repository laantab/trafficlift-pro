"""International Amazon ASINs must not reset permanent winner history.

The ASIN is a stable catalog identifier across official Amazon marketplaces.
Unrelated lookalike domains must not be trusted.
"""
from backend.winner_history import WinnerHistory, identity_keys


def test_amazon_uk_to_us_relisting_cannot_repeat_winner(tmp_path):
    history = WinnerHistory("returning-owner", path=tmp_path / "history.sqlite3")
    old = identity_keys(
        "Bamboo Three-Tier Shelf Organizer",
        "https://www.amazon.co.uk/gp/product/B08EXAMP12?tag=affiliate",
    )
    renamed = identity_keys(
        "Adjustable Natural Wood Storage Rack",
        "https://m.amazon.com/dp/B08EXAMP12?ref_=mobile",
    )
    assert "amazon-asin-b08examp12" in old
    assert history.claim(old)
    assert not history.claim(renamed), "Changing marketplace and title must not reset history"


def test_amazon_de_to_jp_relisting_keeps_distinct_asins_eligible(tmp_path):
    history = WinnerHistory("returning-owner", path=tmp_path / "history.sqlite3")
    original = identity_keys(
        "Compact Desk Storage Basket",
        "https://www.amazon.de/dp/B08EXAMP12",
    )
    relisted = identity_keys(
        "Multipurpose Office Basket",
        "https://www.amazon.co.jp/gp/aw/d/B08EXAMP12?utm_source=search",
    )
    different = identity_keys(
        "Multipurpose Office Basket Large",
        "https://www.amazon.co.jp/dp/B08EXAMP13",
    )
    assert history.claim(original)
    assert not history.claim(relisted)
    assert history.claim(different), "A different ASIN must remain eligible"


def test_amazon_marketplace_allowlist_rejects_lookalike_hosts():
    for host in ("amazon.co.uk.evil.example", "not-amazon.de", "amazon.example"):
        keys = identity_keys("Unique Listing", f"https://{host}/dp/B08EXAMP12")
        assert "amazon-asin-b08examp12" not in keys
