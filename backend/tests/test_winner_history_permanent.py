"""Winner history cannot silently recycle products after 30 days."""
import sqlite3
from backend.winner_history import WinnerHistory, identity_keys


def test_expired_records_still_block_previous_product(tmp_path):
    history=WinnerHistory("returning-browser",path=tmp_path/"discovery.db")
    keys=identity_keys("Criusia Drawer Organizer 18 Piece Set","https://example.com/products/criusia")
    assert history.claim(keys)
    with sqlite3.connect(history.path) as db:
        db.execute("UPDATE discovery_seen SET expires = 1 WHERE scope=?", (history.scope,))
    history.next_cursor()
    assert set(keys).issubset(history.seen())
    assert not history.claim(keys)


def test_retailer_tracking_urls_cannot_repeat_identical_listing(tmp_path):
    history=WinnerHistory("buyer",path=tmp_path/"db.sqlite")
    one=identity_keys("Criusia Drawer Organizer","https://seller.example/criusia?utm_source=a")
    two=identity_keys("Criusia Drawer Organizer","https://seller.example/criusia?utm_source=b")
    assert history.claim(one)
    assert not history.claim(two)


def test_other_browser_has_its_own_history(tmp_path):
    path=tmp_path/"db.sqlite"
    assert WinnerHistory("browser-a",path=path).claim(["sample-key"])
    assert WinnerHistory("browser-b",path=path).claim(["sample-key"])


def test_amazon_product_duplicate_across_listing_paths(tmp_path):
    from backend.winner_history import identity_keys, WinnerHistory
    history = WinnerHistory("returning-browser", path=tmp_path/"history.db")
    first = identity_keys("Example Stainless Desk Lamp", "https://www.amazon.com/dp/B08EXAMP12?tag=affiliate")
    second = identity_keys("Example LED Desk Light with USB", "https://amazon.com/gp/product/B08EXAMP12/ref=something")
    assert history.claim(first)
    assert not history.claim(second)


def test_walmart_listing_names_can_change_without_repeat(tmp_path):
    from backend.winner_history import identity_keys, WinnerHistory
    history = WinnerHistory("returning-browser", path=tmp_path/"history.db")
    one = identity_keys("Example Metal Rack", "https://www.walmart.com/ip/Rack-Organizer/123456789")
    two = identity_keys("Adjustable Storage Rack", "https://www.walmart.com/ip/123456789?athbdg=1")
    assert history.claim(one)
    assert not history.claim(two)


def test_different_asins_and_items_not_collapsed(tmp_path):
    from backend.winner_history import identity_keys, WinnerHistory
    history = WinnerHistory("returning-browser", path=tmp_path/"history.db")
    assert history.claim(identity_keys("Example first model", "https://amazon.com/dp/B08EXAMP12"))
    assert history.claim(identity_keys("Example second model", "https://amazon.com/dp/B08EXAMP13"))



def test_default_history_path_uses_persistent_campaign_disk(tmp_path, monkeypatch):
    from backend.winner_history import default_history_path
    monkeypatch.delenv('TRAFFICLIFT_DISCOVERY_DB', raising=False)
    monkeypatch.setenv('CAMPAIGNS_DB_PATH', str(tmp_path / 'campaigns.db'))
    assert default_history_path() == tmp_path / 'discovery.sqlite3'
    first = WinnerHistory('returning-customer')
    assert first.path == tmp_path / 'discovery.sqlite3'
    assert first.claim(['name-first-winner'])
    # A new process/reload opens the same durable file and cannot recycle it.
    assert not WinnerHistory('returning-customer').claim(['name-first-winner'])


def test_explicit_history_path_takes_priority_over_campaign_disk(tmp_path, monkeypatch):
    from backend.winner_history import default_history_path
    explicit = tmp_path / 'separate' / 'winners.db'
    monkeypatch.setenv('TRAFFICLIFT_DISCOVERY_DB', str(explicit))
    monkeypatch.setenv('CAMPAIGNS_DB_PATH', str(tmp_path / 'campaigns.db'))
    assert default_history_path() == explicit
    assert WinnerHistory('client').path == explicit


def test_local_default_kept_without_render_configuration(monkeypatch):
    from backend.winner_history import default_history_path
    monkeypatch.delenv('TRAFFICLIFT_DISCOVERY_DB', raising=False)
    monkeypatch.delenv('CAMPAIGNS_DB_PATH', raising=False)
    monkeypatch.delenv('RENDER_SERVICE_ID', raising=False)
    assert default_history_path() == __import__('pathlib').Path('data/discovery.sqlite3')


def test_reordered_uk_spelling_and_piece_abbreviation_block_repeat(tmp_path):
    history = WinnerHistory('returning-customer', path=tmp_path/'winners.db')
    first = identity_keys('Criusia Drawer Organizer 18 Piece Set', 'https://seller.example/item-a?utm_source=ad')
    second = identity_keys('18 pc Criusia Drawer Organiser Set', 'https://other.example/offer-b')
    assert history.claim(first)
    assert not history.claim(second)


