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


def test_same_sku_relisting_with_presentation_query_cannot_repeat(tmp_path):
    """Display modes and sorting do not create new products when SKU is stable."""
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    first = identity_keys(
        "Rechargeable Desk Lamp Original Edition",
        "https://shop.example/products/desk-lamp?sku=MODEL-A&view=quick&sort_by=price-ascending",
    )
    relisted = identity_keys(
        "Portable LED Reading Light Updated",
        "https://shop.example/products/reading-light?SKU=MODEL-A&view=full&sort_by=manual",
    )
    assert history.claim(first)
    assert not WinnerHistory("returning-browser", path=history.path).claim(relisted), (
        "Changing only display options, title and path must not recycle a merchant SKU"
    )


def test_presentation_query_does_not_merge_distinct_skus_or_child_options(tmp_path):
    """Presentation keys are safe to ignore for SKU identity, variant keys are not."""
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    first = identity_keys(
        "Adjustable Shelf Model One",
        "https://shop.example/products/shelf?sku=BASE-100&view=quick",
    )
    different_sku = identity_keys(
        "Adjustable Shelf Model Two",
        "https://shop.example/products/other?sku=BASE-200&view=full",
    )
    child = identity_keys(
        "Adjustable Shelf Model One Blue",
        "https://shop.example/products/other?sku=BASE-100&option_id=blue&view=full",
    )
    assert history.claim(first)
    assert history.claim(different_sku), "Different merchant SKUs must remain distinct"
    assert not any(key.startswith("merchant-sku-") for key in child), (
        "Unknown child-variant selectors must still disable broad SKU identity"
    )


def test_html_escaped_product_title_cannot_recycle_after_url_change(tmp_path):
    """Retailer HTML-escaped ampersands must not create a fresh winner."""
    path = tmp_path / "seen.db"
    old = identity_keys(
        "Premium Bamboo &amp; Steel Drawer Organizer",
        "https://shop.example/products/bamboo-steel-original?utm_source=first",
    )
    relisted = identity_keys(
        "Premium Bamboo & Steel Drawer Organizer",
        "https://shop.example/products/updated-storage-shelf?utm_source=second",
    )
    assert WinnerHistory("returning-browser", path=path).claim(old)
    assert not WinnerHistory("returning-browser", path=path).claim(relisted)


def test_html_escaped_title_reverse_direction_and_distinct_quantity(tmp_path):
    """Both spellings share identity; genuinely different packs remain eligible."""
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    assert history.claim(identity_keys(
        "Premium Bamboo & Steel Drawer Organizer 2 Pack",
        "https://shop.example/products/original-2",
    ))
    assert not history.claim(identity_keys(
        "Premium Bamboo &amp; Steel Drawer Organizer 2 Pack",
        "https://shop.example/products/renamed-2",
    ))
    assert history.claim(identity_keys(
        "Premium Bamboo &amp; Steel Drawer Organizer 4 Pack",
        "https://shop.example/products/other-4",
    ))


def test_preupgrade_html_escaped_history_hash_still_blocks_plain_title(tmp_path):
    """Older SQLite rows only contain hashes, so old 'amp' tokens must match."""
    import hashlib
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    old_folded = "premium bamboo amp steel drawer organizer"
    old_key = "name-folded-" + hashlib.sha256(old_folded.encode()).hexdigest()[:24]
    assert history.claim([old_key])
    assert not WinnerHistory("returning-browser", path=history.path).claim(identity_keys(
        "Premium Bamboo & Steel Drawer Organizer",
        "https://shop.example/products/brand-new-slug",
    ))


def test_ebay_item_id_blocks_relisted_winner_across_mobile_urls(tmp_path):
    """An eBay item ID survives title, slug, click tracking and mobile-host changes."""
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    original = identity_keys(
        "Compact Rechargeable Desk Lamp Original",
        "https://www.ebay.com/itm/Compact-Desk-Lamp/325019872006?mkcid=1&mkevt=1",
    )
    relisted = identity_keys(
        "Portable LED Reading Light Updated",
        "https://m.ebay.com/itm/325019872006?mkcid=99&campid=second",
    )
    assert history.claim(original)
    assert not WinnerHistory("returning-browser", path=history.path).claim(relisted), (
        "The same eBay item must not win twice when listing title, path and host change"
    )
    assert history.claim(identity_keys(
        "Completely Different Outdoor Lantern",
        "https://www.ebay.com/itm/325019872007",
    )), "A different eBay item ID remains eligible"


