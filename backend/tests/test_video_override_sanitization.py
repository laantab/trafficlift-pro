from unittest.mock import Mock, patch

from backend.photo_video_api import VideoRequest, prepare_video_plan
from backend.heygen_video_api import HeyGenSubmit, heygen_submit

NAME = "Silicone Mats for Baking Food-Grade Non-Stick Leakproof Cooking"
URL = "https://www.amazon.com/Silicone-Food-Grade-Non-Stick-Leakproof-Cooking/dp/B0HJ8LLXVL"
IMAGE = "https://m.media-amazon.com/images/I/61SS+778qxL._AC_SL1500_.jpg"


def _payload(**overrides):
    data = dict(
        name=NAME,
        image_url=IMAGE,
        product_url=URL,
        image_source=URL,
        product_identity_verified=True,
        benefit="Non-stick silicone baking surface | Read more",
        fact_sources=[{
            "title": NAME,
            "url": URL,
            "snippet": "Shop Generic at the Amazon Bakeware store... Free Shipping on eligible items.",
        }],
        buyer_need="",
        consideration="",
        seconds=15,
        style="warm",
    )
    data.update(overrides)
    return VideoRequest(**data)


def test_retailer_fragment_override_is_ignored_and_plan_still_builds():
    payload = _payload()
    with patch("backend.photo_video_api.resolve_video_facts", return_value={}):
        plan, photos = prepare_video_plan(payload, require_renderable=True)
    assert plan["mode"] == "product_overview"
    assert plan["evidence"] == []
    assert photos == []


def test_current_product_reaches_mocked_heygen_submit_even_with_stale_fragment_override():
    product = _payload()
    body = HeyGenSubmit(product=product, approve_paid_generation=True)
    with patch("backend.heygen_video_api.video_guard"), \
         patch("backend.heygen_video_api.configured", return_value=True), \
         patch("backend.heygen_video_api.provider_request", return_value={"video_id":"mocked_fragment_fix_123"}) as send:
        result = heygen_submit(body, Mock())
    assert result["status"] == "submitted"
    assert send.called
    assert send.call_args.kwargs["json"]["image"]["url"] == IMAGE


def test_valid_manual_benefit_is_still_preserved():
    payload = _payload(
        benefit="Reusable silicone surface supports repeated baking use",
        fact_sources=[],
    )
    with patch("backend.photo_video_api.resolve_video_facts", return_value={}):
        plan, _ = prepare_video_plan(payload, require_renderable=True)
    # One valid user fact is insufficient for a benefit-led ad, so the safety
    # fallback remains product_overview instead of inventing a second claim.
    assert plan["mode"] == "product_overview"