def test_tracking_click_ids_do_not_make_new_winner(tmp_path):
    history = WinnerHistory('returning-customer', path=tmp_path/'winners.db')
    one = identity_keys('Modern Desk Lamp', 'https://seller.example/products/lamp?clickid=one&msclkid=a')
    two = identity_keys('Modern Desk Lamp', 'https://seller.example/products/lamp?clickid=two&msclkid=b')
    assert history.claim(one)
    assert not history.claim(two)


def test_distinct_sizes_and_model_numbers_remain_distinct(tmp_path):
    history = WinnerHistory('returning-customer', path=tmp_path/'winners.db')
    assert history.claim(identity_keys('Acme Desk Lamp Model 100', 'https://shop.example/a'))
    assert history.claim(identity_keys('Acme Desk Lamp Model 101', 'https://shop.example/b'))
    assert history.claim(identity_keys('Acme Storage Rack 2 Pack', 'https://shop.example/c'))
    assert history.claim(identity_keys('Acme Storage Rack 4 Pack', 'https://shop.example/d'))


def test_history_is_per_browser_not_cross_device_account(tmp_path):
    path = tmp_path/'winners.db'
    one = identity_keys('18 Piece Drawer Organizer Set', 'https://seller.example/one')
    two = identity_keys('18 pc Drawer Organiser Set', 'https://seller.example/two')
    assert WinnerHistory('browser-one', path=path).claim(one)
    assert not WinnerHistory('browser-one', path=path).claim(two)
    # No customer account ID is currently passed to WinnerHistory.
    assert WinnerHistory('browser-two', path=path).claim(two)


def test_compact_piece_quantities_are_one_winner_but_distinct_counts_remain(tmp_path):
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    first = identity_keys('Criusia Drawer Organizer 18pc Set', 'https://shop-a.example/item/one')
    variant = identity_keys('18-piece Criusia Drawer Organiser Set', 'https://shop-b.example/item/two')
    distinct = identity_keys('Criusia Drawer Organizer 20pcs Set', 'https://shop-c.example/item/three')
    assert history.claim(first)
    assert not history.claim(variant), 'Joined count/unit must not bypass permanent history'
    assert history.claim(distinct), 'Different pack quantity is a different product'


def test_mobile_amazon_path_and_walmart_subdomain_preserve_retailer_ids(tmp_path):
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    assert history.claim(identity_keys('LED Reading Light Model One',
                                       'https://amazon.com/dp/B08EXAMP12?tag=affiliate'))
    assert not history.claim(identity_keys('Rechargeable Desk Light Model One',
                                           'https://m.amazon.com/gp/aw/d/B08EXAMP12?ref_=abc'))
    assert history.claim(identity_keys('Steel Storage Rack Model 42',
                                       'https://m.walmart.com/ip/Storage-Rack/123456789?athbdg=1'))
    assert not history.claim(identity_keys('Metal Shelving Rack Model 42',
                                           'https://www.walmart.com/ip/123456789?utm_source=ad'))
    assert history.claim(identity_keys('LED Reading Light Model Two',
                                       'https://amazon.com/dp/B08EXAMP13'))


