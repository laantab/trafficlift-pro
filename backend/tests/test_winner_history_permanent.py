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
