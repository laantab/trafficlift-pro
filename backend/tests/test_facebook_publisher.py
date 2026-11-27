from cryptography.fernet import Fernet

from backend import facebook_publisher as fb


def test_queue_publish_and_track_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR", str(tmp_path))
    monkeypatch.setenv("TRAFFICLIFT_PUBLISHING_KEY", Fernet.generate_key().decode())
    video = tmp_path / "video.mp4"
    video.write_bytes(b"approved video")
    fb.store.put("facebook", "owner", {"id": "999", "name": "Intelligent innovation solutions",
                 "access_token": "page-token"})
    created = []

    def fake_api(method, path, **kwargs):
        if path == "999/video_reels" and kwargs["params"]["upload_phase"] == "start":
            return {"video_id": "8", "upload_url": "https://rupload.facebook.com/video-upload/v21.0/8"}
        if path == "8":
            return {"status": {"video_status": "ready"}}
        assert path == "999/video_reels" and kwargs["params"]["upload_phase"] == "finish"
        created.append(kwargs["params"])
        return {"success": True}
    monkeypatch.setattr(fb, "api", fake_api)

    class Success:
        def raise_for_status(self):
            pass
    monkeypatch.setattr(fb.requests, "post", lambda *a, **k: Success())

    fb.queue("campaign", "job-1", "Comic Book Creator", "A memory for Christmas.", video)
    fb.tick()
    assert len(created) == 1 and created[0]["description"] == "A memory for Christmas."
    with fb.database() as db:
        assert db.execute("SELECT state FROM reels WHERE source='campaign' AND identifier='job-1'").fetchone()[0] == \
            "published and tracked"
    assert any(s["reference"].endswith("/8") for s in fb.ad_results.overview()["subjects"])
    # A published Reel is never queued twice.
    fb.queue("campaign", "job-1", "Comic Book Creator", "A memory for Christmas.", video)
    assert fb.status_for("campaign", "job-1") == "published and tracked"


def test_owner_connects_and_selects_page(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from urllib.parse import parse_qs, urlsplit
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR", str(tmp_path))
    monkeypatch.setenv("TRAFFICLIFT_PUBLISHING_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FACEBOOK_APP_ID", "1234")
    monkeypatch.setenv("FACEBOOK_APP_SECRET", "test-secret")
    monkeypatch.setattr(fb, "start", lambda: None)

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def fake_get(url, params=None, **kwargs):
        if url == fb.API + "oauth/access_token" and params.get("code") == "c":
            return Response({"access_token": "short-token"})
        if url == fb.API + "oauth/access_token" and params.get("fb_exchange_token") == "short-token":
            return Response({"access_token": "long-token"})
        if url == fb.API + "me/accounts":
            assert params["access_token"] == "long-token"
            return Response({"data": [{"id": "999", "name": "Intelligent innovation solutions", "access_token": "page-token"},
                                      {"id": "111", "name": "Mt Zion Baptist Church", "access_token": "other-token"}]})
        raise AssertionError(url)
    monkeypatch.setattr(fb.requests, "get", fake_get)

    app = FastAPI()
    app.include_router(fb.router)
    app.dependency_overrides[fb.owner] = lambda: None
    assert fb.page() is None
    with TestClient(app, base_url="https://testserver") as client:
        first = client.get("/api/v1/facebook/connect", follow_redirects=False)
        query = parse_qs(urlsplit(first.headers["location"]).query)
        assert first.status_code == 303 and query["client_id"] == ["1234"]
        assert "pages_manage_posts" in query["scope"][0] and "test-secret" not in first.headers["location"]
        assert query["redirect_uri"] == ["https://testserver/api/v1/facebook/callback"]
        state = query["state"][0]
        assert client.get("/api/v1/facebook/callback", params={"state": "wrong", "code": "c"}).status_code == 400
        again = client.get("/api/v1/facebook/connect", follow_redirects=False)
        state = parse_qs(urlsplit(again.headers["location"]).query)["state"][0]
        done = client.get("/api/v1/facebook/callback", params={"state": state, "code": "c"}, follow_redirects=False)
        assert done.status_code == 303 and done.headers["location"] == "/owner?facebook=choose-page"
        choices = client.get("/api/v1/facebook/status").json()["choices"]
        assert {c["id"] for c in choices} == {"999", "111"}
        picked = client.post("/api/v1/facebook/page", json={"page_id": "999"})
        assert picked.status_code == 200 and picked.json()["page_name"] == "Intelligent innovation solutions"
    assert fb.page() == {"id": "999", "name": "Intelligent innovation solutions", "access_token": "page-token"}
