"""A Shopify variant remains the same winner after its product slug is renamed."""
from backend.winner_history import WinnerHistory, identity_keys


def test_shopify_variant_relisted_under_new_slug_cannot_win_again(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.db")
    original = identity_keys(
        "Rechargeable Desk Lamp Small",
        "https://seller.example/products/rechargeable-desk-lamp?variant=44332211&utm_source=ad",
    )
    renamed = identity_keys(
        "Portable Reading Light Compact",
        "https://seller.example/products/portable-reading-light?variant=44332211&_pos=4&gad_source=2",
    )
    assert history.claim(original)
    assert not WinnerHistory("returning-browser", path=history.path).claim(renamed), (
        "Changing a Shopify listing slug and title must not recycle the same variant"
    )


def test_shopify_variant_scope_preserves_other_merchants_and_skus(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "winners.db")
    assert history.claim(identity_keys(
        "Desk Lamp Compact A", "https://seller-a.example/products/desk-lamp?variant=44332211"
    ))
    assert history.claim(identity_keys(
        "Desk Lamp Large B", "https://seller-a.example/products/desk-lamp?variant=44332212"
    )), "A different variant on the same merchant must remain eligible"
    assert history.claim(identity_keys(
        "Other Brand Desk Lamp", "https://seller-b.example/products/desk-lamp?variant=44332211"
    )), "Another merchant's variant ID must not be conflated"
