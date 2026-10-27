"""Offline acceptance regressions: existing master + three owner-only export ZIPs.

These tests never contact Render, authenticate, or start a video render.
"""
import io
import hashlib
import json
from types import SimpleNamespace
import zipfile

import pytest

from scripts.owner_existing_export_acceptance import AcceptanceError, verify_existing_exports, inspect_master_media, _verify_kit

BASE = "https://trafficlift-pro.onrender.com"
JOB = "d" * 32
MASTER = b"\x00\x00\x00\x18ftypisom" + b"fixture-video-data" * 100


def kit(platform, *, video=MASTER, job=JOB):
    labels = {"tiktok": "TikTok", "youtube-shorts": "YouTube Shorts",
              "instagram-reels": "Instagram Reels"}
    tags = {"tiktok": "#ProductFinds #ShoppingTips",
            "youtube-shorts": "#Shorts #ProductFinds",
            "instagram-reels": "#Reels #ProductFinds"}
    description = "Desk Organizer — a closer look.\n" + tags[platform]
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("video.mp4", video)
        archive.writestr("manifest.json", json.dumps({
            "platform": platform, "platform_name": labels[platform],
            "job_id": job, "title": "Desk Organizer", "description": description,
            "video": "video.mp4", "video_master_reused": True,
            "video_reencoded": False,
            "safe_zone_approval": "MANUAL_REVIEW_REQUIRED",
            "marketing_approval": "MANUAL_REVIEW_REQUIRED",
            "safe_zone_preview": "safe_zone_review.html",
        }))
        archive.writestr("safe_zone_review.html",
                         '<video src="video.mp4"></video>Illustrative caution overlays; manual review')
        archive.writestr("post.txt",
                         f"{labels[platform]} posting kit — review before publishing\n"
                         "Title: Desk Organizer\nDescription:\n"
                         + description + "\nVideo: video.mp4 is an exact copy of the finished master.")
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
    def __init__(self, *, bad_platform=None, bad_video=None, denied_platform=None,
                 cache="private, no-store", public=False, wrong_job=False):
        self.bad_platform = bad_platform
        self.bad_video = bad_video
        self.denied_platform = denied_platform
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
            status = 401 if platform == self.denied_platform else 200
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


def test_existing_master_matches_all_three_kits_without_render(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.owner_existing_export_acceptance.inspect_master_media', lambda path: {'video': 'h264', 'audio': 'aac', 'width': 1080, 'height': 1920})
    session = Session()
    result = verify_existing_exports(session, "fixture-secret", JOB, temp_root=tmp_path)
    assert result["job_id"] == JOB
    assert result["platforms"] == ["tiktok", "youtube-shorts", "instagram-reels"]
    assert result["master_bytes"] == len(MASTER)
    assert session.posts == [BASE + "/api/v1/video-owner-login"]
    assert len(session.requests) == 5  # anonymous history + master + 3 ZIPs
    assert all(response.closed for response in session.responses)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("problem", [
    {"bad_platform": "youtube-shorts", "bad_video": b"different-master"},
    {"denied_platform": "instagram-reels"},
    {"cache": "public, no-store"},
    {"public": True},
    {"wrong_job": True},
])
def test_existing_export_verification_fails_closed_without_render(tmp_path, problem, monkeypatch):
    monkeypatch.setattr('scripts.owner_existing_export_acceptance.inspect_master_media', lambda path: {'video': 'h264', 'audio': 'aac', 'width': 1080, 'height': 1920})
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


@pytest.mark.parametrize("streams", [
    [{"codec_type": "video", "codec_name": "hevc", "width": 1080, "height": 1920},
     {"codec_type": "audio", "codec_name": "aac"}],
    [{"codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280},
     {"codec_type": "audio", "codec_name": "aac"}],
    [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920}],
    [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
     {"codec_type": "audio", "codec_name": "mp3"}],
])
def test_existing_master_rejects_wrong_codec_resolution_or_missing_audio(monkeypatch, tmp_path, streams):
    def fake_run(*args, **kwargs):
        assert args[0][0] == "ffprobe"
        assert kwargs["timeout"] <= 30
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "streams": streams, "format": {"duration": "31.0"}
        }), stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(AcceptanceError):
        inspect_master_media(tmp_path / "existing.mp4")


def test_existing_master_accepts_h264_aac_1080x1920(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "avg_frame_rate": "30/1"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
            ], "format": {"duration": "31.0"}
        }), stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)
    assert inspect_master_media(tmp_path / "existing.mp4") == {
        "video": "h264", "audio": "aac", "width": 1080, "height": 1920
    }


