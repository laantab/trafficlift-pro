"""Offline tests: no HeyGen requests or credits are consumed."""
from unittest.mock import patch, Mock
import pytest
from fastapi import HTTPException

from backend.heygen_video_api import configured, product_prompt, provider_request, compose_heygen_narration, verified_listing_image, trusted_winner_image


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


def test_supported_organizer_facts_use_problem_solution_hook_and_cta():
    plan = {"evidence": [
        {"text": "Three adjustable drawers"},
        {"text": "A removable divider keeps small pieces separate"}
    ]}
    spoken = compose_heygen_narration("Smart+, Desk Drawer Organizer, Black - Walmart.com", plan)
    assert spoken.startswith("Tired of clutter taking over your space?")
    assert "Get things back under control." in spoken
    assert "Take a look at this Smart+, Desk Drawer Organizer." in spoken
    assert spoken.endswith("Tap the product link to learn more.")
    assert spoken.count("Tap the product link") == 1
    assert len(spoken.split()) <= 34


def test_prompt_has_single_cta_and_stable_framing():
    plan = {"evidence": [{"text": "Three adjustable drawers"}, {"text": "Removable divider for smaller items"}]}
    result = product_prompt("Smart Desk Organizer", plan)
    assert "no strobing or flicker" in result
    assert "Speak exactly the following once" in result
    assert "Tap the product link to learn more." in result
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


def test_long_listing_facts_no_longer_block_generation():
    plan = {"evidence": [{"text": "Durable plastic material is made to handle assorted household objects in a wide variety of setups across many different household rooms"}]}
    narration = compose_heygen_narration("Organizer", plan, max_words=28)
    assert narration.startswith("Looking for a simpler solution?")
    assert narration.endswith("Tap the product link to learn more.")
    assert len(narration.split()) <= 28
    assert "made to handle assorted household" not in narration


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
    assert spoken.startswith("Tired of clutter taking over your space?")
    assert spoken.lower().count("desk organizer") <= 1
    assert spoken.endswith("Tap the product link to learn more.")


def test_problem_hook_replaces_fact_dump_for_supported_organizer():
    plan = {"evidence": [
        {"text": "This desk organizer has three adjustable tiers"},
        {"text": "Includes removable dividers"},
    ]}
    spoken = compose_heygen_narration("Smart Desk Organizer", plan)
    assert spoken.startswith("Tired of clutter taking over your space?")
    assert "Get things back under control." in spoken
    assert "Take a look at this Smart Desk Organizer." in spoken
    assert "adjustable tiers" not in spoken.lower()
    assert "removable dividers" not in spoken.lower()


def test_heygen_cta_directs_to_seller_product_page_without_on_video_price():
    plan = {"evidence": [
        {"text": "Expandable storage tiers"},
        {"text": "Includes removable dividers"},
    ]}
    speech = compose_heygen_narration("Smart Drawer Organizer", plan)
    prompt = product_prompt("Smart Drawer Organizer", plan)
    assert speech.endswith("Tap the product link to learn more.")
    assert speech.count("Tap the product link") == 1
    assert "VISIT PRODUCT LINK" in prompt
    assert "See Price & Details" not in prompt


def test_long_seller_paragraph_extracts_whole_short_clauses():
    from backend.heygen_video_api import _short_seller_facts
    paragraph = ("Designed for a range of household uses and storage needs across many rooms. "
                 "Features three adjustable drawers. Includes removable dividers, "
                 "easy access to everyday items, and many other long specifications.")
    clauses = _short_seller_facts(paragraph)
    assert "Features three adjustable drawers." in clauses
    assert "Includes removable dividers." in clauses
    spoken = compose_heygen_narration("Smart Desk Organizer",
                                     {"evidence": [{"text": paragraph}]})
    assert spoken.startswith("Tired of clutter taking over your space?")
    assert "Tap the product link" in spoken
    assert len(spoken.split()) <= 34


def test_missing_evidence_never_submits_unverified_narration():
    with pytest.raises(HTTPException) as exc:
        compose_heygen_narration("Desk Organizer", {"evidence": []})
    assert exc.value.status_code == 422


