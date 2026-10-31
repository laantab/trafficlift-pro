"""Offline tests: no HeyGen requests or credits are consumed."""
from unittest.mock import patch, Mock
import pytest
from fastapi import HTTPException

from backend.heygen_video_api import configured, product_prompt, provider_request


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


def test_prompt_uses_evidence_text_and_stable_framing():
    plan = {"phrases": ["A compact storage organizer.", "Check dimensions on the seller listing."]}
    result = product_prompt("Organizer", plan)
    assert "no strobing or flicker" in result
    assert "Speak exactly: A compact storage organizer." in result
    assert "no invented features" in result.lower()


def test_prompt_refuses_missing_script():
    with pytest.raises(HTTPException) as err:
        product_prompt("Organizer", {"phrases": []})
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