def test_existing_export_acceptance_fails_closed_if_master_probe_rejects(monkeypatch, tmp_path):
    def reject(_path):
        raise AcceptanceError("Wrong codec")
    monkeypatch.setattr("scripts.owner_existing_export_acceptance.inspect_master_media", reject)
    session = Session()
    with pytest.raises(AcceptanceError, match="Wrong codec"):
        verify_existing_exports(session, "fixture-secret", JOB, temp_root=tmp_path)
    assert len(session.requests) == 2  # anonymous check and master only
    assert all(response.closed for response in session.responses)
    assert list(tmp_path.iterdir()) == []


# The kit's MP4 can be byte-perfect while its customer-facing posting draft is
# empty, mislabelled or inconsistent with the manifest. Reject such packages.
def _posting_kit(tmp_path, *, changes=None, post=None):
    manifest = {
        "platform": "tiktok", "platform_name": "TikTok", "job_id": JOB,
        "title": "Desk Organizer",
        "description": "Desk Organizer — a closer look.\n#ProductFinds #ShoppingTips",
        "video": "video.mp4", "video_master_reused": True,
        "video_reencoded": False, "safe_zone_approval": "MANUAL_REVIEW_REQUIRED",
        "marketing_approval": "MANUAL_REVIEW_REQUIRED",
        "safe_zone_preview": "safe_zone_review.html",
    }
    manifest.update(changes or {})
    if post is None:
        post = ("TikTok posting kit — review before publishing\n"
                "Title: Desk Organizer\nDescription:\n"
                "Desk Organizer — a closer look.\n#ProductFinds #ShoppingTips\n"
                "Video: video.mp4 is an exact copy of the finished master.")
    path = tmp_path / "posting.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("video.mp4", MASTER)
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("post.txt", post)
        archive.writestr("safe_zone_review.html",
                         '<video src="video.mp4"></video>Illustrative; manual review')
    return path


def test_existing_export_acceptance_validates_real_posting_copy(tmp_path):
    path = _posting_kit(tmp_path)
    _verify_kit(path, "tiktok", JOB, len(MASTER), hashlib.sha256(MASTER).hexdigest())


@pytest.mark.parametrize("changes,post", [
    ({"title": ""}, None),
    ({"description": ""}, None),
    ({"platform_name": "Instagram Reels"}, None),
    ({"video": "different.mp4"}, None),
    ({"marketing_approval": "APPROVED"}, None),
    ({}, ""),
    ({}, "Generic posting copy without the product or description"),
])
def test_existing_export_acceptance_rejects_empty_or_inconsistent_posting_metadata(
        tmp_path, changes, post):
    path = _posting_kit(tmp_path, changes=changes, post=post)
    with pytest.raises(AcceptanceError):
        _verify_kit(path, "tiktok", JOB, len(MASTER), hashlib.sha256(MASTER).hexdigest())


# A correct H.264/AAC codec and 9:16 resolution are insufficient for a
# cross-platform posting master. Official TikTok and Meta guidance require
# 23-60 FPS; Meta's Reels audio guidance limits AAC to 48 kHz mono/stereo.
# YouTube Shorts classification is limited to 3 minutes. The 31s master
# should pass all these constraints without creating any new video.
@pytest.mark.parametrize("changes,format_changes", [
    ({"avg_frame_rate": "15/1"}, {}),
    ({"avg_frame_rate": "120/1"}, {}),
    ({"avg_frame_rate": "0/0"}, {}),
    ({"sample_rate": "96000"}, {}),
    ({"channels": 6}, {}),
    ({}, {"duration": "2.9"}),
    ({}, {"duration": "181"}),
])
def test_existing_master_rejects_platform_incompatible_fps_audio_or_duration(
        monkeypatch, tmp_path, changes, format_changes):
    video = {"codec_type": "video", "codec_name": "h264", "width": 1080,
             "height": 1920, "avg_frame_rate": "30/1"}
    audio = {"codec_type": "audio", "codec_name": "aac",
             "sample_rate": "48000", "channels": 2}
    (audio if "sample_rate" in changes or "channels" in changes else video).update(changes)
    fmt = {"duration": "31.0", **format_changes}
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "streams": [video, audio], "format": fmt,
        }), stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(AcceptanceError):
        inspect_master_media(tmp_path / "existing.mp4")
