"""Offline regressions for the explicit live owner MP4 acceptance probe."""
from pathlib import Path
import subprocess
import tempfile

import pytest

from scripts.owner_video_live_acceptance import AcceptanceError, preflight, verify

BASE = "https://trafficlift-pro.onrender.com"
ID = "a" * 32
COOKIE = "trafficlift_video_session=fake; HttpOnly; Secure; SameSite=Strict"


def _valid_mp4_fixture():
    """Real tiny playable media: the HTTP acceptance must decode both streams."""
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "fixture.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=64x96:r=5:d=1",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
            "-t", "1", "-c:v", "mpeg4", "-q:v", "5",
            "-c:a", "aac", "-b:a", "32k", "-shortest",
            "-movflags", "+faststart", str(output),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return output.read_bytes()


MP4 = _valid_mp4_fixture()


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
                 status_public=401, mp4_public=401, export_public=401,
                 sample_public=401, sample_mp4_public=401,
                 readiness_status=200, readiness_state="ready", hosted_enabled=True):
        self.health = health
        self.unauthorized = unauthorized
        self.login = login
        self.states = iter(states)
        self.mp4 = mp4
        self.posts = []
        self.download = None
        self.probe_download = None
        self.logged_in = False
        self.reads = []
        self.status_public = status_public
        self.mp4_public = mp4_public
        self.export_public = export_public
        self.sample_public = sample_public
        self.sample_mp4_public = sample_mp4_public
        self.readiness_status = readiness_status
        self.readiness_state = readiness_state
        self.hosted_enabled = hosted_enabled
        self.probe_export = None
        self.probe_sample_mp4 = None

    def get(self, url, **kwargs):
        assert url.startswith(BASE + "/api/v1/")
        assert kwargs["allow_redirects"] is False
        self.reads.append(url)
        if url.endswith("/health"):
            return Response(self.health)
        if url.endswith("/video-readiness"):
            return Response(self.readiness_status, data={
                "state": self.readiness_state,
                "customer_rendering_enabled": self.hosted_enabled,
            })
        if url.endswith("/photo-videos"):
            return Response(self.unauthorized)
        if url.endswith("/exports/tiktok") and not self.logged_in:
            self.probe_export = Response(self.export_public)
            return self.probe_export
        if url.endswith("/video-owner-acceptance/mp4") and not self.logged_in:
            self.probe_sample_mp4 = Response(self.sample_mp4_public)
            return self.probe_sample_mp4
        if url.endswith("/video-owner-acceptance") and not self.logged_in:
            return Response(self.sample_public)
        if url.endswith("/video") and not self.logged_in:
            self.probe_download = Response(self.mp4_public)
            return self.probe_download
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
        "owner_exports": "protected", "owner_sample_status": "protected",
        "owner_sample_mp4": "protected", "video_prerequisites": "ready",
    }
    assert session.reads == [
        BASE + "/api/v1/health", BASE + "/api/v1/photo-videos",
        BASE + "/api/v1/photo-videos/" + ID,
        BASE + "/api/v1/photo-videos/" + ID + "/video",
        BASE + "/api/v1/photo-videos/" + ID + "/exports/tiktok",
        BASE + "/api/v1/video-owner-acceptance",
        BASE + "/api/v1/video-owner-acceptance/mp4",
        BASE + "/api/v1/video-readiness",
    ]
    assert session.posts == []
    assert session.download is None
    assert session.probe_download.closed
    assert session.probe_export.closed
    assert session.probe_sample_mp4.closed


@pytest.mark.parametrize("override", [
    {"health": 503}, {"unauthorized": 200}, {"health": 302},
    {"status_public": 200}, {"mp4_public": 200},
    {"status_public": 404}, {"mp4_public": 302},
    {"export_public": 200}, {"export_public": 302},
    {"sample_public": 200}, {"sample_public": 404},
    {"sample_mp4_public": 200}, {"sample_mp4_public": 302},
    {"readiness_status": 503}, {"readiness_status": 302},
    {"readiness_state": "preparing"}, {"readiness_state": "failed"},
    {"readiness_state": "unknown"}, {"hosted_enabled": False},
])
def test_readonly_preflight_fails_closed_without_login_or_render(override):
    session = FakeSession(**override)
    with pytest.raises(AcceptanceError):
        preflight(session)
    assert session.posts == []
    assert session.download is None
    for response in (session.probe_download, session.probe_export, session.probe_sample_mp4):
        if response is not None:
            assert response.closed


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




