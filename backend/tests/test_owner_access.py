"""Private campaigns, paid video commands and research limits on the hosted site."""
import hashlib
import hmac
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import trafficlift_pro as server
from backend import owner_access

SECRET = "s" * 40
BASE = "https://trafficlift-pro.onrender.com"
ID = "11111111-2222-3333-4444-555555555555"


def session_cookie(secret=SECRET, issued=None):
    issued = str(int(issued if issued is not None else time.time()))
    signature = hmac.new(secret.encode(), ("video-session:" + issued).encode(), hashlib.sha256).hexdigest()
    return issued + "." + signature


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("RENDER_SERVICE_ID", "srv-test")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN", SECRET)
    owner_access._PER_CLIENT.clear()
    owner_access._ALL_ANONYMOUS.clear()
    return TestClient(server.app, base_url=BASE)


PRIVATE = [
    ("get", "/api/v1/campaigns/history", None),
    ("get", "/api/v1/campaigns/" + ID, None),
    ("delete", "/api/v1/campaigns/" + ID, None),
    ("delete", "/api/v1/campaigns/clear", None),
    ("post", "/api/v1/campaigns/delete-selected", {"campaign_ids": [ID]}),
    ("post", "/api/v1/video/generate", {"prompt": "A product on a table, slow camera move."}),
    ("post", "/api/v1/video/render", {"prompt": "A product on a table, slow camera move."}),
    ("get", "/api/v1/video/status/task-1", None),
]


@pytest.mark.parametrize("method,path,body", PRIVATE)
def test_strangers_cannot_read_delete_or_spend(hosted, method, path, body):
    with patch.object(server.db, "delete") as delete, patch.object(server.db, "delete_many") as delete_many, \
         patch.object(server.db, "clear_all") as clear_all, patch.object(server.db, "list_history") as list_all, \
         patch.object(server.db, "get_by_id") as get_by_id, patch.object(server, "_video_client") as video:
        response = hosted.request(method, path, json=body)
    assert response.status_code == 401
    for touched in (delete, delete_many, clear_all, list_all, get_by_id, video):
        touched.assert_not_called()


def test_forged_and_expired_sessions_are_refused(hosted):
    for cookie in (session_cookie("x" * 40), session_cookie(issued=time.time() - 9 * 3600), "garbage"):
        hosted.cookies.set("trafficlift_video_session", cookie)
        assert hosted.get("/api/v1/campaigns/history").status_code == 401


def test_missing_owner_code_fails_closed(hosted, monkeypatch):
    monkeypatch.delenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN")
    with patch.object(server.db, "clear_all") as clear_all:
        assert hosted.delete("/api/v1/campaigns/" + ID).status_code == 503
    clear_all.assert_not_called()


def test_owner_session_reads_and_deletes(hosted):
    hosted.cookies.set("trafficlift_video_session", session_cookie())
    assert hosted.get("/api/v1/campaigns/history").status_code == 200
    with patch.object(server.db, "delete", return_value=True) as delete:
        assert hosted.delete("/api/v1/campaigns/" + ID).status_code == 200
    delete.assert_called_once_with(ID)


def test_owner_pc_is_unchanged(monkeypatch):
    for name in ("RENDER_SERVICE_ID", "TRAFFICLIFT_HOSTED_VIDEO"):
        monkeypatch.delenv(name, raising=False)
    assert TestClient(server.app).get("/api/v1/campaigns/history").status_code == 200


def test_anonymous_research_is_capped_but_owner_is_not(hosted, monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_ANON_RESEARCH_PER_DAY", "3")
    with patch("backend.product_scout._synthesize", return_value={"ok": True}) as research:
        codes = [hosted.get("/api/v1/find-winner", headers={"x-forwarded-for": f"203.0.113.{n}"}).status_code
                 for n in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        assert research.call_count == 3
        hosted.cookies.set("trafficlift_video_session", session_cookie())
        assert hosted.get("/api/v1/find-winner").status_code == 200


def test_one_visitor_is_slowed_down(hosted):
    with patch("backend.product_scout._synthesize", return_value={"ok": True}):
        codes = [hosted.get("/api/v1/find-winner").status_code for _ in range(owner_access.PER_MINUTE + 1)]
    assert codes[-1] == 429 and set(codes[:-1]) == {200}


def test_only_own_addresses_may_call_from_a_browser(hosted):
    def allowed(origin):
        reply = hosted.get("/api/v1/health", headers={"origin": origin})
        return reply.headers.get("access-control-allow-origin")
    assert allowed("https://evil.example") is None
    assert allowed(BASE) == BASE
    assert allowed("http://localhost:8000") == "http://localhost:8000"
    assert allowed("http://localhost.evil.example") is None