def test_referral_tracking_does_not_recommend_same_product_after_title_change(tmp_path):
    """Referrer tags must not create a new winner when the listing title changes."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    original = identity_keys('Compact Desk Lamp Model A',
                             'https://seller.example/products/desk-lamp?ref_=homepage&aff_id=123')
    relisted = identity_keys('Rechargeable Reading Light Model A',
                             'https://seller.example/products/desk-lamp?ref_=email&aff_id=456')
    assert history.claim(original)
    assert not history.claim(relisted)


def test_exhausted_live_catalog_returns_no_fresh_winner(tmp_path, monkeypatch):
    """A previously claimed item must not be recycled on an exhausted search."""
    from types import SimpleNamespace
    from fastapi import HTTPException
    from backend import discovery, discovery_engine, live_research

    monkeypatch.setenv('TRAFFICLIFT_DISCOVERY_DB', str(tmp_path / 'winners.db'))
    name = 'Compact Desk Lamp Model A'
    url = 'https://seller.example/products/desk-lamp?ref_=email'
    history = WinnerHistory('returning-browser')
    assert history.claim(identity_keys(name, url))
    candidate = SimpleNamespace(id='candidate-1', name=name, url=url)
    envelope = SimpleNamespace(research_status='live',
                               research_sources=[{'title': name, 'url': url}],
                               research_errors=[])
    monkeypatch.setattr(live_research, 'research', lambda *a, **kw: envelope)
    monkeypatch.setattr(discovery, '_build_candidates_from_envelope',
                        lambda *a, **kw: [candidate])
    monkeypatch.setattr(discovery_engine, 'bounded_map',
                        lambda func, values, seconds: [func(value) for value in values])
    with __import__('pytest').raises(HTTPException) as error:
        discovery_engine._discover_batch(client_id='returning-browser', seed=1)
    assert error.value.status_code == 404
    assert 'Previous products remain excluded' in str(error.value.detail)
    assert not WinnerHistory('returning-browser').claim(identity_keys(name, url))


def test_target_listing_tcin_blocks_relisted_winner_without_collapsing_variants(tmp_path):
    """Target product URLs can change slugs, titles and referral query tags.

    Target's /p/<slug>/-/A-<item> and /p/-/A-<item> routes should share
    a durable identity. A preselected child variant is not necessarily
    the same SKU as its parent, so do not attach the parent's ID to it.
    """
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    first = identity_keys(
        'Compact Bamboo Drawer Divider',
        'https://www.target.com/p/bamboo-drawer-divider/-/A-91234567?ref=tgt_adv',
    )
    relisted = identity_keys(
        'Expandable Kitchen Storage Organizer',
        'https://target.com/p/-/A-91234567?utm_source=pin',
    )
    assert history.claim(first)
    assert not history.claim(relisted), 'A changed Target slug and title must not recycle a winner'
    assert history.claim(identity_keys(
        'Metal Shelf Bracket Model B', 'https://target.com/p/-/A-91234568'
    )), 'Different Target product IDs must remain eligible'
    assert 'target-tcin-91234567' not in identity_keys(
        'Drawer Divider Child Variant',
        'https://target.com/p/-/A-91234567?preselect=91234569',
    ), 'Parent identity must not collapse a separately preselected child variant'
    assert 'target-tcin-91234567' not in identity_keys(
        'Unrelated Seller', 'https://not-target.com/p/-/A-91234567',
    )


def test_target_preselected_child_relisting_does_not_repeat(tmp_path):
    """A Target child SKU stays the same across changed parent slugs and titles."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    first = identity_keys(
        'Blue Expandable Drawer Divider',
        'https://www.target.com/p/divider-original/-/A-91234567?preselect=91234569&utm_source=pin',
    )
    relisted = identity_keys(
        'Kitchen Organizer Adjustable Blue',
        'https://target.com/p/-/A-91234567?preselect=91234569&ref=affiliate',
    )
    assert history.claim(first)
    assert not history.claim(relisted), 'The same preselected Target child SKU cannot win twice'
    assert not history.claim(identity_keys(
        'New Title For Direct Child Listing',
        'https://target.com/p/-/A-91234569',
    )), 'A direct link to the same child TCIN must also be excluded'
    assert history.claim(identity_keys(
        'Different Green Child Variant',
        'https://target.com/p/-/A-91234567?preselect=91234570',
    )), 'A distinct preselected child remains eligible'
    assert history.claim(identity_keys(
        'Unselected Parent Product',
        'https://target.com/p/-/A-91234567',
    )), 'A separately selected child must not claim the parent product'


def test_target_preselect_does_not_claim_parent_or_non_target_host():
    selected = identity_keys(
        'Blue Drawer Divider',
        'https://target.com/p/-/A-91234567?preselect=91234569',
    )
    assert 'target-tcin-91234569' in selected
    assert 'target-tcin-91234567' not in selected
    assert 'target-tcin-91234569' not in identity_keys(
        'Blue Drawer Divider',
        'https://unrelated.example/p/-/A-91234567?preselect=91234569',
    )


def test_common_referrer_and_campaign_query_aliases_do_not_recycle_winner(tmp_path):
    """Changing only marketing attribution and listing title cannot reset history."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    first = identity_keys(
        'Compact Desk Lamp Model A',
        'https://seller.example/products/desk-lamp?referrer=email&affiliate_id=100&campaign_id=autumn',
    )
    relisted = identity_keys(
        'Rechargeable Reading Light Model A',
        'https://seller.example/products/desk-lamp?referrer=social&affiliate_id=200&campaign_id=winter',
    )
    assert history.claim(first)
    assert not history.claim(relisted), 'Marketing attribution is not a new product identity'


def test_variant_and_sku_queries_remain_distinct_after_tracking_cleanup(tmp_path):
    """Removing referral tags must never erase meaningful size/SKU selectors."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    assert history.claim(identity_keys(
        'Blue Desk Lamp Small', 'https://seller.example/products/lamp?size=small&referrer=email',
    ))
    assert history.claim(identity_keys(
        'Blue Desk Lamp Large', 'https://seller.example/products/lamp?size=large&referrer=email',
    ))
    assert history.claim(identity_keys(
        'Blue Desk Lamp Model B', 'https://seller.example/products/lamp?sku=model-b&referrer=email',
    ))



def test_shopify_search_and_google_ad_tracking_cannot_recycle_winner(tmp_path):
    """Shopify search placement and Google ad attribution are not product variants."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    first = identity_keys(
        'Rechargeable Desk Lamp Model A',
        'https://seller.example/products/desk-lamp?_pos=1&_sid=search-one&_ss=r'
        '&gad_source=1&gad_campaignid=111&gbraid=click-one&utm_source=google',
    )
    relisted = identity_keys(
        'Portable Reading Light Model A',
        'https://seller.example/products/desk-lamp?_pos=4&_sid=search-two&_ss=c'
        '&gad_source=5&gad_campaignid=222&wbraid=click-two&utm_source=pin',
    )
    assert history.claim(first)
    assert not history.claim(relisted), 'Ad/search attribution must not reset winner history'


def test_shopify_variant_selector_still_distinguishes_real_skus(tmp_path):
    """Tracking cleanup must preserve a meaningful Shopify ?variant selector."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    assert history.claim(identity_keys(
        'Rechargeable Desk Lamp Small',
        'https://seller.example/products/desk-lamp?variant=111&_pos=1&gad_source=1',
    ))
    assert history.claim(identity_keys(
        'Rechargeable Desk Lamp Large',
        'https://seller.example/products/desk-lamp?variant=222&_pos=2&gad_source=2',
    ))


