"""Cloud video is opt-in, metered, and must not weaken research key security."""
from types import SimpleNamespace
from fastapi import HTTPException
import pytest
from backend import photo_video_api as video


def _request(origin=None, token=None):
    headers = {'origin': origin} if origin else {}
    if token is not None: headers['x-trafficlift-video-token'] = token
    return SimpleNamespace(headers=headers,
        base_url='https://trafficlift-pro.onrender.com/',
        client=SimpleNamespace(host='203.0.113.4'),
        url=SimpleNamespace(hostname='trafficlift-pro.onrender.com',scheme='https',netloc='trafficlift-pro.onrender.com'))


def test_cloud_video_off_by_default(monkeypatch):
    monkeypatch.delenv('TRAFFICLIFT_HOSTED_VIDEO', raising=False)
    with pytest.raises(HTTPException) as exc:
        video.video_guard(_request())
    assert exc.value.status_code == 503


def test_cloud_video_explicit_optin_and_origin(monkeypatch):
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_VIDEO','1')
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN','a'*40)
    video.video_guard(_request(token='a'*40))
    with pytest.raises(HTTPException) as exc:
        video.video_guard(_request('https://wrong.example',token='a'*40))
    assert exc.value.status_code == 403



def test_hosted_video_requires_secure_config_and_authorization(monkeypatch):
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_VIDEO','1')
    monkeypatch.delenv('TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN',raising=False)
    with pytest.raises(HTTPException) as missing:
        video.video_guard(_request())
    assert missing.value.status_code == 503
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN','a'*40)
    with pytest.raises(HTTPException) as unauthorized:
        video.video_guard(_request())
    assert unauthorized.value.status_code == 401
    with pytest.raises(HTTPException) as wrong:
        video.video_guard(_request(token='b'*40))
    assert wrong.value.status_code == 401
    video.video_guard(_request(token='a'*40))

def test_daily_quota_survives_repeated_calls(monkeypatch,tmp_path):
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_VIDEO','1')
    monkeypatch.setenv('TRAFFICLIFT_VIDEO_DIR',str(tmp_path))
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_DAILY_LIMIT','2')
    video.reserve_hosted_render()
    video.reserve_hosted_render()
    with pytest.raises(HTTPException) as exc:
        video.reserve_hosted_render()
    assert exc.value.status_code == 429


def test_sensitive_research_endpoint_stays_loopback_only(monkeypatch):
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_VIDEO','1')
    monkeypatch.delenv('TRAFFICLIFT_FREE_VIDEO', raising=False)
    with pytest.raises(HTTPException) as exc:
        video.guard(_request())
    assert exc.value.status_code == 503
