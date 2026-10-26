"""Non-Latin product titles must not collapse unrelated permanent winners.

Legacy ASCII-only tokenization discarded Chinese/Arabic letters, causing
unrelated listings to share a generic name hash. The new ledger must preserve
meaningful Unicode letters without changing ASCII title behavior.
"""
from backend.winner_history import WinnerHistory, identity_keys


def test_distinct_chinese_titles_are_independent_winners(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "history.sqlite3")
    assert history.claim(identity_keys("竹制抽屉收纳盒", "https://seller-a.example/item/one"))
    assert history.claim(identity_keys("陶瓷咖啡杯", "https://seller-b.example/item/two"))


def test_same_chinese_title_stays_excluded_when_url_changes(tmp_path):
    path = tmp_path / "history.sqlite3"
    assert WinnerHistory("returning-browser", path=path).claim(
        identity_keys("竹制抽屉收纳盒", "https://seller-a.example/item/old?utm_source=ad"))
    assert not WinnerHistory("returning-browser", path=path).claim(
        identity_keys("竹制抽屉收纳盒", "https://seller-b.example/item/new?utm_source=pin"))


def test_non_latin_brand_word_distinguishes_similar_english_titles(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "history.sqlite3")
    assert history.claim(identity_keys("竹制 Drawer Organizer", "https://seller-a.example/one"))
    assert history.claim(identity_keys("陶瓷 Drawer Organizer", "https://seller-b.example/two"))
    assert not history.claim(identity_keys("竹制 Drawer Organizer", "https://seller-c.example/three"))


def test_distinct_arabic_titles_are_not_one_empty_ascii_hash(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "history.sqlite3")
    assert history.claim(identity_keys("مصباح مكتب", "https://shop-a.example/one"))
    assert history.claim(identity_keys("حامل هاتف", "https://shop-b.example/two"))


def test_ascii_title_equivalence_and_quantities_still_work(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "history.sqlite3")
    assert history.claim(identity_keys("18 Piece Drawer Organizer Set", "https://shop-a.example/one"))
    assert not history.claim(identity_keys("Drawer Organiser Set 18pcs", "https://shop-b.example/two"))
    assert history.claim(identity_keys("Drawer Organiser Set 20pcs", "https://shop-c.example/three"))
