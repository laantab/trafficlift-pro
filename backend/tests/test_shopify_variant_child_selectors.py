"""A Shopify variant is not enough when additional selectors choose a child item.

Some storefronts reuse a numeric variant for distinct size/custom-option
selections. Those are different sellable products; presentation and tracking
query fields, by contrast, do not change the selected item.
"""
from backend.winner_history import WinnerHistory, identity_keys


def test_same_numeric_variant_with_distinct_sizes_is_not_one_winner(tmp_path):
    history = WinnerHistory("returning-owner", tmp_path / "seen.sqlite3")
    assert history.claim(identity_keys(
        "Compact Blue Shelf Bracket Small",
        "https://seller.example/products/bracket?variant=44332211009988&size=small",
    ))
    assert history.claim(identity_keys(
        "Expanded Red Wall Support Large",
        "https://m.seller.example/collections/storage/products/bracket-new"
        "?variant=44332211009988&size=large&utm_source=pin",
    )), "Distinct size selections cannot be merged by a shared numeric variant"


def test_parent_numeric_variant_does_not_claim_custom_child_option(tmp_path):
    history = WinnerHistory("returning-owner", tmp_path / "seen.sqlite3")
    assert history.claim(identity_keys(
        "Original Standard Storage Rack",
        "https://seller.example/products/rack?variant=44332211009988",
    ))
    assert history.claim(identity_keys(
        "Custom Painted Storage Organizer",
        "https://seller.example/products/rack-new?variant=44332211009988&option_id=blue",
    )), "A custom child option must remain eligible"


def test_numeric_variant_does_not_claim_broad_key_when_child_option_present():
    keys = identity_keys(
        "Custom Shelf Blue Model",
        "https://seller.example/products/shelf?variant=44332211009988&option_id=blue",
    )
    assert not any(key.startswith("shopify-variant-") for key in keys)


def test_same_numeric_variant_with_presentation_and_tracking_still_excluded(tmp_path):
    history = WinnerHistory("returning-owner", tmp_path / "seen.sqlite3")
    assert history.claim(identity_keys(
        "Original Desk Drawer Tray",
        "https://seller.example/products/desk-tray?variant=44332211009988"
        "&view=quick&utm_source=pin",
    ))
    assert not history.claim(identity_keys(
        "Renamed Home Storage Rack",
        "https://m.seller.example/en-us/products/new-tray?VARIANT=44332211009988"
        "&sort_by=best-selling&fbclid=click",
    )), "Changing only presentation, tracking, title and slug cannot recycle a winner"


def test_same_selected_child_with_tracking_still_excluded(tmp_path):
    history = WinnerHistory("returning-owner", tmp_path / "seen.sqlite3")
    assert history.claim(identity_keys(
        "Compact Blue Shelf Bracket Small",
        "https://seller.example/products/bracket?variant=44332211009988&size=small"
        "&utm_source=pin",
    ))
    assert not history.claim(identity_keys(
        "Compact Blue Shelf Bracket Small",
        "https://seller.example/products/bracket?size=small&variant=44332211009988"
        "&fbclid=other",
    ))


def test_selected_child_relisted_with_new_title_slug_and_storefront_stays_excluded(tmp_path):
    """A stable numeric variant plus the same child option is one product."""
    history = WinnerHistory("returning-owner", tmp_path / "seen.sqlite3")
    first = identity_keys(
        "Compact Blue Shelf Bracket Small",
        "https://seller.example/products/old-bracket?variant=44332211009988"
        "&option_id=blue&size=small&utm_source=pin",
    )
    relisted = identity_keys(
        "Rebranded Wall Support Model X",
        "https://m.seller.example/en-us/collections/storage/products/new-support"
        "?SIZE=small&OPTION_ID=blue&VARIANT=44332211009988"
        "&view=quick&fbclid=another-click",
    )
    assert history.claim(first)
    assert not history.claim(relisted), (
        "A changed title, slug, mobile host or attribution must not recycle a selected child"
    )


def test_distinct_child_options_remain_eligible_even_when_variant_reused(tmp_path):
    history = WinnerHistory("returning-owner", tmp_path / "seen.sqlite3")
    assert history.claim(identity_keys(
        "Blue Custom Rack",
        "https://seller.example/products/old?variant=44332211009988&option_id=blue",
    ))
    assert history.claim(identity_keys(
        "Red Custom Rack",
        "https://m.seller.example/products/new?variant=44332211009988&option_id=red",
    )), "A different child option is a different sellable item"


def test_conflicting_child_selectors_do_not_claim_ambiguous_stable_identity():
    keys = identity_keys(
        "Special Custom Shelf",
        "https://seller.example/products/shelf?variant=44332211009988"
        "&option_id=blue&option_id=red",
    )
    assert not any(key.startswith("shopify-child-") for key in keys), (
        "Conflicting child selections cannot be assigned a stable SKU"
    )
