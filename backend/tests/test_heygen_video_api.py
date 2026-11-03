"""Offline tests: no HeyGen requests or credits are consumed."""
from unittest.mock import patch, Mock
import pytest
from fastapi import HTTPException

from backend.heygen_video_api import configured, product_prompt, provider_request, compose_heygen_narration, verified_listing_image


def test_provider_defaults_off(monkeypatch):
    monkeypatch.delenv("TRAFFICLIFT_HEYGEN_VIDEO_ENABLED", raising=False)
    monkeypatch.delenv("HEYGEN_API_KEY", raising=False)
    assert configured() is False


def test_provider_requires_both_settings(monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_HEYGEN_VIDEO_ENABLED", "1")
    monkeypatch.delenv("HEYGEN_API_KEY", raising=False)
    assert configured() is False
    monkeypatch.setenv("HEYGEN_API_KEY", "test-key-only")
    assert configured() is True


def test_concise_heygen_ad_uses_seller_facts_once_and_ends_with_cta():
    plan = {"evidence": [
        {"text": "Three adjustable drawers"},
        {"text": "A removable divider keeps small pieces separate"}
    ]}
    spoken = compose_heygen_narration("Smart+, Desk Drawer Organizer, Black - Walmart.com", plan)
    assert spoken.startswith("Take a look at Smart+, Desk Drawer Organizer.")
    assert "Three adjustable drawers." in spoken
    assert "A removable divider keeps small pieces separate." in spoken
    assert spoken.endswith("Tap the product link to visit the seller's page for current price and details.")
    assert spoken.count("Tap the product link") == 1
    assert len(spoken.split()) <= 34


def test_prompt_has_single_cta_and_stable_framing():
    plan = {"evidence": [{"text": "Three adjustable drawers"}, {"text": "Removable divider for smaller items"}]}
    result = product_prompt("Smart Desk Organizer", plan)
    assert "no strobing or flicker" in result
    assert "Speak exactly the following once" in result
    assert "Tap the product link to visit the seller's page for current price and details." in result
    assert "no invented features" in result.lower()


def test_product_lock_prompt_requires_full_frame_and_cta_hero_shot():
    plan = {"evidence": [
        {"text": "Adjustable neck for positioning"},
        {"text": "Includes an on/off switch"}
    ]}
    prompt = product_prompt("Banker Desk Lamp", plan)
    assert "PRODUCT IDENTITY LOCK:" in prompt
    assert "SCENE SEPARATION:" in prompt
    assert "NO packaging photo visible over the demonstration" in prompt
    assert "keep the organizer and hands unobstructed" in prompt
    assert "No aggressive zoom" in prompt
    assert "final call to action" in prompt
    assert "do not bring back the large retail package" in prompt
    assert "Speak exactly the following once" in prompt


def test_long_listing_facts_fail_before_paid_generation():
    plan = {"evidence": [{"text": "Durable plastic material is made to handle assorted household objects in a wide variety of setups across many different household rooms"}]}
    with pytest.raises(HTTPException) as err:
        compose_heygen_narration("Organizer", plan, max_words=28)
    assert err.value.status_code == 422
    assert "no paid video was started" in err.value.detail


def test_no_facts_refuses_narration():
    with pytest.raises(HTTPException) as err:
        product_prompt("Organizer", {"phrases": ["Repeat the title", "Repeat the title"]})
    assert err.value.status_code == 422


def test_provider_error_does_not_expose_key(monkeypatch):
    monkeypatch.setenv("HEYGEN_API_KEY", "top-secret-test-key")
    response = Mock()
    response.raise_for_status.side_effect = __import__("requests").HTTPError("top-secret-test-key")
    with patch("backend.heygen_video_api.requests.request", return_value=response):
        with pytest.raises(HTTPException) as err:
            provider_request("POST", "https://api.heygen.com/v3/models/videos", json={})
    assert err.value.status_code == 502
    assert "top-secret-test-key" not in err.value.detail


def test_paid_heygen_requires_verified_seller_photo():
    with pytest.raises(HTTPException) as exc:
        verified_listing_image([])
    assert exc.value.status_code == 422
    assert "no paid video was started" in exc.value.detail
    assert verified_listing_image([
        "https://www.target.com/images/products/actual-lamp.jpg"
    ]) == "https://www.target.com/images/products/actual-lamp.jpg"


def test_heygen_ad_prompt_has_readable_cta_and_no_fabricated_demo():
    plan = {"evidence": [{"text": "Includes a six-foot cord"}, {"text": "Adjustable neck for positioning"}]}
    prompt = product_prompt("Desk Light", plan)
    assert "VISIT PRODUCT LINK" in prompt
    assert "high-contrast, readable call to action" in prompt
    assert "never invent" in prompt
    assert "clean full-frame view" in prompt


def test_heygen_submit_uses_listing_image_not_stale_browser_image(monkeypatch):
    from backend.heygen_video_api import heygen_submit, HeyGenSubmit
    from backend.photo_video_api import VideoRequest
    listing = "https://target.scene7.com/is/image/Target/verified-lamp"
    stale = "https://target.scene7.com/is/image/Target/stale-piano-lamp"
    product = VideoRequest(name="Classic Desk Light", image_url=stale,
                           product_url="https://www.target.com/p/classic-desk-light",
                           benefit="Includes a six-foot cord")
    body = HeyGenSubmit(product=product, approve_paid_generation=True)
    plan = {"evidence": [{"text": "Includes a six-foot cord"},
                         {"text": "Adjustable neck for positioning"}]}
    with patch("backend.heygen_video_api.video_guard"), \
         patch("backend.heygen_video_api.configured", return_value=True), \
         patch("backend.heygen_video_api.prepare_video_plan", return_value=(plan, [listing])), \
         patch("backend.heygen_video_api.marketing_readiness_gate"), \
         patch("backend.heygen_video_api.provider_request", return_value={"video_id": "sample_valid_job_123"}) as send:
        result = heygen_submit(body, Mock())
    assert result["status"] == "submitted"
    assert send.call_args.kwargs["json"]["image"]["url"] == listing


def test_heygen_submit_aborts_before_paid_call_if_image_unverified():
    from backend.heygen_video_api import heygen_submit, HeyGenSubmit
    from backend.photo_video_api import VideoRequest
    product = VideoRequest(name="Desk Light",
                           image_url="https://target.scene7.com/is/image/Target/unknown-product",
                           product_url="https://www.target.com/p/desk-light")
    body = HeyGenSubmit(product=product, approve_paid_generation=True)
    plan = {"evidence": [{"text": "Includes a six-foot cord"},
                         {"text": "Adjustable neck for positioning"}]}
    with patch("backend.heygen_video_api.video_guard"), \
         patch("backend.heygen_video_api.configured", return_value=True), \
         patch("backend.heygen_video_api.prepare_video_plan", return_value=(plan, [])), \
         patch("backend.heygen_video_api.marketing_readiness_gate"), \
         patch("backend.heygen_video_api.provider_request") as send:
        with pytest.raises(HTTPException) as exc:
            heygen_submit(body, Mock())
    assert exc.value.status_code == 422
    send.assert_not_called()


def test_heygen_prompt_prevents_shake_and_unreadable_package_reconstruction():
    plan = {"evidence": [
        {"text": "Three adjustable drawers"},
        {"text": "Expandable storage tiers"},
    ]}
    prompt = product_prompt("Smart Drawer Organizer", plan)
    assert "PRODUCTION CAMERA LOCK" in prompt
    assert "no handheld shake" in prompt
    assert "frame jitter" in prompt
    assert "PACKAGING TEXT LOCK" in prompt
    assert "without re-drawing" in prompt
    assert "Avoid close-up shots of tiny printed packaging text" in prompt
    assert "rather than fabricating product mechanics" in prompt
    assert "Speak exactly the following once" in prompt


def test_heygen_reference_photo_never_overlays_product_demo():
    plan = {"evidence": [{"text": "Three adjustable drawers"},
                         {"text": "Expandable drawer storage"}]}
    prompt = product_prompt("Smart Drawer Organizer", plan)
    assert "0-2 second opening" in prompt
    assert "clean, complete CUT" in prompt
    assert "No crossfade of the box into" in prompt
    assert "picture-in-picture" in prompt
    assert "Do not keep the packaging stationary while the background moves" in prompt
    assert "full-frame product" in prompt


def test_spoken_product_category_is_not_repeated_in_consecutive_sentences():
    plan = {"evidence": [
        {"text": "This desk organizer includes three adjustable tiers"},
        {"text": "The desk organizer offers expandable storage"},
    ]}
    spoken = compose_heygen_narration("Smart Desk Organizer", plan)
    assert spoken.startswith("Take a closer look.")
    assert spoken.count("desk organizer") == 0 or spoken.lower().count("desk organizer") <= 2
    assert spoken.endswith("Tap the product link to visit the seller's page for current price and details.")


def test_narration_prioritizes_distinct_feature_before_repeated_title():
    plan = {"evidence": [
        {"text": "This desk organizer has three adjustable tiers"},
        {"text": "Includes removable dividers"},
    ]}
    spoken = compose_heygen_narration("Smart Desk Organizer", plan)
    assert spoken.startswith("Take a look at Smart Desk Organizer.")
    assert "Includes removable dividers." in spoken
    assert spoken.index("Includes removable dividers.") < spoken.index("This desk organizer has three adjustable tiers.")


def test_heygen_cta_directs_to_seller_product_page_without_on_video_price():
    plan = {"evidence": [
        {"text": "Expandable storage tiers"},
        {"text": "Includes removable dividers"},
    ]}
    speech = compose_heygen_narration("Smart Drawer Organizer", plan)
    prompt = product_prompt("Smart Drawer Organizer", plan)
    assert speech.endswith("Tap the product link to visit the seller's page for current price and details.")
    assert speech.count("Tap the product link") == 1
    assert "VISIT PRODUCT LINK" in prompt
    assert "See Price & Details" not in prompt