def test_ebay_variant_selector_and_unrelated_host_do_not_collapse_products(tmp_path):
    """An item ID alone is not a sellable variant when eBay ?var= selects a child."""
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    first = identity_keys(
        "Compact Lamp Small Blue Edition",
        "https://www.ebay.com/itm/325019872006?var=111111111111",
    )
    second = identity_keys(
        "Portable Lantern Large Red Edition",
        "https://www.ebay.com/itm/325019872006?var=222222222222",
    )
    assert not any(k.startswith("ebay-item-") for k in first + second)
    assert history.claim(first)
    assert history.claim(second), "Different eBay variants must not be merged"
    assert not any(k.startswith("ebay-item-") for k in identity_keys(
        "Third-party Item", "https://not-ebay.com/itm/325019872006"
    )), "Never trust a lookalike merchant hostname"


def test_ebay_same_variation_cannot_repeat_across_relisted_urls(tmp_path):
    """Stable eBay item+variation IDs must survive changed title, slug and tracking."""
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    original = identity_keys(
        "Adjustable Shelf Bracket Small Blue",
        "https://www.ebay.com/itm/Original-Shelf-Bracket/325019872006"
        "?var=675019872006&mkcid=1&campid=first",
    )
    renamed = identity_keys(
        "Compact Wall Storage Support Azure Edition",
        "https://m.ebay.com/itm/325019872006"
        "?VAR=675019872006&mkcid=2&campid=second",
    )
    assert history.claim(original)
    assert not WinnerHistory("returning-browser", path=history.path).claim(renamed), (
        "The same selected eBay variation must not win again after relisting"
    )


def test_ebay_variation_selection_keeps_other_variants_and_parent_distinct(tmp_path):
    """An eBay item can have different child variants; do not claim parent identity."""
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    blue = identity_keys(
        "Adjustable Shelf Bracket Blue",
        "https://ebay.com/itm/325019872006?var=675019872006",
    )
    red = identity_keys(
        "Adjustable Shelf Bracket Red",
        "https://ebay.com/itm/325019872006?var=675019872007",
    )
    parent = identity_keys(
        "Adjustable Shelf Bracket Base",
        "https://ebay.com/itm/325019872006",
    )
    assert history.claim(blue)
    assert history.claim(red)
    assert history.claim(parent)
    assert not any(k.startswith("ebay-item-") for k in blue + red)


def test_ebay_ambiguous_variation_selectors_do_not_claim_stable_variant_key():
    """Conflicting/unknown query selectors cannot be assumed to identify one SKU."""
    urls = (
        "https://ebay.com/itm/325019872006?var=675019872006&var=675019872007",
        "https://ebay.com/itm/325019872006?var=675019872006&size=large",
        "https://ebay.com/itm/325019872006?var=not-numeric",
        "https://unrelated.example/itm/325019872006?var=675019872006",
    )
    for url in urls:
        keys = identity_keys("Unique Shelf Bracket Model", url)
        assert not any(k.startswith("ebay-variant-") for k in keys), url


