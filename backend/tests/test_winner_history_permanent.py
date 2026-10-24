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
