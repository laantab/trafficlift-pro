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


@pytest.mark.parametrize("tracking_key", [
    "pr_prod_strat", "pr_rec_id", "pr_ref_pid", "pr_seq",
])
def test_shopify_recommendation_context_cannot_recycle_winner(tmp_path, tracking_key):
    """Shopify's recommendation-source fields describe placement, not a new SKU."""
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.sqlite3")
    original = identity_keys(
        "Bamboo Adjustable Desk Organizer",
        f"https://shop.example/products/desk-organizer?{tracking_key}=source-a",
    )
    renamed = identity_keys(
        "Wood Office Storage Rack",
        f"https://shop.example/products/desk-organizer?{tracking_key}=source-b",
    )
    assert history.claim(original)
    assert not WinnerHistory("returning-browser", path=history.path).claim(renamed), (
        "Recommendation tracking changes must not turn the same listing into a fresh winner"
    )


def test_shopify_recommendation_context_keeps_selected_variants_distinct(tmp_path):
    """Removing placement attribution must not erase a real variant selector."""
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.sqlite3")
    assert history.claim(identity_keys(
        "Desk Organizer Blue",
        "https://shop.example/products/desk-organizer?variant=44332211009988"
        "&pr_rec_id=first&pr_ref_pid=111",
    ))
    assert history.claim(identity_keys(
        "Desk Organizer Red",
        "https://shop.example/products/desk-organizer?variant=44332211009989"
        "&pr_rec_id=second&pr_ref_pid=222",
    )), "Different selected variants must remain eligible"
