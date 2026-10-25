"""Attribution click IDs must not turn a previous winner into a new product."""

import pytest

from backend.winner_history import WinnerHistory, identity_keys


@pytest.mark.parametrize("tracking_key", ["dclid", "li_fat_id"])
def test_ad_click_id_changes_cannot_recycle_relisted_winner(tmp_path, tracking_key):
    """Google/DoubleClick and LinkedIn click IDs identify visits, not SKUs."""
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.sqlite3")
    old = identity_keys(
        "Compact Rechargeable Desk Lamp Model A",
        f"https://shop.example/products/desk-lamp?{tracking_key}=first-click&sku=MODEL-A",
    )
    relisted = identity_keys(
        "Portable LED Reading Light Model A",
        f"https://shop.example/products/desk-lamp?{tracking_key}=second-click&sku=MODEL-A",
    )
    assert history.claim(old)
    assert not WinnerHistory("returning-browser", path=history.path).claim(relisted)


@pytest.mark.parametrize("tracking_key", ["dclid", "li_fat_id"])
def test_ad_click_id_normalization_preserves_distinct_skus(tmp_path, tracking_key):
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.sqlite3")
    assert history.claim(identity_keys(
        "Compact Rechargeable Desk Lamp Model A",
        f"https://shop.example/products/desk-lamp?{tracking_key}=click-a&sku=MODEL-A",
    ))
    assert history.claim(identity_keys(
        "Portable LED Reading Light Model B",
        f"https://shop.example/products/desk-lamp?{tracking_key}=click-b&sku=MODEL-B",
    )), "Distinct retailer SKUs must remain eligible"
