"""Growth Strategy mode request validation and channel isolation."""
import pytest
from pydantic import ValidationError
from trafficlift_pro import GenerateRequest


BASE = {
    "product_payload": {"name": "Sample product", "image_url": "https://example.com/image.jpg"},
    "input_url": "https://example.com/product",
    "daily_budget": 25,
}


@pytest.mark.parametrize("mode,channels", [
    ("organic", ["pinterest"]),
    ("organic", ["tiktok_organic", "youtube_shorts"]),
    ("paid", ["meta_ads"]),
    ("paid", ["meta_ads", "tiktok_ads"]),
])
def test_valid_growth_strategy_combinations(mode, channels):
    req = GenerateRequest(**BASE, mode=mode, target_channels=channels)
    assert req.mode == mode
    assert req.target_channels == channels


@pytest.mark.parametrize("mode,channels", [
    ("organic", ["meta_ads"]),
    ("paid", ["pinterest"]),
    ("paid", ["tiktok_organic", "tiktok_ads"]),
    ("organic", ["pinterest", "pinterest"]),
])
def test_growth_strategy_rejects_wrong_or_duplicate_channels(mode, channels):
    with pytest.raises(ValidationError):
        GenerateRequest(**BASE, mode=mode, target_channels=channels)