def test_http_probe_rejects_fake_ftyp_and_never_publishes_corrupt_mp4(tmp_path):
    """Previously the HTTP probe accepted arbitrary bytes after ftyp as success."""
    fake = b"\x00\x00\x00\x18ftypisom" + b"x" * 2048
    session = FakeSession(states=("succeeded",), mp4=fake)
    output = tmp_path / "corrupt.mp4"
    with pytest.raises(AcceptanceError, match="MP4 invalid"):
        verify(session, "not-a-real-secret", resume=ID, output=output)
    assert session.posts == [BASE + "/api/v1/video-owner-login"]
    assert session.download.closed
    assert not output.exists()
    assert not list(tmp_path.glob("*.partial"))


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


@pytest.mark.parametrize("cache_control", [
    "public, no-store", "no-store", "public, private, no-store",
])
def test_http_acceptance_rejects_public_mp4_even_with_no_store(tmp_path, cache_control):
    """A private owner MP4 cannot pass acceptance with a public cache policy."""
    class PublicDownloadSession(FakeSession):
        def get(self, url, **kwargs):
            if url.endswith("/video"):
                self.download = Response(
                    headers={"Cache-Control": cache_control, "Content-Type": "video/mp4"},
                    content=MP4,
                )
                return self.download
            return super().get(url, **kwargs)

    session = PublicDownloadSession(states=("succeeded",))
    output = tmp_path / "public.mp4"
    with pytest.raises(AcceptanceError, match="private"):
        verify(session, "not-a-real-secret", resume=ID, output=output)
    assert session.download.closed
    assert not output.exists()
    assert not list(tmp_path.glob("*.partial"))


def test_live_acceptance_does_not_overwrite_output_created_during_download(tmp_path):
    """A second operator/process may create the output after the initial check.

    The acceptance probe must fail closed and preserve that existing file,
    rather than replacing it after the streamed download finishes.
    """
    output = tmp_path / "race.mp4"

    class ConcurrentWriterSession(FakeSession):
        def get(self, url, **kwargs):
            response = super().get(url, **kwargs)
            if url.endswith("/video"):
                original = response.iter_content

                def concurrent_stream(chunk_size):
                    output.write_bytes(b"another-operator-output")
                    yield from original(chunk_size)

                response.iter_content = concurrent_stream
            return response

    session = ConcurrentWriterSession(states=("succeeded",))
    with pytest.raises(AcceptanceError, match="already exists"):
        verify(session, "not-a-real-secret", resume=ID, output=output)
    assert output.read_bytes() == b"another-operator-output"
    assert session.download.closed
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.parametrize("cache_control", [
    "public, no-store",
    "private, no-store-false",
    "private, x-no-store",
])
def test_owner_status_rejects_public_or_fake_no_store_cache_directives(tmp_path, cache_control):
    """The authenticated status probe must not accept public or fake no-store.

    Before the repair, a substring check accepted all three and proceeded to
    download a private MP4, falsely reporting a secure owner acceptance.
    """
    class MisleadingStatusSession(FakeSession):
        def get(self, url, **kwargs):
            if url.endswith("/" + ID):
                return Response(
                    data={"video": {"status": "succeeded"}},
                    headers={"Cache-Control": cache_control},
                )
            return super().get(url, **kwargs)

    session = MisleadingStatusSession(states=("succeeded",))
    output = tmp_path / "misleading-cache.mp4"
    with pytest.raises(AcceptanceError, match="cache"):
        verify(session, "not-a-real-secret", resume=ID, output=output)
    assert session.posts == [BASE + "/api/v1/video-owner-login"]
    assert session.download is None, "never download after unsafe status headers"
    assert not output.exists()