def test_shopify_variant_relisted_under_new_slug_cannot_win_again(tmp_path):
    """A Shopify variant ID is stable when the seller rewrites slug and title."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    original = identity_keys(
        'Rechargeable Desk Lamp Small',
        'https://seller.example/products/rechargeable-desk-lamp?variant=44332211&utm_source=ad',
    )
    renamed = identity_keys(
        'Portable Reading Light Compact',
        'https://seller.example/products/portable-reading-light?variant=44332211&_pos=4&gad_source=2',
    )
    assert history.claim(original)
    assert not WinnerHistory('returning-browser', path=history.path).claim(renamed), (
        'Changing a Shopify slug and title must not recycle the same variant'
    )


def test_shopify_variant_scope_preserves_other_merchants_and_skus(tmp_path):
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    assert history.claim(identity_keys(
        'Desk Lamp Compact A', 'https://seller-a.example/products/desk-lamp?variant=44332211',
    ))
    assert history.claim(identity_keys(
        'Desk Lamp Large B', 'https://seller-a.example/products/desk-lamp?variant=44332212',
    )), 'Different variant on the same merchant remains eligible'
    assert history.claim(identity_keys(
        'Other Brand Desk Lamp', 'https://seller-b.example/products/desk-lamp?variant=44332211',
    )), 'Another merchant must not be conflated'



def test_shopify_localized_product_urls_keep_variant_history_across_relisting(tmp_path):
    """Shopify Markets adds /en, /en-us, /fr-ca before /products/slug."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    original = identity_keys(
        'Blue Expandable Desk Organizer',
        'https://shop.example/en-us/products/desk-organizer?variant=44332211009988&utm_source=pin',
    )
    relisted = identity_keys(
        'Compact Office Storage Rack',
        'https://shop.example/fr-ca/products/office-rack?variant=44332211009988&gad_source=1',
    )
    assert history.claim(original)
    assert not WinnerHistory('returning-browser', path=history.path).claim(relisted), (
        'A localized storefront path and renamed listing must not recycle the same SKU'
    )


def test_shopify_localized_variant_history_keeps_other_skus_and_merchants(tmp_path):
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    assert history.claim(identity_keys(
        'Blue Expandable Desk Organizer',
        'https://shop.example/en/products/desk-organizer?variant=44332211009988',
    ))
    assert history.claim(identity_keys(
        'Green Storage Rack',
        'https://shop.example/fr/products/office-rack?variant=44332211009989',
    )), 'Another variant on the same merchant remains eligible'
    assert history.claim(identity_keys(
        'Metal Storage Basket',
        'https://another.example/en-us/products/office-rack?variant=44332211009988',
    )), 'Shopify variant identifiers must be scoped to merchant host'
    assert not any(key.startswith('shopify-variant-') for key in identity_keys(
        'Unrelated Product', 'https://shop.example/blog/products/article?variant=44332211009988',
    )), 'Non-product paths must not be treated as Shopify variant routes'


def test_missing_browser_identity_fails_closed_before_live_research(monkeypatch):
    """A missing browser ID must never bypass permanent winner history."""
    import pytest
    from fastapi import HTTPException
    from backend import discovery_engine, live_research

    def forbidden_research(*args, **kwargs):
        pytest.fail("No paid or free research should run without durable winner identity")

    monkeypatch.setattr(live_research, "research", forbidden_research)
    for missing in (None, ""):
        with pytest.raises(HTTPException) as error:
            discovery_engine.discover(client_id=missing)
        assert error.value.status_code == 422
        assert "browser" in str(error.value.detail).lower()


def test_discovery_endpoint_requires_browser_identity(monkeypatch):
    """Direct API callers cannot opt out of no-repeat history by omitting client_id."""
    from fastapi.testclient import TestClient
    from backend import live_research
    from trafficlift_pro import app

    def forbidden_research(*args, **kwargs):
        raise AssertionError("Research must not start without a browser identity")

    monkeypatch.setattr(live_research, "research", forbidden_research)
    response = TestClient(app).get("/api/v1/discover-winner")
    assert response.status_code == 422
    assert "browser" in str(response.json().get("detail", "")).lower()


def test_short_unicode_title_relisting_is_not_a_fresh_winner(tmp_path):
    """A two-word accented product name must match its ASCII relisting.

    Two-word names have no sorted-title alias key; changing the listing URL
    currently bypasses the old ASCII-only primary name identity.
    """
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    original = identity_keys('Café Mug', 'https://shop.example/products/cafe-mug-old')
    relisted = identity_keys('Cafe Mug', 'https://shop.example/products/cafe-mug-new?utm_source=pin')
    assert history.claim(original)
    assert not WinnerHistory('returning-browser', path=history.path).claim(relisted), (
        'Accent-folded relistings must not recycle the same winner'
    )


