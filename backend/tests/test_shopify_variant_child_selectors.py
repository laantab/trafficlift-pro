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