def test_product_overview_uses_supported_nonstick_problem_hook():
    plan = {"mode": "product_overview", "evidence": []}
    spoken = compose_heygen_narration(
        "Silicone Mats for Baking Food-Grade Non-Stick Leakproof Cooking",
        plan,
    )
    assert spoken.startswith("Tired of scraping your baking pan after every batch?")
    assert "Kiss those days goodbye." in spoken
    assert "Take a look at this silicone baking mat." in spoken
    assert spoken.endswith("Tap the product link to learn more.")
    assert len(spoken.split()) <= 34


def test_verified_winner_image_can_replace_failed_rediscovery():
    from backend.photo_video_api import VideoRequest
    seller = (
        "https://www.amazon.com/Silicone-Food-Grade-Non-Stick-Leakproof-Cooking/"
        "dp/B0HJ8LLXVL"
    )
    image = "https://m.media-amazon.com/images/I/61SS+778qxL._AC_SL1500_.jpg"
    product = VideoRequest(
        name="Silicone Mats for Baking Food-Grade Non-Stick Leakproof Cooking",
        image_url=image,
        product_url=seller,
        image_source=seller + "?th=1",
        product_identity_verified=True,
    )
    trusted = trusted_winner_image(product)
    assert trusted == image
    assert verified_listing_image([], trusted_image=trusted) == image


def test_verified_winner_image_rejects_mismatched_listing_source():
    from backend.photo_video_api import VideoRequest
    image = "https://m.media-amazon.com/images/I/61SS+778qxL._AC_SL1500_.jpg"
    product = VideoRequest(
        name="Silicone Mats for Baking",
        image_url=image,
        product_url="https://www.amazon.com/dp/B0HJ8LLXVL",
        image_source="https://www.amazon.com/dp/B0DIFFERENT",
        product_identity_verified=True,
    )
    assert trusted_winner_image(product) is None
    with pytest.raises(HTTPException):
        verified_listing_image([], trusted_image=trusted_winner_image(product))


def test_heygen_submit_uses_verified_winner_image_when_listing_rediscovery_is_empty():
    from backend.heygen_video_api import heygen_submit, HeyGenSubmit
    from backend.photo_video_api import VideoRequest
    seller = "https://www.amazon.com/dp/B0HJ8LLXVL"
    image = "https://m.media-amazon.com/images/I/61SS+778qxL._AC_SL1500_.jpg"
    product = VideoRequest(
        name="Silicone Mats for Baking Food-Grade Non-Stick Leakproof Cooking",
        image_url=image,
        product_url=seller,
        image_source=seller,
        product_identity_verified=True,
    )
    body = HeyGenSubmit(product=product, approve_paid_generation=True)
    plan = {"mode": "product_overview", "evidence": []}
    with patch("backend.heygen_video_api.video_guard"), \
         patch("backend.heygen_video_api.configured", return_value=True), \
         patch("backend.heygen_video_api.prepare_video_plan", return_value=(plan, [])), \
         patch("backend.heygen_video_api.marketing_readiness_gate"), \
         patch("backend.heygen_video_api.provider_request", return_value={"video_id":"sample_valid_job_456"}) as send:
        result = heygen_submit(body, Mock())
    assert result["status"] == "submitted"
    assert send.call_args.kwargs["json"]["image"]["url"] == image


def test_long_amazon_title_uses_problem_hook_and_compact_product_reference():
    name = (
        "Generic Silicone Baking Mat, Food Grade Non Stick Heat Resistant Pastry Sheet, "
        "Reusable Food Grade Silicone Baking Mat With Non Stick For Bread"
    )
    plan = {"mode": "product_overview", "evidence": []}
    spoken = compose_heygen_narration(name, plan)
    assert spoken.startswith("Tired of scraping your baking pan after every batch?")
    assert "Kiss those days goodbye." in spoken
    assert "Take a look at this silicone baking mat." in spoken
    assert "Food Grade Non Stick Heat Resistant Pastry Sheet" not in spoken
    assert spoken.endswith("Tap the product link to learn more.")
    assert len(spoken.split()) <= 34