def test_unicode_title_identity_does_not_collapse_distinct_sizes(tmp_path):
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    assert history.claim(identity_keys('Café Mug 12oz', 'https://shop.example/a'))
    assert history.claim(identity_keys('Cafe Mug 16oz', 'https://shop.example/b'))


def test_compact_measurement_relisting_cannot_recycle_winner(tmp_path):
    """A changed seller URL and spaced measurement must not reset a winner.

    A 12oz mug and a 12 oz mug describe the same quantity; a 16oz size
    is a different SKU and must remain eligible. This also applies to ml.
    """
    history = WinnerHistory('returning-browser', path=tmp_path / 'history.db')
    first = identity_keys('Café Travel Mug 12oz', 'https://seller.example/old-listing')
    relisted = identity_keys('Cafe Travel Mug 12 oz', 'https://seller.example/new-listing')
    assert history.claim(first)
    assert not WinnerHistory('returning-browser', path=history.path).claim(relisted), (
        'A measurement-space and URL change must not recycle a previous winner'
    )
    assert history.claim(identity_keys('Cafe Travel Mug 16 oz', 'https://seller.example/large'))

    second = identity_keys('Hydration Bottle 500ml', 'https://seller.example/bottle-old')
    assert history.claim(second)
    assert not history.claim(identity_keys('Hydration Bottle 500 ml', 'https://seller.example/bottle-new'))
    assert history.claim(identity_keys('Hydration Bottle 750 ml', 'https://seller.example/bottle-large'))


def test_existing_compact_and_spaced_history_keys_remain_effective(tmp_path):
    """Old SQLite hashes cannot be rewritten; new claims must recognize both.

    Simulate a winner recorded before measurement aliases existed, without
    inserting any of the new alternate keys into the historical database.
    """
    import hashlib
    path = tmp_path / 'legacy-history.db'
    history = WinnerHistory('returning-browser', path=path)
    for old_title, new_title in (
        ('cafe travel mug 12oz', 'Cafe Travel Mug 12 oz'),
        ('hydration bottle 500 ml', 'Hydration Bottle 500ml'),
    ):
        old_key = 'name-folded-' + hashlib.sha256(old_title.encode()).hexdigest()[:24]
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO discovery_seen VALUES (?,?,?)', (history.scope, old_key, 1))
        assert not WinnerHistory('returning-browser', path=path).claim(
            identity_keys(new_title, 'https://seller.example/changed-' + old_title.replace(' ', '-'))
        ), 'Pre-upgrade permanent history must still exclude measurement relistings'


def test_percent_encoded_unreserved_product_path_cannot_recycle_winner(tmp_path):
    """Retailer URL encoders may escape letters or hyphens without changing SKU."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    original = identity_keys(
        'Compact Desk Lamp Model A',
        'https://shop.example/products/desk-lamp?utm_source=pin',
    )
    relisted = identity_keys(
        'Rechargeable Reading Light Model A',
        'https://shop.example/products/%64esk%2Dlamp?utm_source=video',
    )
    assert history.claim(original)
    assert not history.claim(relisted), (
        'Percent-encoding an unreserved path character must not create a new winner'
    )
    reverse = WinnerHistory('second-browser', path=history.path)
    assert reverse.claim(relisted)
    assert not reverse.claim(original), 'Canonicalization must work in both directions'


def test_percent_encoded_reserved_path_and_real_skus_remain_distinct(tmp_path):
    """Encoded slashes must not become route separators; SKU selectors stay intact."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    assert history.claim(identity_keys(
        'Lamp Variant Alpha', 'https://shop.example/products/desk%2Flamp?sku=alpha'
    ))
    assert history.claim(identity_keys(
        'Lamp Variant Beta', 'https://shop.example/products/desk/lamp?sku=beta'
    ))
    assert history.claim(identity_keys(
        'Lamp Variant Gamma', 'https://shop.example/products/desk%2Flamp?sku=gamma'
    ))


def test_repeated_identical_shopify_variant_parameters_cannot_recycle_winner(tmp_path):
    """Repeated case-varied ?variant values still identify one merchant SKU.

    Search redirects and integrations can append the same variant selector
    twice. This must not turn a previously claimed SKU into a new winner
    when the listing title and slug also change.
    """
    path = tmp_path / "repeated-variant.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    original = identity_keys(
        "Blue Compact Drawer Divider",
        "https://seller.example/products/blue-divider?variant=44332211009988",
    )
    renamed = identity_keys(
        "Expandable Storage Organizer",
        "https://seller.example/en-us/products/storage-organizer"
        "?Variant=44332211009988&variant=44332211009988&utm_source=ad",
    )
    assert history.claim(original)
    assert not WinnerHistory("returning-browser", path=path).claim(renamed), (
        "Duplicated identical Shopify variant selectors cannot reset winner history"
    )
    assert history.claim(identity_keys(
        "Different Green Organizer",
        "https://seller.example/products/green-organizer?variant=44332211009989",
    )), "Different variant IDs must remain eligible"
    assert history.claim(identity_keys(
        "Another Merchant Organizer",
        "https://another.example/products/organizer?variant=44332211009988",
    )), "The same numeric variant on a different merchant is not the same SKU"


