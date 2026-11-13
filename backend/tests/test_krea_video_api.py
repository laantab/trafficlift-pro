"""Offline Krea guardrail tests; never charge API credits."""
from unittest.mock import Mock, patch
import pytest
from fastapi import HTTPException
from backend.krea_video_api import KreaSubmit, configured, submit, provider_request, status


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRAFFICLIFT_KREA_VIDEO_ENABLED", raising=False)
    monkeypatch.delenv("KREA_API_KEY", raising=False)
    assert not configured()


def test_billing_requires_explicit_approval(monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_KREA_VIDEO_ENABLED", "1")
    monkeypatch.setenv("KREA_API_KEY", "mock-only")
    with patch("backend.krea_video_api.video_guard"), patch("backend.krea_video_api.provider_request") as send:
        with pytest.raises(HTTPException) as error:
            submit(KreaSubmit(image_url="https://example.com/item.png", prompt="Show the mat staying intact while chicken is lifted", confirm_video_photo=True), Mock())
        assert error.value.status_code == 409
        send.assert_not_called()


def test_unconfirmed_image_never_charged(monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_KREA_VIDEO_ENABLED", "1")
    monkeypatch.setenv("KREA_API_KEY", "mock-only")
    with patch("backend.krea_video_api.video_guard"), patch("backend.krea_video_api.provider_request") as send:
        with pytest.raises(HTTPException) as error:
            submit(KreaSubmit(image_url="https://example.com/item.png", prompt="Show the mat staying intact while chicken is lifted", approve_paid_generation=True), Mock())
        assert error.value.status_code == 422
        send.assert_not_called()


def test_submit_image_to_video_without_audio_or_pin_sharing(monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_KREA_VIDEO_ENABLED", "1")
    monkeypatch.setenv("KREA_API_KEY", "mock-only")
    with patch("backend.krea_video_api.video_guard"), patch("backend.krea_video_api._validate_public_https_url"), patch("backend.krea_video_api.provider_request", return_value={"job_id": "job_1234567", "status": "scheduled"}) as send:
        result = submit(KreaSubmit(image_url="https://example.com/mat.png", prompt="Lift the roasted chicken from the red mat using tongs", approve_paid_generation=True, confirm_video_photo=True), Mock())
    assert result["status"] == "submitted"
    payload = send.call_args.kwargs["json"]
    assert payload["start_image"] == "https://example.com/mat.png"
    assert payload["aspect_ratio"] == "9:16"
    assert payload["generate_audio"] is False
    send.assert_called_once()


def test_status_only_returns_completed_https_video(monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_KREA_VIDEO_ENABLED", "1")
    monkeypatch.setenv("KREA_API_KEY", "mock-only")
    with patch("backend.krea_video_api.video_guard"), patch("backend.krea_video_api.provider_request", return_value={"job_id": "job_1234567", "status": "completed", "result": {"urls": ["https://cdn.example.com/video.mp4"]}}):
        result = status("job_1234567", Mock())
    assert result["video_url"] == "https://cdn.example.com/video.mp4"


def test_http_error_never_leaks_secret(monkeypatch):
    import requests
    monkeypatch.setenv("KREA_API_KEY", "secret-krea-token")
    response = requests.Response()
    response.status_code = 402
    response._content = b'secret-krea-token'
    with patch("backend.krea_video_api.requests.request", return_value=response):
        with pytest.raises(HTTPException) as error:
            provider_request("POST", "/generate/video/bytedance/seedance-2-fast", json={})
    assert error.value.status_code == 402
    assert "secret-krea-token" not in error.value.detail
