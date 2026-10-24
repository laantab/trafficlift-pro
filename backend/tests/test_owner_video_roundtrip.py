"""Exercise the protected hosted owner API from login to MP4 download.

This is an HTTP/record/download integration test with a fake worker and
fixture bytes. Real Kokoro/FFmpeg composition has its own separate smoke test.
No external APIs, real rendering, or hosted configuration are used here.
"""
import time
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import photo_video_api as api


HOST = "https://trafficlift-pro.onrender.com"
SECRET = "test-only-owner-secret-" + "x" * 40


def test_hosted_storage_defaults_to_persistent_disk(monkeypatch):
    monkeypatch.delenv("TRAFFICLIFT_VIDEO_DIR", raising=False)
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO", "1")
    assert api.storage() == Path("/var/data/trafficlift-videos")
    monkeypatch.delenv("TRAFFICLIFT_HOSTED_VIDEO", raising=False)
    assert api.storage() == (api.ROOT / "video/studio").resolve()


def test_owner_login_create_status_history_and_mp4_download(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN", SECRET)
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_DAILY_LIMIT", "1")
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR", str(tmp_path))
    monkeypatch.delenv("TRAFFICLIFT_LEGACY_VIDEO_DIR", raising=False)
    monkeypatch.setattr(api, "dependencies", lambda: None)

    fixture_mp4 = b"fixture-only-not-a-real-mp4"
    def fake_worker(record, payload, directory):
        try:
            (directory / "video.mp4").write_bytes(fixture_mp4)
            record.update(
                status="succeeded",
                message="Fixture complete",
                video_url=f"/api/v1/photo-videos/{record['id']}/video",
            )
            api.save(record, directory)
        finally:
            api.lock.release()

    monkeypatch.setattr(api, "worker", fake_worker)
    app = FastAPI()
    app.include_router(api.router)
    payload = {
        "name": "Desk Organizer",
        "image_url": "https://example.com/organizer.jpg",
        "product_url": "https://example.com/organizer",
        "benefit": "Keeps supplies organized",
        "seconds": 15,
        "style": "clean",
    }

    try:
        with TestClient(app, base_url=HOST) as owner, TestClient(app, base_url=HOST) as stranger:
            assert stranger.get("/api/v1/photo-videos").status_code == 401
            assert stranger.post("/api/v1/photo-videos", json=payload).status_code == 401

            login = owner.post(
                "/api/v1/video-owner-login",
                json={"access_code": SECRET},
                headers={"origin": HOST},
            )
            assert login.status_code == 200
            assert "httponly" in login.headers["set-cookie"].lower()
            assert "secure" in login.headers["set-cookie"].lower()

            created = owner.post(
                "/api/v1/photo-videos",
                json=payload,
                headers={"origin": HOST},
            )
            assert created.status_code == 202, created.text
            job_id = created.json()["video"]["id"]
            assert len(job_id) == 32

            status_url = f"/api/v1/photo-videos/{job_id}"
            for _ in range(100):
                response = owner.get(status_url)
                assert response.status_code == 200
                if response.json()["video"]["status"] == "succeeded":
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("Fixture worker never completed")

            assert any(job["id"] == job_id for job in owner.get("/api/v1/photo-videos").json()["videos"])
            download_url = status_url + "/video"
            downloaded = owner.get(download_url)
            assert downloaded.status_code == 200
            assert downloaded.headers["content-type"].startswith("video/mp4")
            assert downloaded.content == fixture_mp4
            assert "attachment" in downloaded.headers["content-disposition"].lower()

            assert stranger.get(status_url).status_code == 401
            assert stranger.get(download_url).status_code == 401
            assert owner.post("/api/v1/photo-videos", json=payload, headers={"origin": HOST}).status_code == 429
            assert (tmp_path / job_id / "video.mp4").is_file()
    finally:
        if api.lock.locked():
            api.lock.release()


def test_hosted_acceptance_download_is_owner_only(monkeypatch, tmp_path):
    """Only the authenticated owner can inspect/download a genuine smoke receipt."""
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN", SECRET)
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_MODEL_DIR", str(tmp_path))
    acceptance = tmp_path / "owner-acceptance"
    acceptance.mkdir()
    sample = b"hosted-real-render-sample" * 1000
    (acceptance / "real-kokoro.mp4").write_bytes(sample)
    (acceptance / "receipt.json").write_text(json.dumps({
        "state": "passed", "checked_at": "2026-10-24T12:00:00Z",
        "video_size_bytes": len(sample), "sample_only": True,
    }))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app, base_url=HOST) as owner, TestClient(app, base_url=HOST) as stranger:
        assert stranger.get("/api/v1/video-owner-acceptance").status_code == 401
        assert stranger.get("/api/v1/video-owner-acceptance/mp4").status_code == 401
        assert owner.post("/api/v1/video-owner-login", json={"access_code": SECRET},
                          headers={"origin": HOST}).status_code == 200
        info = owner.get("/api/v1/video-owner-acceptance")
        assert info.status_code == 200
        assert info.json()["state"] == "passed"
        assert info.json()["sample_only"] is True
        downloaded = owner.get("/api/v1/video-owner-acceptance/mp4")
        assert downloaded.status_code == 200
        assert downloaded.content == sample
        assert downloaded.headers["content-type"].startswith("video/mp4")
        assert "no-store" in downloaded.headers["cache-control"]
        (acceptance / "receipt.json").unlink()
        assert owner.get("/api/v1/video-owner-acceptance").json()["state"] == "not_run"
        assert owner.get("/api/v1/video-owner-acceptance/mp4").status_code == 404


def test_hosted_acceptance_is_unavailable_when_disabled(monkeypatch):
    monkeypatch.delenv("TRAFFICLIFT_HOSTED_VIDEO", raising=False)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app, base_url=HOST) as client:
        assert client.get("/api/v1/video-owner-acceptance").status_code == 503
        assert client.get("/api/v1/video-owner-acceptance/mp4").status_code == 503


def test_interrupted_job_remains_failed_while_another_render_runs(monkeypatch, tmp_path):
    """A newer job must never resurrect an older interrupted render."""
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR", str(tmp_path))
    monkeypatch.delenv("TRAFFICLIFT_LEGACY_VIDEO_DIR", raising=False)
    stale_id, current_id = "a" * 32, "b" * 32
    for job_id in (stale_id, current_id):
        folder = tmp_path / job_id
        folder.mkdir()
        api.save({
            "id": job_id, "name": "Test", "status": "running",
            "message": "Rendering", "video_url": "/api/v1/photo-videos/" + job_id + "/video",
        }, folder)

    assert api.lock.acquire(blocking=False)
    monkeypatch.setattr(api, "active_job_id", current_id)
    try:
        stale = api.read_record(stale_id)
        assert stale["status"] == "failed"
        assert stale["video_url"] is None
        assert json.loads((tmp_path / stale_id / "job.json").read_text())["status"] == "failed"
        assert api.read_record(current_id)["status"] == "running"
    finally:
        api.lock.release()

    monkeypatch.setattr(api, "active_job_id", None)
    assert api.read_record(stale_id)["status"] == "failed"
    assert api.read_record(current_id)["status"] == "failed"
    assert json.loads((tmp_path / current_id / "job.json").read_text())["status"] == "failed"