def test_conflicting_shopify_variant_parameters_are_not_assumed_equivalent():
    """Conflicting variant selectors are ambiguous; do not choose one arbitrarily."""
    keys = identity_keys(
        "Ambiguous Product",
        "https://seller.example/products/organizer"
        "?variant=44332211009988&variant=44332211009989",
    )
    assert not any(key.startswith("shopify-variant-") for key in keys)


def test_ad_click_tracking_cannot_recycle_renamed_winner_after_restart(tmp_path):
    """TikTok click IDs, Google linker and Pinterest click attribution are not SKUs."""
    path = tmp_path / 'winner-history.db'
    original = identity_keys(
        'Portable Desk Light Compact A',
        'https://seller.example/products/lamp?sku=compact-a&TTCLID=first'
        '&_gl=1~old&epik=pin-a',
    )
    renamed = identity_keys(
        'USB Rechargeable Reading Lantern',
        'https://seller.example/products/lamp?epik=pin-b&_gl=1~new'
        '&ttclid=second&sku=compact-a',
    )
    assert WinnerHistory('returning-browser', path=path).claim(original)
    assert not WinnerHistory('returning-browser', path=path).claim(renamed), (
        'New ad-click identifiers must not recycle a winner after browser reload'
    )


def test_ad_tracking_cleanup_keeps_real_sku_selectors(tmp_path):
    """Do not discard merchant product variants while stripping attribution."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winner-history.db')
    assert history.claim(identity_keys(
        'Desk Light Compact Blue',
        'https://seller.example/products/lamp?sku=compact-blue&ttclid=first&_gl=1~a',
    ))
    assert history.claim(identity_keys(
        'Desk Light Large Green',
        'https://seller.example/products/lamp?sku=large-green&ttclid=second&_gl=1~b',
    )), 'Different SKUs remain eligible despite shared listing path'


def test_inch_measurement_aliases_never_recycle_relisted_winner(tmp_path):
    """12in, 12 inch, 12 inches, and 12-inch are one size despite URL changes."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'inches.db')
    assert history.claim(identity_keys(
        'Expandable Drawer Divider 12in', 'https://seller.example/old-listing'
    ))
    for title, url in (
        ('Expandable Drawer Divider 12 inches', 'https://seller.example/relisted-one'),
        ('Expandable Drawer Divider 12 inch', 'https://seller.example/relisted-two'),
        ('Expandable Drawer Divider 12 in', 'https://seller.example/relisted-three'),
        ('Expandable Drawer Divider 12-inch', 'https://seller.example/relisted-four'),
    ):
        assert not WinnerHistory('returning-browser', path=history.path).claim(
            identity_keys(title, url)
        ), f'An inch spelling change cannot create a fresh winner: {title}'
    assert history.claim(identity_keys(
        'Expandable Drawer Divider 16 inches', 'https://seller.example/large'
    )), 'Different dimensions remain distinct'
    assert history.claim(identity_keys(
        'Expandable Drawer Divider 12 cm', 'https://seller.example/metric'
    )), 'Different units are not equivalent'


def test_preexisting_inch_spelling_hash_still_blocks_relisting(tmp_path):
    """Retain aliases for old durable name-folded hashes, not just new claims."""
    import hashlib
    path = tmp_path / 'legacy-inch.db'
    history = WinnerHistory('returning-browser', path=path)
    old = 'folding shelf bracket 12in'
    old_key = 'name-folded-' + hashlib.sha256(old.encode()).hexdigest()[:24]
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO discovery_seen VALUES (?,?,?)', (history.scope, old_key, 1))
    assert not WinnerHistory('returning-browser', path=path).claim(
        identity_keys('Folding Shelf Bracket 12 inches', 'https://seller.example/new-url')
    ), 'An old 12in winner must remain excluded after an inch-unit spelling change'


def test_inch_alias_does_not_rewrite_two_in_one_product_phrase():
    """A numeric '2 in 1' feature is not a two-inch measurement."""
    phrase = identity_keys('Portable Blender 2 in 1', 'https://seller.example/one')
    measurement = identity_keys('Portable Blender 2 inch 1', 'https://seller.example/two')
    assert not set(phrase) & set(measurement)


def test_html_escaped_tracking_query_cannot_recycle_relisted_winner(tmp_path):
    """HTML-escaped retailer links and clean URLs must identify the same SKU."""
    path = tmp_path / 'escaped-query.sqlite3'
    history = WinnerHistory('returning-browser', path=path)
    original = identity_keys(
        'Rechargeable Desk Lamp Compact A',
        'https://seller.example/products/lamp?sku=compact-a&amp;utm_source=pin',
    )
    renamed = identity_keys(
        'Portable Reading Light Model B',
        'https://seller.example/products/lamp?sku=compact-a&utm_source=video',
    )
    assert history.claim(original)
    assert not WinnerHistory('returning-browser', path=path).claim(renamed), (
        'HTML-escaped tracking separators must not turn a claimed SKU into a new winner'
    )
    assert history.claim(identity_keys(
        'Different Portable Light Model C',
        'https://seller.example/products/lamp?sku=compact-b&utm_source=pin',
    )), 'Distinct product SKUs must remain eligible'


