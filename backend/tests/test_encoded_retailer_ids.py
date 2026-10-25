"""Encoded unreserved path bytes must not bypass merchant-specific winner IDs."""
from backend.winner_history import WinnerHistory, identity_keys


def test_encoded_shopify_route_relisting_retains_variant_identity(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    first = identity_keys(
        "Copper Pour Over Kettle",
        "https://seller.example/products/copper-kettle?variant=12345678901234",
    )
    relisted = identity_keys(
        "Electric Water Heating Vessel",
        "https://seller.example/%70roducts/electric-vessel?variant=12345678901234&utm_source=ad",
    )
    distinct = identity_keys(
        "Ceramic Brewing Vessel",
        "https://seller.example/%70roducts/ceramic-vessel?variant=12345678901235",
    )
    assert history.claim(first)
    assert not WinnerHistory("returning-browser", path=history.path).claim(relisted)
    assert history.claim(distinct)


def test_encoded_amazon_route_does_not_recycle_same_asin(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    assert history.claim(identity_keys(
        "Portable Reading Light",
        "https://amazon.com/gp/product/B08EXAMP12",
    ))
    assert not history.claim(identity_keys(
        "USB Desk Illumination Device",
        "https://www.amazon.com/d%70/B08EXAMP12?tag=another",
    ))


def test_encoded_walmart_route_does_not_recycle_same_item(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    assert history.claim(identity_keys(
        "Metal Closet Shelf",
        "https://walmart.com/ip/Shelf/123456789",
    ))
    assert not history.claim(identity_keys(
        "Adjustable Storage Platform",
        "https://www.walmart.com/i%70/123456789?utm_campaign=other",
    ))


def test_encoded_target_route_does_not_recycle_preselected_child(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    assert history.claim(identity_keys(
        "Bamboo Tray Organizer",
        "https://target.com/p/tray/-/A-12345678?preselect=12345679",
    ))
    assert not history.claim(identity_keys(
        "Natural Wood Divider",
        "https://www.target.com/%70/other/-/A-12345679",
    ))
    assert history.claim(identity_keys(
        "White Plastic Divider",
        "https://target.com/p/other/-/A-12345680",
    ))


def test_encoded_reserved_slash_never_becomes_a_retailer_id(tmp_path):
    history = WinnerHistory("returning-browser", path=tmp_path / "seen.db")
    assert history.claim(identity_keys(
        "Travel Lantern Model A",
        "https://amazon.com/dp/B08EXAMP12",
    ))
    # Encoded slash is a distinct path segment, not the /dp/<ASIN> route.
    assert history.claim(identity_keys(
        "Portable Night Light Model B",
        "https://amazon.com/dp%2FB08EXAMP12",
    ))
