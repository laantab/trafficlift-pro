"""Hosted preparation has no effect until explicitly enabled."""
import json
from pathlib import Path
from unittest.mock import patch
from backend.hosted_video_bootstrap import provision


def test_not_prepared_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("TRAFFICLIFT_HOSTED_VIDEO_PREPARE", raising=False)
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_MODEL_DIR", str(tmp_path))
    assert provision() is False
    assert list(tmp_path.iterdir()) == []


def test_preparation_checks_voice_and_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_PREPARE", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_MODEL_DIR", str(tmp_path))
    def install(path):
        (path / "kokoro-v1.0.int8.onnx").write_bytes(b"test")
        (path / "voices-v1.0.bin").write_bytes(b"test")
    with patch("scripts.install_hosted_voice_assets.install", side_effect=install), \
         patch("static_ffmpeg.add_paths") as add_paths, \
         patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run") as run:
        assert provision() is True
    add_paths.assert_called_once_with(weak=True)
    assert run.call_count == 2
    status=json.loads((tmp_path / "provision-status.json").read_text())
    assert status["state"]=="ready"
    assert (tmp_path / "kokoro-v1.0.int8.onnx").exists()


def test_preparation_failure_is_not_reported_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_PREPARE", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_MODEL_DIR", str(tmp_path))
    with patch("scripts.install_hosted_voice_assets.install", side_effect=ValueError("bad checksum")):
        assert provision() is False
    status=json.loads((tmp_path / "provision-status.json").read_text())
    assert status["state"]=="failed"


def test_optin_server_mp4_smoke_records_success(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_PREPARE", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_SMOKE", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_MODEL_DIR", str(tmp_path))
    with patch("scripts.install_hosted_voice_assets.install"), \
         patch("static_ffmpeg.add_paths"), \
         patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run", return_value=SimpleNamespace(returncode=0,stdout="PASS",stderr="")) as run:
        assert provision() is True
    assert run.call_count == 3
    status=json.loads((tmp_path / "provision-status.json").read_text())
    assert status["state"]=="ready"
    assert "MP4 acceptance passed" in status["detail"]


def test_optin_server_mp4_failure_fails_closed(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_PREPARE", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_SMOKE", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_MODEL_DIR", str(tmp_path))
    with patch("scripts.install_hosted_voice_assets.install"), \
         patch("static_ffmpeg.add_paths"), \
         patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run", side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=0), SimpleNamespace(returncode=1,stderr="MP4 failed",stdout="")]):
        assert provision() is False
    status=json.loads((tmp_path / "provision-status.json").read_text())
    assert status["state"]=="failed"
