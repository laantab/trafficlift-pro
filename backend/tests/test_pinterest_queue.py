import zipfile
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend import campaign_api, campaign_editor, pinterest_queue as pins


def test_schedule_publish_and_track_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR", str(tmp_path))
    campaign = uuid4()
    folder = campaign_api.root()/campaign.hex
    folder.mkdir()
    (folder/"video.mp4").write_bytes(b"approved video")
    with zipfile.ZipFile(folder/"campaign.zip","w") as archive:
        archive.writestr("cover-frame.jpg", b"approved cover")
        archive.writestr("facebook-reels.txt", "A memory for Christmas.\n"+campaign_api.HOLIDAY["destination"])
    campaign_editor.receipt(folder, 10, 4)
    with campaign_api.database() as db:
        db.execute("INSERT INTO jobs VALUES(?,?,?,?)",(campaign.hex,"holiday",0,"ready"))
    created = []
    def fake_api(method,path,**kwargs):
        if path=="boards": return {"items":[{"id":"123","name":"Books"}]}
        if path=="media": return {"media_id":"8","upload_url":"https://example.org/upload","upload_parameters":{}}
        if path=="media/8": return {"status":"succeeded"}
        assert path=="pins"
        created.append(kwargs["json"])
        return {"id":"999"}
    monkeypatch.setattr(pins,"api",fake_api)
    class Success:
        def raise_for_status(self): pass
    monkeypatch.setattr(pins.requests,"post",lambda *a,**k: Success())
    selection=pins.Schedule(board="123",when=datetime.now(timezone.utc)+timedelta(hours=1))
    for _ in range(2): assert len(pins.schedule(campaign,selection))==1
    pins.tick()
    assert not created
    with pins.database() as db: db.execute("UPDATE pins SET due=0")
    pins.tick()
    pins.tick()
    assert len(created)==1 and created[0]["link"]==campaign_api.HOLIDAY["destination"]
    assert pins.history(campaign)[0]["state"]=="published and tracked"
    # A published Pin is never queued twice, but one that never reached the board can be approved again.
    assert pins.schedule(campaign,selection)[0]["state"]=="published and tracked"
    with pins.database() as db, db: db.execute("UPDATE pins SET state='check board', pin=''")
    assert pins.schedule(campaign,selection)[0]["state"]=="scheduled"
    assert any(s["reference"].endswith("/999/") for s in pins.ad_results.overview()["subjects"])


def test_owner_connects_and_token_renews(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from urllib.parse import parse_qs, urlsplit
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR", str(tmp_path))
    monkeypatch.setenv("TRAFFICLIFT_PUBLISHING_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PINTEREST_APP_ID", "1644839")
    monkeypatch.setenv("PINTEREST_APP_SECRET", "test-secret")
    monkeypatch.delenv("TRAFFICLIFT_PINTEREST_TOKEN", raising=False)
    monkeypatch.setattr(pins, "start", lambda: None)
    sent = []
    def fake_exchange(fields):
        sent.append(fields)
        return {"access_token": "access-%d" % len(sent), "refresh_token": "refresh-%d" % len(sent),
                "expires_at": pins.time.time() + (60 if len(sent) == 1 else 3600)}
    monkeypatch.setattr(pins, "exchange", fake_exchange)
    app = FastAPI()
    app.include_router(pins.router)
    app.dependency_overrides[pins.owner] = lambda: None
    assert pins.token() is None
    with TestClient(app, base_url="https://testserver") as client:
        first = client.get("/api/v1/pinterest/connect", follow_redirects=False)
        query = parse_qs(urlsplit(first.headers["location"]).query)
        assert first.status_code == 303 and query["client_id"] == ["1644839"]
        assert "pins:write" in query["scope"][0] and "boards:write" in query["scope"][0] and "test-secret" not in first.headers["location"]
        assert query["redirect_uri"] == ["https://testserver/api/v1/pinterest/callback"]
        state = query["state"][0]
        assert client.get("/api/v1/pinterest/callback", params={"state": "wrong", "code": "c"}).status_code == 400
        assert pins.store.get("pinterest", "owner") is None
        # A failed attempt consumes the request, so the owner starts again.
        again = client.get("/api/v1/pinterest/connect", follow_redirects=False)
        state = parse_qs(urlsplit(again.headers["location"]).query)["state"][0]
        done = client.get("/api/v1/pinterest/callback", params={"state": state, "code": "c"}, follow_redirects=False)
        assert done.status_code == 303 and done.headers["location"] == "/owner?pinterest=connected"
    assert sent[0]["grant_type"] == "authorization_code" and sent[0]["code"] == "c"
    # The first token is about to expire, so the next use renews and saves it.
    assert pins.token() == "access-2" and sent[1] == {"grant_type": "refresh_token", "refresh_token": "refresh-1"}
    assert pins.token() == "access-2" and len(sent) == 2
