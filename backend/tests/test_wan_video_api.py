"""Offline Wan 2.2 trial tests. No fal.ai credentials or paid requests."""
from unittest.mock import Mock, patch
import pytest
from fastapi import HTTPException
from backend.wan_video_api import configured, wan_prompt, readiness, submit, status, WanSubmit
from backend.photo_video_api import VideoRequest


def test_wan_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRAFFICLIFT_WAN_VIDEO_ENABLED", raising=False)
    monkeypatch.delenv("FAL_KEY", raising=False)
    assert configured() is False
    monkeypatch.setenv("FAL_KEY", "dummy-fal-key")
    assert configured() is False
    monkeypatch.setenv("TRAFFICLIFT_WAN_VIDEO_ENABLED", "1")
    assert configured() is True


def test_wan_seller_image_and_product_lock_prompt():
    prompt = wan_prompt("Vintage Desk Light", {"evidence": [{"text": "adjustable shade"}]})
    assert "Vintage Desk Light" in prompt
    assert "entire real product centered" in prompt
    assert "no" in prompt.lower()
    assert "adjustable shade" in prompt


def test_wan_no_paid_submit_without_key(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setenv("TRAFFICLIFT_WAN_VIDEO_ENABLED", "1")
    data = WanSubmit(product=VideoRequest(
        name="Desk Light",
        image_url="https://target.scene7.com/is/image/Target/light",
        product_url="https://www.target.com/p/desk-light"),
        approve_paid_generation=True)
    with patch("backend.wan_video_api.video_guard"), patch("backend.wan_video_api.provider_request") as send:
        with pytest.raises(HTTPException) as exc:
            submit(data, Mock())
    assert exc.value.status_code == 503
    send.assert_not_called()


def test_wan_rejects_unapproved_paid_submit(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fake")
    monkeypatch.setenv("TRAFFICLIFT_WAN_VIDEO_ENABLED", "1")
    data = WanSubmit(product=VideoRequest(
        name="Desk Light",
        image_url="https://target.scene7.com/is/image/Target/light",
        product_url="https://www.target.com/p/desk-light"))
    with patch("backend.wan_video_api.video_guard"), patch("backend.wan_video_api.provider_request") as send:
        with pytest.raises(HTTPException) as exc:
            submit(data, Mock())
    assert exc.value.status_code == 409
    send.assert_not_called()


def test_wan_submit_uses_exact_listing_photo_without_reusing_stale_selected(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fake")
    monkeypatch.setenv("TRAFFICLIFT_WAN_VIDEO_ENABLED", "1")
    data = WanSubmit(product=VideoRequest(
        name="Desk Light",
        image_url="https://target.scene7.com/is/image/Target/wrong-stale-lamp",
        product_url="https://www.target.com/p/desk-light"),
        approve_paid_generation=True)
    plan = {"evidence": [{"text": "Adjustable neck"}, {"text": "On/off switch"}]}
    seller_photo = "https://target.scene7.com/is/image/Target/verified-desk-light"
    with patch("backend.wan_video_api.video_guard"), \
         patch("backend.wan_video_api.prepare_video_plan", return_value=(plan, [seller_photo])), \
         patch("backend.wan_video_api.marketing_readiness_gate"), \
         patch("backend.wan_video_api.fal_client.submit", return_value=Mock(request_id="019a1b2c-3d4e-7f80-9a1b-2c3d4e5f6a7b")) as send:
        response = submit(data, Mock())
    assert response["status"] == "submitted"
    payload = send.call_args.kwargs["arguments"]
    assert payload["image_url"] == seller_photo
    assert payload["aspect_ratio"] == "9:16"
    assert payload["num_frames"] == 121
    assert payload["frames_per_second"] == 24
    assert send.call_args.args[0] == "fal-ai/wan/v2.2-5b/image-to-video"


def test_wan_no_paid_request_when_seller_photo_missing(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fake")
    monkeypatch.setenv("TRAFFICLIFT_WAN_VIDEO_ENABLED", "1")
    data = WanSubmit(product=VideoRequest(
        name="Desk Light",
        image_url="https://target.scene7.com/is/image/Target/wrong-lamp",
        product_url="https://www.target.com/p/desk-light"),
        approve_paid_generation=True)
    with patch("backend.wan_video_api.video_guard"), \
         patch("backend.wan_video_api.prepare_video_plan", return_value=({"evidence": [{"text": "Switch"}]}, [])), \
         patch("backend.wan_video_api.marketing_readiness_gate"), \
         patch("backend.wan_video_api.provider_request") as send:
        with pytest.raises(HTTPException) as exc:
            submit(data, Mock())
    assert exc.value.status_code == 422
    send.assert_not_called()


def test_status_constructs_provider_url_from_valid_id_only(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fake")
    monkeypatch.setenv("TRAFFICLIFT_WAN_VIDEO_ENABLED", "1")
    with patch("backend.wan_video_api.video_guard"), patch("backend.wan_video_api.provider_request") as send:
        with pytest.raises(HTTPException) as exc:
            status("https://evil.example/a", Mock())
        assert exc.value.status_code == 404
        send.assert_not_called()


@pytest.mark.parametrize("provider_status,expected_text", [
    (401, "API key"), (403, "permissions"), (402, "credits"),
    (422, "video input"), (429, "rate limit"), (503, "temporarily unavailable")
])
def test_provider_diagnostic_is_safe_and_never_leaks_provider_body(monkeypatch, provider_status, expected_text):
    from backend.wan_video_api import provider_request
    monkeypatch.setenv("FAL_KEY", "private-test-secret")
    response = Mock()
    response.status_code = provider_status
    response.text = "private-test-secret private seller information"
    with patch("backend.wan_video_api.requests.request", return_value=response) as send:
        with pytest.raises(HTTPException) as exc:
            provider_request("POST", "https://queue.fal.run/fal-ai/wan/v2.2-5b/image-to-video", json={})
    assert exc.value.status_code == 502
    assert expected_text in exc.value.detail
    assert str(provider_status) in exc.value.detail
    assert "private-test-secret" not in exc.value.detail
    assert "private seller" not in exc.value.detail
    assert send.call_count == 1


def test_wan_status_uses_official_queue_handle(monkeypatch):
    from backend.wan_video_api import status
    import fal_client
    monkeypatch.setenv("FAL_KEY", "fake")
    monkeypatch.setenv("TRAFFICLIFT_WAN_VIDEO_ENABLED", "1")
    handle = Mock()
    handle.status.return_value = fal_client.Completed(logs=None, metrics={})
    handle.get.return_value = {"video": {"url": "https://cdn.example.org/trial.mp4"}}
    with patch("backend.wan_video_api.video_guard"), \
         patch("backend.wan_video_api.fal_client.get_handle", return_value=handle) as get_handle:
        result = status("019a1b2c-3d4e-7f80-9a1b-2c3d4e5f6a7b", Mock())
    assert result["status"] == "completed"
    assert result["video_url"] == "https://cdn.example.org/trial.mp4"
    get_handle.assert_called_once_with("fal-ai/wan/v2.2-5b/image-to-video",
                                       "019a1b2c-3d4e-7f80-9a1b-2c3d4e5f6a7b")
