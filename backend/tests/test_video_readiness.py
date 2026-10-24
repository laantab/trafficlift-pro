"""Hosted readiness returns state without leaking server details."""
import json
from fastapi.testclient import TestClient
from trafficlift_pro import app


def test_video_readiness_disabled(monkeypatch):
    monkeypatch.delenv("TRAFFICLIFT_HOSTED_VIDEO_PREPARE", raising=False)
    response = TestClient(app).get("/api/v1/video-readiness")
    assert response.status_code == 200
    assert response.json() == {"state": "disabled", "customer_rendering_enabled": False}


def test_video_readiness_reads_persistent_state(monkeypatch,tmp_path):
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_PREPARE", "1")
    monkeypatch.delenv("TRAFFICLIFT_HOSTED_VIDEO", raising=False)
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_MODEL_DIR", str(tmp_path))
    (tmp_path / "provision-status.json").write_text(json.dumps({"state":"ready","detail":"secret filesystem path"}))
    response = TestClient(app).get("/api/v1/video-readiness")
    assert response.json() == {"state":"ready", "customer_rendering_enabled":False}
    assert "secret" not in response.text
