from unittest.mock import Mock, patch

from backend.photo_video_api import VideoRequest, _safe_video_product_name, prepare_video_plan
from backend.heygen_video_api import HeyGenSubmit, heygen_submit

URL = "https://www.amazon.com/dp/B0HJ8LLXVL"
IMAGE = "https://m.media-amazon.com/images/I/61SS+778qxL._AC_SL1500_.jpg"
STALE_NAME = (
    "Silicone Mats for Baking Food-Grade Non-Stick Leakproof Cooking | "
    "Shop Amazon Bakeware... Read more"
)


def test_saved_product_name_drops_retailer_fragments_before_fact_checks():
    safe = _safe_video_product_name(STALE_NAME)
    assert "|" not in safe
    assert "..." not in safe
    assert "read more" not in safe.lower()
    assert safe.startswith("Silicone Mats for Baking")


def test_stale_saved_name_and_fact_fragment_fall_back_to_safe_overview():
    payload = VideoRequest(
        name=STALE_NAME[:90],
        image_url=IMAGE,
        product_url=URL,
        image_source=URL,
        product_identity_verified=True,
        benefit="Non-stick silicone baking surface | Read more",
        seconds=15,
        style="warm",
    )
    with patch("backend.photo_video_api.resolve_video_facts", return_value={}):
        plan, photos = prepare_video_plan(payload, require_renderable=True)
    assert plan["mode"] == "product_overview"
    assert "|" not in plan["product"]
    assert "read more" not in plan["product"].lower()
    assert photos == []


def test_stale_saved_name_reaches_mocked_heygen_submit_without_paid_call():
    payload = VideoRequest(
        name=STALE_NAME[:90],
        image_url=IMAGE,
        product_url=URL,
        image_source=URL,
        product_identity_verified=True,
        benefit="Non-stick silicone baking surface | Read more",
        seconds=15,
        style="warm",
    )
    body = HeyGenSubmit(product=payload, approve_paid_generation=True)
    with patch("backend.heygen_video_api.video_guard"), \
         patch("backend.heygen_video_api.configured", return_value=True), \
         patch("backend.photo_video_api.resolve_video_facts", return_value={}), \
         patch("backend.heygen_video_api.provider_request", return_value={"video_id":"mocked_identity_fix_123"}) as send:
        result = heygen_submit(body, Mock())
    assert result["status"] == "submitted"
    assert send.called
    provider_payload = send.call_args.kwargs["json"]
    assert "|" not in provider_payload["prompt"]
    assert "read more" not in provider_payload["prompt"].lower()
