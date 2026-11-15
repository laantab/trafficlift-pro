"""Offline Krea validation diagnostics, without any paid provider requests."""
import json
from unittest.mock import patch
import requests
import pytest
from fastapi import HTTPException
from backend.krea_video_api import _safe_validation_fields, provider_request

@pytest.mark.parametrize("body,expected", [
    ({"detail":[{"loc":["body","start_image"],"msg":"secret URL"}]}, ["start_image"]),
    ({"error":{"details":[{"path":"$.aspect_ratio","message":"private"}]}}, ["aspect_ratio"]),
    ({"errors":[{"field":"duration","message":"token"}]}, ["duration"]),
    ({"detail":"secret image URL"}, []),
    ({"errors":[{"field":"unknown","message":"start_image=secret"}]}, []),
])
def test_safe_fields(body,expected):
    fields, labels = _safe_validation_fields(body)
    assert fields == expected
    assert all("secret" not in label for label in labels)


def test_krea_422_logs_only_allowlisted_field(monkeypatch,caplog):
    monkeypatch.setenv("KREA_API_KEY","secret-token")
    response=requests.Response()
    response.status_code=422
    response._content=json.dumps({"detail":[{"loc":["body","start_image"],"msg":"signed URL secret-token https://private.example/path"}]}).encode()
    with patch("backend.krea_video_api.requests.request",return_value=response) as send:
        with pytest.raises(HTTPException) as exc:
            provider_request("POST","/generate/video/bytedance/seedance-2",json={})
    assert exc.value.status_code == 422
    assert "starting product image" in exc.value.detail
    assert "fields=start_image" in caplog.text
    assert "secret-token" not in caplog.text + exc.value.detail
    assert "private.example" not in caplog.text + exc.value.detail
    send.assert_called_once()
