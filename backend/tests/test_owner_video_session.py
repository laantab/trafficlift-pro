"""Owner beta login uses HttpOnly cookies; forged/missing sessions are rejected."""
from fastapi.testclient import TestClient
from trafficlift_pro import app
from backend.photo_video_api import valid_video_session
import time
import hmac, hashlib

SECRET = 'A' * 48

def test_hosted_owner_login_and_cookie(monkeypatch):
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_VIDEO', '1')
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN', SECRET)
    client = TestClient(app, base_url='https://trafficlift-pro.onrender.com')
    denied = client.get('/api/v1/photo-videos')
    assert denied.status_code == 401
    wrong = client.post('/api/v1/video-owner-login', json={'access_code':'wrong'},
        headers={'origin':'https://trafficlift-pro.onrender.com'})
    assert wrong.status_code == 401
    login = client.post('/api/v1/video-owner-login', json={'access_code':SECRET},
        headers={'origin':'https://trafficlift-pro.onrender.com'})
    assert login.status_code == 200
    assert 'httponly' in login.headers['set-cookie'].lower()
    assert 'secure' in login.headers['set-cookie'].lower()
    assert 'samesite=strict' in login.headers['set-cookie'].lower()
    assert client.get('/api/v1/photo-videos').status_code == 200

def test_owner_login_cross_site_rejected(monkeypatch):
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_VIDEO', '1')
    monkeypatch.setenv('TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN', SECRET)
    client = TestClient(app, base_url='https://trafficlift-pro.onrender.com')
    response = client.post('/api/v1/video-owner-login',
        json={'access_code':SECRET}, headers={'origin':'https://attacker.example'})
    assert response.status_code == 403

def test_signed_owner_session_rejects_expired_and_forged():
    issued = str(int(time.time()) - 29000)
    sig = hmac.new(SECRET.encode(), ('video-session:' + issued).encode(), hashlib.sha256).hexdigest()
    assert not valid_video_session(issued + '.' + sig, SECRET)
    assert not valid_video_session(str(int(time.time())) + '.' + '0'*64, SECRET)
    assert not valid_video_session('', SECRET)
