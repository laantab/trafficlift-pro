from unittest.mock import patch

from backend.product_facts import _safe_listing_fact, facts_from_sources, resolve_video_facts


def test_safe_listing_fact_discards_retailer_fragment():
    assert _safe_listing_fact(
        "Non-stick silicone baking surface | Read more",
        "Silicone Mats for Baking",
    ) == ""


def test_exact_listing_retailer_fragment_downgrades_to_no_facts():
    def fake_listing_images(name, url, timeout=4, details=None):
        details.update({
            "seller_facts": [
                "Non-stick silicone baking surface | Read more",
                "Shop Generic at the Amazon Bakeware store...",
            ],
            "seller_benefit_source": url,
        })
        return ["https://m.media-amazon.com/images/I/61SS+778qxL._AC_SL1500_.jpg"]

    with patch("backend.product_facts.listing_images", side_effect=fake_listing_images), \
         patch("backend.live_research._is_tavily_configured", return_value=False):
        result = resolve_video_facts(
            "Silicone Mats for Baking Food-Grade Non-Stick Leakproof Cooking",
            "https://www.amazon.com/dp/B0HJ8LLXVL",
        )

    assert result == {}


def test_indexed_source_retailer_fragment_is_not_promoted_to_evidence():
    destination = "https://www.amazon.com/dp/B0HJ8LLXVL"
    result = facts_from_sources(
        "Silicone Mats for Baking Food-Grade Non-Stick Leakproof Cooking",
        destination,
        [{
            "title": "Silicone Mats for Baking Food-Grade Non-Stick Leakproof Cooking",
            "url": destination,
            "snippet": "Non-stick silicone baking surface | Read more",
            "image_urls": [],
        }],
    )
    assert result == {}