def test_compact_spoken_identity_preserves_model_identifier_from_later_title_text():
    from backend.heygen_video_api import _spoken_identity
    name = (
        "Acme Adjustable Desk Lamp, Brushed Nickel Home Office Lighting, "
        "Model DLX9000"
    )
    spoken_identity = _spoken_identity(name)
    assert spoken_identity.startswith("Acme Adjustable Desk Lamp")
    assert "DLX9000" in spoken_identity
    assert len(spoken_identity.split()) <= 10


def test_baking_mat_prompt_shows_actual_chicken_removal_without_clipping():
    prompt = product_prompt(
        "Silicone Baking Mat Non Stick Pastry Sheet",
        {"mode": "product_overview", "evidence": []},
    )
    assert "PHYSICAL CONTACT LOCK:" in prompt
    assert "BAKING MAT DEMONSTRATION SEQUENCE:" in prompt
    assert "cooked chicken resting naturally on the flat silicone mat" in prompt
    assert "one continuous, clearly visible removal shot" in prompt
    assert "lift one piece of cooked chicken upward and completely clear of the mat" in prompt
    assert "small visible gap between the chicken and mat after release" in prompt
    assert "Do NOT jump directly from food-on-mat to an empty mat" in prompt
    assert "FLEXIBLE MAT CONTACT LOCK:" in prompt
    assert "Never lift, fold, bend, curl, or pull the mat through food" in prompt
    assert "melt through" in prompt
    assert "Only after the food-removal demonstration is complete" in prompt
    assert "Never show food floating unsupported" in prompt


def test_non_mat_product_gets_general_physics_lock_without_mat_specific_direction():
    prompt = product_prompt(
        "Smart Desk Organizer",
        {"evidence": [{"text": "Three adjustable drawers"}]},
    )
    assert "PHYSICAL CONTACT LOCK:" in prompt
    assert "No object may pass through" in prompt
    assert "FLEXIBLE MAT CONTACT LOCK:" not in prompt


def test_verified_amazon_image_accepts_same_asin_in_titled_and_dp_urls():
    from backend.photo_video_api import VideoRequest
    product = VideoRequest(
        name="Silicone Baking Mat",
        image_url="https://m.media-amazon.com/images/I/mat.jpg",
        product_url="https://www.amazon.com/Silicone-Mat/dp/B0HJ8LLXVL",
        image_source="https://www.amazon.com/dp/B0HJ8LLXVL?th=1",
        product_identity_verified=True,
    )
    assert trusted_winner_image(product) == product.image_url


def test_verified_amazon_image_rejects_lookalike_and_missing_identity():
    from backend.photo_video_api import VideoRequest
    baseline = dict(
        name="Silicone Baking Mat",
        image_url="https://m.media-amazon.com/images/I/mat.jpg",
        product_url="https://www.amazon.com/Silicone-Mat/dp/B0HJ8LLXVL",
        image_source="https://www.amazon.com/dp/B012345678",
        product_identity_verified=True,
    )
    assert trusted_winner_image(VideoRequest(**baseline)) is None
    assert trusted_winner_image(VideoRequest(**{**baseline, "image_source": "https://www.amazon.com/Silicone-Mat"})) is None
    assert trusted_winner_image(VideoRequest(**{**baseline, "image_source": baseline["product_url"], "product_identity_verified": False})) is None


def test_heygen_image_handoff_does_not_depend_on_optional_image_status_label():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "index.html").read_text(encoding="utf-8")
    assert "product_identity_verified: winner.product_identity_verified === true," in html
    assert "winner.product_identity_verified === true && winner.image_status === 'verified'" not in html


def test_exact_asin_photo_recovery_allows_unverified_discovery_image_to_be_replaced():
    from backend.heygen_video_api import exact_seller_photo_fallback
    from backend.photo_video_api import VideoRequest
    seller = "https://www.amazon.com/Silicone-Baking-Mat/dp/B0HJ8LLXVL"
    correct = "https://m.media-amazon.com/images/I/true-exact-mat.jpg"
    product = VideoRequest(
        name="Silicone Baking Mat",
        product_url=seller,
        image_url="https://m.media-amazon.com/images/I/unproven-thumbnail.jpg",
        product_identity_verified=False,
    )
    record = {"asin": "B0HJ8LLXVL", "image_url": correct,
              "name": "Silicone Baking Mat", "final_url": "https://www.amazon.com/dp/B0HJ8LLXVL"}
    with patch("backend.product_url_finder_v2.resolve_product_identity", return_value=record) as resolve, \
         patch("local_video.trafficlift_video_bridge._validate_public_https_url"):
        assert exact_seller_photo_fallback(product) == correct
    resolve.assert_called_once_with(seller)


