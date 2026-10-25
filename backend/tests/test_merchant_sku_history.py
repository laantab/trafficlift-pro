"""Merchant-scoped SKU history must survive retailer relistings without conflating SKUs."""
from backend.winner_history import WinnerHistory, identity_keys


def test_same_merchant_sku_blocks_renamed_product_with_new_path(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    old = identity_keys(
        "Rechargeable Desk Lamp Original Edition",
        "https://shop.example/products/desk-lamp?sku=MODEL-A&gclid=first",
    )
    renamed = identity_keys(
        "Portable LED Reading Light Updated",
        "https://shop.example/products/reading-light?SKU=MODEL-A&gclid=second",
    )
    assert history.claim(old)
    assert not WinnerHistory("returning-browser", path=history.path).claim(renamed), (
        "A new slug, name and click ID must not recycle the same merchant SKU"
    )


def test_distinct_skus_and_merchants_remain_eligible(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    assert history.claim(identity_keys(
        "Rechargeable Desk Lamp Original Edition",
        "https://shop.example/products/desk-lamp?sku=MODEL-A",
    ))
    assert history.claim(identity_keys(
        "Portable LED Reading Light Updated",
        "https://shop.example/products/reading-light?sku=MODEL-B",
    ))
    assert history.claim(identity_keys(
        "Travel Illumination Device Another Seller",
        "https://other.example/products/reading-light?sku=MODEL-A",
    ))


def test_repeated_identical_sku_selectors_cannot_bypass_history(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    assert history.claim(identity_keys(
        "Rechargeable Desk Lamp Original Edition",
        "https://shop.example/products/desk-lamp?sku=MODEL-A",
    ))
    assert not history.claim(identity_keys(
        "Portable LED Reading Light Updated",
        "https://shop.example/products/reading-light?SKU=%20MODEL-A%20&sku=MODEL-A",
    ))


def test_conflicting_or_weak_sku_values_not_assigned_merchant_identity():
    for query in ("sku=MODEL-A&sku=MODEL-B", "sku=1", "sku=", "sku=ab"):
        keys = identity_keys(
            "One Unique Product",
            "https://shop.example/products/one?" + query,
        )
        assert not any(key.startswith("merchant-sku-") for key in keys), query


def test_sku_value_case_not_folded():
    first = identity_keys("One Unique Product", "https://shop.example/p/one?sku=ABcd-1")
    second = identity_keys("Another Different Product", "https://shop.example/p/two?sku=abcd-1")
    scoped = lambda keys: {k for k in keys if k.startswith("merchant-sku-")}
    assert scoped(first) and scoped(second) and scoped(first).isdisjoint(scoped(second))


def test_parent_sku_with_variant_selectors_is_not_conflated(tmp_path):
    """A shared parent SKU must not merge different sizes or child variants."""
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    first = identity_keys(
        "Adjustable Shelf Small Model",
        "https://shop.example/products/shelf-small?sku=BASE-100&size=small",
    )
    second = identity_keys(
        "Expandable Storage Rack Large Edition",
        "https://shop.example/products/shelf-large?sku=BASE-100&size=large",
    )
    assert not any(key.startswith("merchant-sku-") for key in first + second)
    assert history.claim(first)
    assert history.claim(second), "Distinct sizes must remain eligible"


def test_unrecognized_query_selector_disables_broad_sku_key():
    keys = identity_keys(
        "Outdoor Organizer",
        "https://shop.example/products/organizer?sku=BASE-100&option_id=blue",
    )
    assert not any(key.startswith("merchant-sku-") for key in keys)
