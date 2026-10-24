"""Platform ZIP integration: owner-only, no paid calls and no extra render."""
import asyncio
import io
import json
import threading
import zipfile

import pytest
from fastapi import HTTPException, Request

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import photo_video_api as api
from backend.media_exports import PLATFORMS, captions_to_srt

HOST = "https://trafficlift-pro.onrender.com"
SECRET = "test-only-media-export-secret-" + "x" * 40


def _setup(monkeypatch, tmp_path, status="succeeded"):
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN", SECRET)
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR", str(tmp_path))
    monkeypatch.delenv("TRAFFICLIFT_LEGACY_VIDEO_DIR", raising=False)
    job_id = "c" * 32
    folder = tmp_path / job_id
    folder.mkdir()
    mp4 = b"original-master-mp4-fixture" * 40
    (folder / "video.mp4").write_bytes(mp4)
    (folder / "captions.ass").write_text(
        "[Events]\nDialogue: 0,0:00:01.20,0:00:02.50,Default,,0,0,0,,Hello\\Nworld!\n",
        encoding="utf-8",
    )
    record = {
        "id": job_id, "name": "Desk Organizer", "status": status,
        "product_url": "https://example.com/desk-organizer",
        "video_url": f"/api/v1/photo-videos/{job_id}/video" if status == "succeeded" else None,
    }
    api.save(record, folder)
    app = FastAPI()
    app.include_router(api.router)
    return app, job_id, mp4, folder


def test_three_authenticated_kits_copy_one_master_without_rendering(monkeypatch, tmp_path):
    app, job_id, mp4, folder = _setup(monkeypatch, tmp_path)
    # An export must never consume a render quota or start a worker.
    monkeypatch.setattr(api, "reserve_hosted_render", lambda: (_ for _ in ()).throw(AssertionError("quota used")))
    monkeypatch.setattr(api, "worker", lambda *a: (_ for _ in ()).throw(AssertionError("rerendered")))
    with TestClient(app, base_url=HOST) as owner, TestClient(app, base_url=HOST) as stranger:
        assert owner.post("/api/v1/video-owner-login", json={"access_code": SECRET},
                          headers={"origin": HOST}).status_code == 200
        for platform in PLATFORMS:
            url = f"/api/v1/photo-videos/{job_id}/exports/{platform}"
            assert stranger.get(url).status_code == 401
            response = owner.get(url)
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("application/zip")
            assert "no-store" in response.headers["cache-control"]
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert archive.read("video.mp4") == mp4
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["platform"] == platform
                assert manifest["video_reencoded"] is False
                assert manifest["video_master_reused"] is True
                assert manifest["safe_zone_approval"] == "MANUAL_REVIEW_REQUIRED"
                assert "seller listing" in archive.read("post.txt").decode()
                assert "00:00:01,200 --> 00:00:02,500" in archive.read("captions.srt").decode()
                assert "Hello\nworld!" in archive.read("captions.srt").decode()
        assert owner.get(f"/api/v1/photo-videos/{job_id}/exports/unknown").status_code == 404
        assert owner.get("/api/v1/photo-videos/not-a-job/exports/tiktok").status_code == 404
        assert (folder / "video.mp4").read_bytes() == mp4


def test_no_export_until_video_succeeds(monkeypatch, tmp_path):
    app, job_id, _, folder = _setup(monkeypatch, tmp_path, status="failed")
    with TestClient(app, base_url=HOST) as owner:
        assert owner.post("/api/v1/video-owner-login", json={"access_code": SECRET},
                          headers={"origin": HOST}).status_code == 200
        assert owner.get(f"/api/v1/photo-videos/{job_id}/exports/tiktok").status_code == 409
        record = api.read_record(job_id)
        record["status"] = "succeeded"
        api.save(record, folder)
        (folder / "video.mp4").unlink()
        assert owner.get(f"/api/v1/photo-videos/{job_id}/exports/tiktok").status_code == 404


def test_hosted_mode_disabled_never_exposes_export(monkeypatch, tmp_path):
    app, job_id, _, _ = _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO", "0")
    monkeypatch.setenv("TRAFFICLIFT_FREE_VIDEO", "0")
    with TestClient(app, base_url=HOST) as client:
        assert client.get(f"/api/v1/photo-videos/{job_id}/exports/tiktok").status_code == 503


def test_caption_converter_fails_closed_on_missing_file(tmp_path):
    assert captions_to_srt(tmp_path / "absent.ass") == ""


@pytest.mark.parametrize("disconnect", [False, True])
def test_export_slot_and_temp_file_survive_until_stream_end(monkeypatch, tmp_path, disconnect):
    """Second kit must be rejected while first is streaming, even after ZIP build."""
    _, job_id, mp4, _ = _setup(monkeypatch, tmp_path)
    export_lock = threading.BoundedSemaphore(1)
    monkeypatch.setattr(api, "EXPORT_LOCK", export_lock)
    path = f"/api/v1/photo-videos/{job_id}/exports/tiktok"
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "https", "server": ("trafficlift-pro.onrender.com", 443),
        "path": path, "client": ("testclient", 12345),
        "headers": [(b"host", b"trafficlift-pro.onrender.com"),
                    (b"x-trafficlift-video-token", SECRET.encode())],
    }
    request = Request(scope)
    response = api.platform_export(job_id, "tiktok", request)
    archive_path = response.path
    assert archive_path.is_file()
    assert not export_lock.acquire(blocking=False)
    with pytest.raises(HTTPException) as blocked:
        api.platform_export(job_id, "youtube-shorts", request)
    assert blocked.value.status_code == 429

    chunks = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        if disconnect:
            raise RuntimeError("client disconnected")
        chunks.append(message)
    if disconnect:
        with pytest.raises(RuntimeError, match="client disconnected"):
            asyncio.run(response(scope, receive, send))
    else:
        asyncio.run(response(scope, receive, send))
        assert any(message.get("body") for message in chunks)
    assert not archive_path.exists(), "temporary ZIP must be removed"
    assert export_lock.acquire(blocking=False), "slot must be released after stream or disconnect"
    export_lock.release()