def test_html_escaped_shopify_variant_selector_cannot_recycle_winner(tmp_path):
    """An escaped ampersand before variant= must not hide the stable SKU ID."""
    path = tmp_path / 'escaped-variant.sqlite3'
    history = WinnerHistory('returning-browser', path=path)
    original = identity_keys(
        'Compact Drawer Divider Blue',
        'https://seller.example/products/blue-divider?utm_source=pin&amp;variant=44332211009988',
    )
    renamed = identity_keys(
        'Expandable Storage Rack Small',
        'https://seller.example/en-us/products/storage-rack?variant=44332211009988',
    )
    assert history.claim(original)
    assert not WinnerHistory('returning-browser', path=path).claim(renamed), (
        'HTML-escaped Shopify variant selectors must preserve permanent SKU exclusions'
    )
    assert history.claim(identity_keys(
        'Expandable Storage Rack Large',
        'https://seller.example/products/storage-rack?variant=44332211009989',
    )), 'Different Shopify variants must remain eligible'


def test_numeric_html_query_entities_preserve_shopify_variant_identity(tmp_path):
    """Both decimal and hex HTML ampersands must preserve retailer SKU selectors."""
    for index, entity in enumerate(('&#38;', '&#x26;')):
        history = WinnerHistory(f'client-{index}', path=tmp_path / 'numeric-entities.sqlite3')
        assert history.claim(identity_keys(
            'Original Desk Lamp Model A',
            'https://shop.example/products/lamp?utm_source=pin' + entity + 'variant=44332211009988',
        ))
        assert not history.claim(identity_keys(
            'Renamed Reading Lantern Model B',
            'https://shop.example/fr-ca/products/lantern?variant=44332211009988',
        ))


def test_html_query_unescaping_does_not_change_product_path_or_sku(tmp_path):
    """An entity-looking path is not an HTML query separator; distinct SKUs stay distinct."""
    history = WinnerHistory('same-browser', path=tmp_path / 'path-control.sqlite3')
    assert history.claim(identity_keys(
        'Compact Desk Light Model A',
        'https://shop.example/products/lamp&amp;special?sku=compact-a',
    ))
    assert history.claim(identity_keys(
        'Portable Reading Lantern Model B',
        'https://shop.example/products/lamp&special?sku=compact-a',
    )), 'Do not decode HTML entities in product paths'
    assert history.claim(identity_keys(
        'Large Desk Light Model C',
        'https://shop.example/products/lamp&amp;special?sku=compact-b',
    )), 'Do not merge distinct SKUs'


def test_case_changed_and_repeated_sku_query_cannot_recycle_winner(tmp_path):
    """Seller integrations can repeat an identical SKU under differently cased keys."""
    path = tmp_path / "sku-repeat.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    original = identity_keys(
        "Compact Blue Desk Lamp",
        "https://seller.example/offer?sku=AB-12&ref=first",
    )
    relisted = identity_keys(
        "Rechargeable LED Reading Light",
        "https://seller.example/offer?SKU=AB-12&sku=AB-12&ttclid=other",
    )
    assert history.claim(original)
    assert not WinnerHistory("returning-browser", path=path).claim(relisted), (
        "Changing selector capitalization and repeating the same SKU cannot reset history"
    )
    assert history.claim(identity_keys(
        "Larger LED Reading Light",
        "https://seller.example/offer?sku=AB-13",
    )), "A genuinely different SKU must remain eligible"


def test_repeated_variant_query_on_non_shopify_path_cannot_recycle_winner(tmp_path):
    """Duplicate variant query selectors must normalize even outside /products URLs."""
    history = WinnerHistory("returning-browser", path=tmp_path / "variant-repeat.sqlite3")
    assert history.claim(identity_keys(
        "Small Travel Organizer",
        "https://seller.example/item?variant=blue-small",
    ))
    assert not history.claim(identity_keys(
        "Compact Packing Cubes",
        "https://seller.example/item?Variant=blue-small&variant=blue-small",
    )), "An identical repeated variant is not a fresh product"
    assert history.claim(identity_keys(
        "Large Packing Cubes",
        "https://seller.example/item?variant=blue-large",
    )), "Different variants must remain distinct"
    assert history.claim(identity_keys(
        "Ambiguous Selector Listing",
        "https://seller.example/item?variant=blue-small&variant=blue-large",
    )), "Conflicting selector values must not be collapsed to one variant"


