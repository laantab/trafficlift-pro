"""Hosted video dependencies are present without activating public rendering."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import pytest
from backend.photo_video import dependencies


def test_video_python_packages_installed():
    for package in ("kokoro_onnx", "onnxruntime", "soundfile", "numpy", "PIL", "static_ffmpeg"):
        assert importlib.util.find_spec(package) is not None, package


def test_cloud_video_ffmpeg_loader_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO", "1")
    monkeypatch.setenv("TRAFFICLIFT_VOICE_MODEL", str(tmp_path / "missing.onnx"))
    monkeypatch.setenv("TRAFFICLIFT_VOICES", str(tmp_path / "missing.bin"))
    with patch("static_ffmpeg.add_paths") as install:
        with pytest.raises(ValueError, match="voice setup"):
            dependencies()
    install.assert_called_once_with(weak=True)


def test_local_video_avoids_cloud_downloader(monkeypatch, tmp_path):
    monkeypatch.delenv("TRAFFICLIFT_HOSTED_VIDEO", raising=False)
    monkeypatch.setenv("TRAFFICLIFT_VOICE_MODEL", str(tmp_path / "missing.onnx"))
    monkeypatch.setenv("TRAFFICLIFT_VOICES", str(tmp_path / "missing.bin"))
    with patch("static_ffmpeg.add_paths") as install:
        with pytest.raises(ValueError, match="voice setup"):
            dependencies()
    install.assert_not_called()
