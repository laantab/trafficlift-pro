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
    def fake_run(command, **kwargs):
        if "-m" in command:
            output = Path(kwargs["env"]["TRAFFICLIFT_TEST_OUTPUT_DIR"])
            (output / "trafficlift-phase2-real-kokoro.mp4").write_bytes(b"real-smoke-fixture" * 1000)
            (output / "trafficlift-phase2-quality.json").write_text(json.dumps({
                "status": "PASS", "motion": {"status": "PASS"},
                "narration_audio": {"status": "PASS"},
            }))
        return SimpleNamespace(returncode=0, stdout="PASS", stderr="")

    with patch("scripts.install_hosted_voice_assets.install"), \
         patch("static_ffmpeg.add_paths"), \
         patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run", side_effect=fake_run) as run:
        assert provision() is True
    assert run.call_count == 3
    status=json.loads((tmp_path / "provision-status.json").read_text())
    assert status["state"]=="ready"
    assert "MP4 acceptance passed" in status["detail"]
    receipt=json.loads((tmp_path / "owner-acceptance" / "receipt.json").read_text())
    assert receipt["state"] == "passed"
    assert receipt["sample_only"] is True
    assert (tmp_path / "owner-acceptance" / "real-kokoro.mp4").stat().st_size > 10000


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