def test_retailer_sku_padding_does_not_recycle_winner(tmp_path):
    """URL encoders may add outer whitespace to a SKU, not a new product."""
    path = tmp_path / "sku-whitespace.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    original = identity_keys(
        "Compact Blue Desk Lamp",
        "https://seller.example/offer?sku=AB-12&ref=first",
    )
    padded = identity_keys(
        "Rechargeable LED Reading Light",
        "https://seller.example/offer?SKU=%20AB-12%20&ttclid=other",
    )
    assert history.claim(original)
    assert not WinnerHistory("returning-browser", path=path).claim(padded), (
        "Padding around the same merchant SKU cannot reset permanent history"
    )
    assert history.claim(identity_keys(
        "Large Reading Light",
        "https://seller.example/offer?sku=AB-13",
    )), "Different SKUs must remain eligible"
    assert history.claim(identity_keys(
        "Spaced Model Light",
        "https://seller.example/offer?sku=AB%2012",
    )), "Internal SKU spaces are meaningful and must not be removed"


def test_shopify_variant_padding_does_not_recycle_winner(tmp_path):
    """Whitespace around a numeric Shopify variant cannot hide its stable ID."""
    path = tmp_path / "shopify-whitespace.sqlite3"
    history = WinnerHistory("returning-browser", path=path)
    original = identity_keys(
        "Blue Compact Drawer Divider",
        "https://seller.example/products/blue-divider?variant=44332211009988",
    )
    relisted = identity_keys(
        "Expandable Storage Organizer",
        "https://seller.example/en-us/products/storage-organizer"
        "?Variant=%2044332211009988%20&utm_source=pin",
    )
    assert history.claim(original)
    assert not WinnerHistory("returning-browser", path=path).claim(relisted), (
        "Whitespace-padded Shopify variant must not reset permanent history"
    )
    assert history.claim(identity_keys(
        "Different Green Organizer",
        "https://seller.example/products/green-organizer?variant=44332211009989",
    )), "Different Shopify variants must remain eligible"
    assert history.claim(identity_keys(
        "Other Merchant Organizer",
        "https://other.example/products/organizer?variant=44332211009988",
    )), "The same variant on another merchant is not the same SKU"


def test_target_preselect_padded_child_cannot_recycle_relisted_winner(tmp_path):
    """A Target child TCIN survives URL-encoded padding and renamed parent pages."""
    path = tmp_path / "target-preselect.sqlite3"
    original = identity_keys(
        "Blue Compact Drawer Organizer",
        "https://www.target.com/p/blue-divider/-/A-91234567?preselect=91234569",
    )
    relisted = identity_keys(
        "Expandable Storage Organizer",
        "https://target.com/p/-/A-91234567?PRESELECT=%2091234569%20&utm_source=pin",
    )
    assert WinnerHistory("returning-browser", path=path).claim(original)
    assert not WinnerHistory("returning-browser", path=path).claim(relisted), (
        "Padding around a Target child TCIN must not reset permanent winner history"
    )
    assert WinnerHistory("returning-browser", path=path).claim(identity_keys(
        "Different Green Storage Organizer",
        "https://target.com/p/-/A-91234567?preselect=91234570",
    )), "Different Target child variants must remain eligible"


def test_target_conflicting_preselect_values_do_not_claim_arbitrary_child():
    """Conflicting child selectors are ambiguous, not permission to claim first."""
    keys = identity_keys(
        "Ambiguous Target Child Listing",
        "https://target.com/p/-/A-91234567?preselect=91234569&PRESELECT=91234570",
    )
    assert not any(key.startswith("target-tcin-") for key in keys), (
        "Never choose an arbitrary child TCIN or parent for conflicting preselect values"
    )


def test_shopify_mobile_storefront_alias_cannot_recycle_numeric_variant(tmp_path):
    """A merchant's m. mobile link must share its stable Shopify variant history."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    original = identity_keys(
        'Original Bamboo Drawer Organizer Small',
        'https://shop.example/en-us/products/organizer?variant=44332211009988&utm_source=pin',
    )
    relisted = identity_keys(
        'Updated Office Storage Rack Compact',
        'https://m.shop.example/fr-ca/products/storage-rack?VARIANT=44332211009988&gad_source=1',
    )
    assert history.claim(original)
    assert not WinnerHistory('returning-browser', path=history.path).claim(relisted)


def test_shopify_mobile_alias_preserves_other_merchants_and_variants(tmp_path):
    """Mobile aliases cannot combine different numeric variants or stores."""
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    assert history.claim(identity_keys(
        'Compact Organizer Model A',
        'https://shop.example/products/organizer?variant=44332211009988',
    ))
    assert history.claim(identity_keys(
        'Large Organizer Model B',
        'https://m.shop.example/products/organizer?variant=44332211009989',
    ))
    assert history.claim(identity_keys(
        'Other Merchant Organizer Model A',
        'https://m.other.example/products/organizer?variant=44332211009988',
    ))


def test_preupgrade_mobile_shopify_variant_hash_still_blocks_desktop(tmp_path):
    """Hash-only mobile history from earlier releases must survive the alias fix."""
    import hashlib
    history = WinnerHistory('returning-browser', path=tmp_path / 'winners.db')
    old_mobile = 'shopify-variant-' + hashlib.sha256(
        'm.shop.example:44332211009988'.encode()
    ).hexdigest()[:24]
    assert history.claim([old_mobile])
    assert not WinnerHistory('returning-browser', path=history.path).claim(identity_keys(
        'Renamed Compact Office Storage Product',
        'https://shop.example/products/new-slug?variant=44332211009988',
    ))
