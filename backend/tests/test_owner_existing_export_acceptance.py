"""Offline acceptance regressions: existing master + three owner-only export ZIPs.

These tests never contact Render, authenticate, or start a video render.
"""
import io
import json
import zipfile

import pytest

from scripts.owner_existing_export_acceptance import AcceptanceError, verify_existing_exports

BASE = "https://trafficlift-pro.onrender.com"
JOB = "d" * 32
MASTER = b"\x00\x00\x00\x18ftypisom" + b"fixture-video-data" * 100


def kit(platform, *, video=MASTER, job=JOB):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("video.mp4", video)
        archive.writestr("manifest.json", json.dumps({
            "platform": platform, "job_id": job,
            "video_master_reused": True, "video_reencoded": False,
            "safe_zone_approval": "MANUAL_REVIEW_REQUIRED",
            "safe_zone_preview": "safe_zone_review.html",
        }))
        archive.writestr("safe_zone_review.html",
                         '<video src="video.mp4"></video>Illustrative caution overlays; manual review')
        archive.writestr("post.txt", "Review before publishing")
    return data.getvalue()


class Response:
    def __init__(self, status=200, content=b"", *, cache="private, no-store",
                 content_type="application/zip", cookie=""):
        self.status_code = status
        self.content = content
        self.headers = {"Cache-Control": cache, "Content-Type": content_type,
                        "Set-Cookie": cookie}
        self.closed = False

    def iter_content(self, chunk_size=1024 * 1024):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Session:
    def __init__(self, *, bad_platform=None, bad_video=None, cache="private, no-store",
                 public=False, wrong_job=False):
        self.bad_platform = bad_platform
        self.bad_video = bad_video
        self.cache = cache
        self.public = public
        self.wrong_job = wrong_job
        self.posts = []
        self.requests = []
        self.responses = []

    def get(self, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        assert kwargs.get("stream") is True
        self.requests.append(url)
        if url.endswith("/photo-videos") and self.public:
            response = Response(200)
        elif url.endswith("/photo-videos"):
            response = Response(401)
        elif url.endswith("/video"):
            response = Response(200, MASTER, cache=self.cache, content_type="video/mp4")
        else:
            platform = url.rsplit("/", 1)[-1]
            status = 401 if platform == self.bad_platform else 200
            video = self.bad_video if platform == self.bad_platform and self.bad_video is not None else MASTER
            response = Response(status, kit(platform, video=video,
                                           job="e" * 32 if self.wrong_job else JOB),
                                cache=self.cache)
        self.responses.append(response)
        return response

    def post(self, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        assert kwargs["headers"]["Origin"] == BASE
        assert kwargs["json"] == {"access_code": "fixture-secret"}
        self.posts.append(url)
        response = Response(200, cookie="trafficlift_video_session=fake; Secure; HttpOnly; SameSite=Strict")
        self.responses.append(response)
        return response


def test_existing_master_matches_all_three_kits_without_render(tmp_path):
    session = Session()
    result = verify_existing_exports(session, "fixture-secret", JOB, temp_root=tmp_path)
    assert result["job_id"] == JOB
    assert result["platforms"] == ["tiktok", "youtube-shorts", "instagram-reels"]
    assert result["master_bytes"] == len(MASTER)
    assert session.posts == [BASE + "/api/v1/video-owner-login"]
    assert len(session.requests) == 5  # anonymous history + master + 3 ZIPs
    assert all(response.closed for response in session.responses if response in session.responses)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("problem", [
    {"bad_platform": "youtube-shorts", "bad_video": b"different-master"},
    {"bad_platform": "instagram-reels"},
    {"cache": "public, no-store"},
    {"public": True},
    {"wrong_job": True},
])
def test_existing_export_verification_fails_closed_without_render(tmp_path, problem):
    session = Session(**problem)
    with pytest.raises(AcceptanceError):
        verify_existing_exports(session, "fixture-secret", JOB, temp_root=tmp_path)
    assert not any(url.endswith("/photo-videos") for url in session.posts)
    assert all(response.closed for response in session.responses)
    assert list(tmp_path.iterdir()) == []


def test_invalid_job_or_missing_code_never_contacts_host(tmp_path):
    session = Session()
    with pytest.raises(AcceptanceError):
        verify_existing_exports(session, "fixture-secret", "../bad", temp_root=tmp_path)
    with pytest.raises(AcceptanceError):
        verify_existing_exports(session, "", JOB, temp_root=tmp_path)
    assert session.posts == [] and session.requests == []