def test_ebay_duplicate_identical_variation_selectors_are_one_product(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    first = identity_keys(
        "Wall Mount Organizer Blue",
        "https://ebay.com/itm/325019872006?var=675019872006",
    )
    second = identity_keys(
        "Different Retailer Description Azure",
        "https://www.ebay.com/itm/New-Title/325019872006"
        "?VAR=675019872006&var=675019872006&mkcid=2",
    )
    assert history.claim(first)
    assert not history.claim(second)


def test_ebay_international_domain_relisting_cannot_repeat_winner(tmp_path):
    """One global eBay item ID must survive a marketplace-domain change."""
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    uk = identity_keys(
        "Blue Adjustable Shelf Organizer Original",
        "https://www.ebay.co.uk/itm/Original-Shelf-Organizer/325019872006?mkcid=1",
    )
    us = identity_keys(
        "Compact Wall Storage Support New Edition",
        "https://www.ebay.com/itm/325019872006?campid=second",
    )
    assert history.claim(uk)
    assert not WinnerHistory("returning-browser", path=history.path).claim(us), (
        "A marketplace redirect must not recycle the same eBay item"
    )


def test_ebay_international_variant_relisting_and_distinct_child(tmp_path):
    """An eBay variation stays excluded across countries; other variants stay fresh."""
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    original = identity_keys(
        "Small Blue Shelf Support",
        "https://www.ebay.de/itm/325019872006?var=675019872006&mkcid=1",
    )
    relisted = identity_keys(
        "Compact Azure Wall Bracket",
        "https://m.ebay.com/itm/Changed-Title/325019872006?VAR=675019872006&campid=2",
    )
    different = identity_keys(
        "Large Red Wall Support",
        "https://www.ebay.co.uk/itm/325019872006?var=675019872007",
    )
    assert history.claim(original)
    assert not WinnerHistory("returning-browser", path=history.path).claim(relisted)
    assert history.claim(different), "A distinct eBay child variation remains eligible"


def test_ebay_international_host_allowlist_rejects_lookalikes():
    """Do not trust unrelated hosts or broad wildcard marketplace suffixes."""
    valid = identity_keys(
        "One Product", "https://www.ebay.com.au/itm/325019872006"
    )
    assert "ebay-item-325019872006" in valid
    for host in ("ebay.co.uk.evil.example", "not-ebay.de", "ebay.example"):
        keys = identity_keys(
            "Different Product", f"https://{host}/itm/325019872006"
        )
        assert not any(k.startswith("ebay-item-") or k.startswith("ebay-variant-")
                       for k in keys), host


def test_mobile_merchant_sku_cannot_recycle_renamed_winner(tmp_path):
    """An explicit SKU remains one winner across merchant desktop/mobile hosts."""
    history = WinnerHistory("returning-owner", path=tmp_path / "seen.db")
    desktop = identity_keys(
        "Premium Bamboo Drawer Organizer Original",
        "https://shop.example/products/bamboo-organizer?sku=MODEL-100&utm_source=ad",
    )
    mobile = identity_keys(
        "Compact Wooden Storage Tray Updated",
        "https://m.shop.example/products/storage-tray?SKU=MODEL-100&gclid=click",
    )
    assert history.claim(desktop)
    assert not WinnerHistory("returning-owner", path=history.path).claim(mobile)


def test_mobile_merchant_sku_first_cannot_recycle_desktop_winner(tmp_path):
    """History written by mobile clients must exclude later desktop listings."""
    history = WinnerHistory("returning-owner", path=tmp_path / "seen.db")
    mobile = identity_keys(
        "Compact Wooden Storage Tray Updated",
        "https://m.shop.example/products/storage-tray?sku=MODEL-100",
    )
    desktop = identity_keys(
        "Premium Bamboo Drawer Organizer Original",
        "https://shop.example/products/bamboo-organizer?SKU=MODEL-100",
    )
    assert history.claim(mobile)
    assert not WinnerHistory("returning-owner", path=history.path).claim(desktop)


def test_mobile_sku_alias_preserves_merchants_and_selected_options(tmp_path):
    """A mobile alias cannot merge another seller or distinct child options."""
    history = WinnerHistory("returning-owner", path=tmp_path / "seen.db")
    assert history.claim(identity_keys(
        "Premium Bamboo Drawer Organizer Original",
        "https://shop.example/products/one?sku=BASE-100",
    ))
    assert history.claim(identity_keys(
        "Another Seller Wooden Storage Tray Updated",
        "https://m.other.example/products/two?sku=BASE-100",
    ))
    child = identity_keys(
        "Large Blue Drawer Tray Edition",
        "https://m.shop.example/products/three?sku=BASE-100&size=large",
    )
    assert not any(key.startswith("merchant-sku-") for key in child)
    assert history.claim(child)
