"""Krea 422 diagnostics retain privacy and prevent automatic paid retries."""
from unittest.mock import patch
import pytest
import requests
from fastapi import HTTPException
from backend.krea_video_api import provider_request

@pytest.mark.parametrize("body,expected", [
    ({"error":"Validation failed","details":[{"field":"start_image","message":"private signed URL token=secret"}]}, "starting product image"),
    ({"error":"Validation failed","details":[{"field":"aspect_ratio","message":"private-value"}]}, "video shape"),
    ({"detail":"https://secret/private/image","errors":[{"field":"unknown_field","message":"private-value"}]}, "video settings or starting image"),
])
def test_422_returns_actionable_field_without_leaking_provider_body(monkeypatch,body,expected):
    monkeypatch.setenv("KREA_API_KEY","secret-token")
    response=requests.Response()
    response.status_code=422
    response._content=__import__("json").dumps(body).encode()
    with patch("backend.krea_video_api.requests.request",return_value=response) as send:
        with pytest.raises(HTTPException) as exc:
            provider_request("POST","/generate/video/bytedance/seedance-2",json={"prompt":"test"})
    assert exc.value.status_code==422
    assert expected in exc.value.detail
    assert "secret" not in exc.value.detail
    assert "private" not in exc.value.detail
    send.assert_called_once()


def test_402_explains_api_balance_not_subscription(monkeypatch):
    monkeypatch.setenv("KREA_API_KEY","secret-token")
    response=requests.Response()
    response.status_code=402
    response._content=b'private balance'
    with patch("backend.krea_video_api.requests.request",return_value=response):
        with pytest.raises(HTTPException) as exc:
            provider_request("POST","/generate/video/bytedance/seedance-2",json={})
    assert exc.value.status_code==402
    assert "Basic subscription" in exc.value.detail
    assert "private balance" not in exc.value.detail