def test_exact_asin_photo_recovery_rejects_mismatched_asin():
    from backend.heygen_video_api import exact_seller_photo_fallback
    from backend.photo_video_api import VideoRequest
    product = VideoRequest(
        name="Silicone Baking Mat",
        product_url="https://www.amazon.com/dp/B0HJ8LLXVL",
        image_url="https://m.media-amazon.com/images/I/unproven-thumbnail.jpg",
    )
    record = {"asin": "B012345678",
              "image_url": "https://m.media-amazon.com/images/I/wrong-product.jpg"}
    with patch("backend.product_url_finder_v2.resolve_product_identity", return_value=record):
        assert exact_seller_photo_fallback(product) is None


def test_exact_asin_photo_recovery_does_not_guess_short_links_or_retailer_pages():
    from backend.heygen_video_api import exact_seller_photo_fallback
    from backend.photo_video_api import VideoRequest
    with patch("backend.product_url_finder_v2.resolve_product_identity") as resolve:
        for url in ("https://link.amazon/B03bHi8EM", "https://www.amazon.com/s?k=baking+mat",
                    "https://seller.example/product"):
            product = VideoRequest.model_construct(name="Silicone Mat", product_url=url, image_url="https://example.com/image.jpg")
            assert exact_seller_photo_fallback(product) is None
        resolve.assert_not_called()


def test_heygen_submit_uses_exact_seller_fallback_without_paying_for_other_product():
    from backend.heygen_video_api import heygen_submit, HeyGenSubmit
    from backend.photo_video_api import VideoRequest
    product = VideoRequest(name="Silicone Baking Mat",
                           product_url="https://www.amazon.com/dp/B0HJ8LLXVL",
                           image_url="https://m.media-amazon.com/images/I/unproven-thumbnail.jpg",
                           product_identity_verified=False)
    plan = {"mode": "product_overview", "evidence": []}
    correct = "https://m.media-amazon.com/images/I/verified-seller-photo.jpg"
    with patch("backend.heygen_video_api.video_guard"), \
         patch("backend.heygen_video_api.configured", return_value=True), \
         patch("backend.heygen_video_api.prepare_video_plan", return_value=(plan, [])), \
         patch("backend.heygen_video_api.marketing_readiness_gate"), \
         patch("backend.heygen_video_api.exact_seller_photo_fallback", return_value=correct) as fallback, \
         patch("backend.heygen_video_api.provider_request", return_value={"video_id": "sample_valid_job_987"}) as send:
        result = heygen_submit(HeyGenSubmit(product=product, approve_paid_generation=True), Mock())
    assert result["status"] == "submitted"
    fallback.assert_called_once()
    assert send.call_args.kwargs["json"]["image"]["url"] == correct


def test_heygen_recovery_failure_does_not_start_paid_request():
    from backend.heygen_video_api import heygen_submit, HeyGenSubmit
    from backend.photo_video_api import VideoRequest
    product = VideoRequest(name="Silicone Baking Mat",
                           product_url="https://www.amazon.com/dp/B0HJ8LLXVL",
                           image_url="https://m.media-amazon.com/images/I/unproven-thumbnail.jpg")
    with patch("backend.heygen_video_api.video_guard"), \
         patch("backend.heygen_video_api.configured", return_value=True), \
         patch("backend.heygen_video_api.prepare_video_plan", return_value=({"mode": "product_overview"}, [])), \
         patch("backend.heygen_video_api.marketing_readiness_gate"), \
         patch("backend.heygen_video_api.exact_seller_photo_fallback", return_value=None), \
         patch("backend.heygen_video_api.provider_request") as paid:
        with pytest.raises(HTTPException) as err:
            heygen_submit(HeyGenSubmit(product=product, approve_paid_generation=True), Mock())
    assert err.value.status_code == 422
    paid.assert_not_called()
