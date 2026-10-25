"""Offline regressions for the explicit live owner MP4 acceptance probe."""
from pathlib import Path

import pytest

from scripts.owner_video_live_acceptance import AcceptanceError, preflight, verify

BASE = "https://trafficlift-pro.onrender.com"
ID = "a" * 32
COOKIE = "trafficlift_video_session=fake; HttpOnly; Secure; SameSite=Strict"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"x" * 2048


class Response:
    def __init__(self, status=200, data=None, headers=None, content=b""):
        self.status_code = status
        self._data = data or {}
        self.headers = headers or {"Cache-Control": "private, no-store"}
        self.content = content
        self.closed = False

    def json(self):
        return self._data

    def iter_content(self, chunk_size):
        yield self.content[:3]
        yield self.content[3:]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, *, unauthorized=401, login=200, states=("running", "succeeded"), mp4=MP4, health=200,
                 status_public=401, mp4_public=401):
        self.health = health
        self.unauthorized = unauthorized
        self.login = login
        self.states = iter(states)
        self.mp4 = mp4
        self.posts = []
        self.download = None
        self.logged_in = False
        self.reads = []
        self.status_public = status_public
        self.mp4_public = mp4_public

    def get(self, url, **kwargs):
        assert url.startswith(BASE + "/api/v1/")
        assert kwargs["allow_redirects"] is False
        self.reads.append(url)
        if url.endswith("/health"):
            return Response(self.health)
        if url.endswith("/photo-videos"):
            return Response(self.unauthorized)
        if url.endswith("/video") and not self.logged_in:
            return Response(self.mp4_public)
        if url.endswith("/" + ID) and not self.logged_in:
            return Response(self.status_public)
        if url.endswith("/video"):
            self.download = Response(
                headers={"Cache-Control": "private, no-store", "Content-Type": "video/mp4"},
                content=self.mp4,
            )
            return self.download
        state = next(self.states)
        return Response(data={"video": {"status": state, "message": "fixture failure"}})

    def post(self, url, **kwargs):
        assert url.startswith(BASE + "/api/v1/")
        assert kwargs["allow_redirects"] is False
        assert kwargs["headers"]["Origin"] == BASE
        self.posts.append(url)
        if url.endswith("/video-owner-login"):
            self.logged_in = self.login == 200
            return Response(self.login, headers={"Cache-Control": "private, no-store",
                                                "Set-Cookie": COOKIE})
        return Response(202, data={"video": {"id": ID}})


def test_readonly_preflight_checks_health_and_denied_history_without_login_or_render():
    session = FakeSession()
    assert preflight(session) == {
        "health": "ok", "owner_history": "protected",
        "owner_status": "protected", "owner_mp4": "protected",
    }
    assert session.reads == [
        BASE + "/api/v1/health", BASE + "/api/v1/photo-videos",
        BASE + "/api/v1/photo-videos/" + ID,
        BASE + "/api/v1/photo-videos/" + ID + "/video",
    ]
    assert session.posts == []
    assert session.download is None


@pytest.mark.parametrize("health,unauthorized,status_public,mp4_public", [
    (503, 401, 401, 401), (200, 200, 401, 401), (302, 401, 401, 401),
    (200, 401, 200, 401), (200, 401, 401, 200),
    (200, 401, 404, 401), (200, 401, 401, 302),
])
def test_readonly_preflight_fails_closed_without_login_or_render(
        health, unauthorized, status_public, mp4_public):
    session = FakeSession(health=health, unauthorized=unauthorized,
                          status_public=status_public, mp4_public=mp4_public)
    with pytest.raises(AcceptanceError):
        preflight(session)
    assert session.posts == []
    assert session.download is None


def test_live_probe_login_create_poll_and_save_real_mp4_signature(tmp_path):
    session = FakeSession()
    output = tmp_path / "verified.mp4"
    result = verify(session, "not-a-real-secret", payload={"name": "test"},
                    output=output, interval=0, sleep=lambda _: None)
    assert result["job_id"] == ID
    assert result["bytes"] == len(MP4)
    assert output.read_bytes() == MP4
    assert len(session.posts) == 2
    assert session.download.closed


