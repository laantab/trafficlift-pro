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