def test_resume_does_not_consume_render_quota(tmp_path):
    session = FakeSession(states=("succeeded",))
    result = verify(session, "not-a-real-secret", resume=ID, output=tmp_path / "saved.mp4")
    assert result["job_id"] == ID
    assert session.posts == [BASE + "/api/v1/video-owner-login"]


@pytest.mark.parametrize("session", [
    FakeSession(unauthorized=200),
    FakeSession(login=302),
])
def test_fail_closed_when_unauthorized_access_or_redirect_is_seen(session, tmp_path):
    with pytest.raises(AcceptanceError):
        verify(session, "not-a-real-secret", resume=ID, output=tmp_path / "bad.mp4")
    assert not (tmp_path / "bad.mp4").exists()


def test_reject_non_mp4_and_remove_partial_file(tmp_path):
    session = FakeSession(states=("succeeded",), mp4=b"not-an-mp4" * 500)
    output = tmp_path / "bad.mp4"
    with pytest.raises(AcceptanceError, match="plausible MP4"):
        verify(session, "not-a-real-secret", resume=ID, output=output)
    assert not output.exists()
    assert not list(tmp_path.glob("*.partial"))
    assert session.download.closed



@pytest.mark.parametrize("status,content_type,expected", [
    (503, "video/mp4", "MP4 download"),
    (200, "text/html", "not an MP4"),
])
def test_rejected_download_closes_private_stream(tmp_path, status, content_type, expected):
    """A failed MP4 response must not keep a streamed connection open."""
    class BadDownloadSession(FakeSession):
        def get(self, url, **kwargs):
            if url.endswith("/video"):
                self.download = Response(
                    status,
                    headers={"Cache-Control": "private, no-store", "Content-Type": content_type},
                    content=MP4,
                )
                return self.download
            return super().get(url, **kwargs)

    session = BadDownloadSession(states=("succeeded",))
    output = tmp_path / "rejected.mp4"
    with pytest.raises(AcceptanceError, match=expected):
        verify(session, "not-a-real-secret", resume=ID, output=output)
    assert session.download.closed
    assert not output.exists()
    assert not list(tmp_path.glob("*.partial"))


def test_failed_video_never_attempts_download(tmp_path):
    session = FakeSession(states=("failed",))
    with pytest.raises(AcceptanceError, match="render failed"):
        verify(session, "not-a-real-secret", resume=ID, output=tmp_path / "bad.mp4")
    assert session.download is None


def test_reject_invalid_resume_id_without_network(tmp_path):
    session = FakeSession()
    with pytest.raises(AcceptanceError, match="invalid video job ID"):
        verify(session, "not-a-real-secret", resume="../unsafe", output=tmp_path / "bad.mp4")
    assert session.posts == []


def test_created_job_id_survives_status_network_interruption(tmp_path):
    """A live acceptance run must reveal its safe job ID before a poll fails.

    The operator can then use --resume rather than consume another render slot.
    """
    import requests

    class InterruptedSession(FakeSession):
        def get(self, url, **kwargs):
            if url.endswith("/" + ID):
                raise requests.Timeout("temporary polling timeout")
            return super().get(url, **kwargs)

    announced = []
    session = InterruptedSession()
    with pytest.raises(requests.Timeout):
        verify(session, "not-a-real-secret", payload={"name": "test"},
               output=tmp_path / "interrupted.mp4", on_created=announced.append)
    assert announced == [ID]
    assert len(session.posts) == 2
    assert not (tmp_path / "interrupted.mp4").exists()

    resumed = FakeSession(states=("succeeded",))
    result = verify(resumed, "not-a-real-secret", resume=announced[0],
                    output=tmp_path / "resumed.mp4", on_created=announced.append)
    assert result["job_id"] == ID
    assert announced == [ID], "resuming must not announce or create another job"
    assert resumed.posts == [BASE + "/api/v1/video-owner-login"]
