"""Offline checks for social export sizing and owner/provider safeguards."""
from pathlib import Path
from unittest.mock import Mock, patch
import zipfile

import pytest
from fastapi import HTTPException

from backend.social_exports_api import _FORMATS, _public_https, _marketing_url, ExportOptions, social_exports


def test_three_social_formats_keep_defined_aspect_ratios():
    assert [(w, h) for _, w, h in _FORMATS] == [
        (720, 1280), (864, 1080), (768, 768)
    ]


def test_private_and_plain_http_urls_are_rejected():
    with pytest.raises(HTTPException):
        _public_https("http://example.com/video.mp4")
    with patch("backend.social_exports_api.socket.getaddrinfo",
               return_value=[(None, None, None, None, ("127.0.0.1", 443))]):
        with pytest.raises(HTTPException):
            _public_https("https://example.com/video.mp4")


def test_export_never_runs_without_owner_guard():
    request = Mock()
    with patch("backend.social_exports_api.video_guard",
               side_effect=HTTPException(401, "Owner access needed")), \
         patch("backend.social_exports_api.provider_request") as provider:
        with pytest.raises(HTTPException) as error:
            social_exports("finished_video_123", request, ExportOptions(marketing_url="https://example.com/product"))
        assert error.value.status_code == 401
        provider.assert_not_called()


def test_export_checks_existing_job_and_never_submits_paid_video():
    request = Mock()
    with patch("backend.social_exports_api.video_guard"), \
         patch("backend.social_exports_api.configured", return_value=True), \
         patch("backend.social_exports_api.provider_request",
               return_value={"status": "processing"}) as provider:
        with pytest.raises(HTTPException) as error:
            social_exports("finished_video_123", request, ExportOptions(marketing_url="https://example.com/product"))
        assert error.value.status_code == 409
        assert provider.call_args.args[0] == "GET"


def test_three_complete_mp4s_are_packaged_from_one_source(tmp_path):
    request = Mock()
    produced = []

    def fake_download(url, target):
        Path(target).write_bytes(b"source bytes")

    def fake_convert(source, destination, width, height, card=None, duration=15):
        produced.append((width, height))
        Path(destination).write_bytes(b"0" * 4096)

    with patch("backend.social_exports_api.video_guard"), \
         patch("backend.social_exports_api.configured", return_value=True), \
         patch("backend.social_exports_api.provider_request",
               return_value={"status": "completed",
                             "video_url": "https://media.example.org/finished.mp4"}), \
         patch("backend.social_exports_api.shutil.disk_usage",
               return_value=Mock(free=999999999)), \
         patch("backend.social_exports_api.tempfile.mkdtemp",
               return_value=str(tmp_path)), \
         patch("backend.production_scene_editor.inspect_footage", return_value={"editorial_review":{"decision":"needs_review"}}), \
         patch("backend.social_exports_api._download", side_effect=fake_download), \
         patch("backend.social_exports_api._video_duration", return_value=15), \
         patch("backend.social_exports_api._link_card"), \
         patch("backend.social_exports_api._convert", side_effect=fake_convert):
        response = social_exports("finished_video_123", request, ExportOptions(marketing_url="https://example.com/product"))
        assert produced == [(720, 1280), (864, 1080), (768, 768)]
        assert response.filename.endswith(".zip")
        with zipfile.ZipFile(response.path) as z:
            assert z.namelist() == ["POST_CAPTION_AND_LINK.txt"] + [entry[0] for entry in _FORMATS]
            assert "https://example.com/product" in z.read("POST_CAPTION_AND_LINK.txt").decode()


def test_export_requires_approved_https_marketing_url():
    for url in ("http://example.com/product", "https://localhost/product",
                "https://user:secret@example.com/product"):
        with pytest.raises(HTTPException) as error:
            _marketing_url(url)
        assert error.value.status_code == 422


def test_marketing_link_is_preserved_exactly_for_caption():
    url = "https://example.com/product?tag=affiliate-20&variant=2"
    assert _marketing_url(url) == url


def test_primary_video_engine_is_heygen_and_paid_job_survives_reload():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "index.html").read_text(encoding="utf-8")
    assert '<option value="heygen" selected>' in html
    assert '<option value="krea" selected>' not in html
    assert "document.getElementById('videoProvider').value = 'heygen';" in html
    assert "function readHeyGenPendingVideoId()" in html
    assert "sessionStorage.getItem(HEYGEN_PENDING_JOB_KEY)" in html
    assert "setHeyGenPendingVideoId(data.video_id);" in html
    assert "setHeyGenPendingVideoId(null);" in html
    assert "if (heygenPendingVideoId) {" in html
    assert "await pollHeyGenVideo(heygenPendingVideoId, statusEl, resultEl);" in html


def test_real_social_export_has_vertical_h264_audio_and_safe_link_card(tmp_path):
    """No HeyGen credits: encode an owned synthetic landscape master locally."""
    import json
    import subprocess
    from backend.social_exports_api import _convert, _ffmpeg, _link_card
    ffmpeg = _ffmpeg()
    source = tmp_path / "landscape-source.mp4"
    card = tmp_path / "link-card.png"
    output = tmp_path / "social-vertical.mp4"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=c=red:s=320x240:r=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
        "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source),
    ], capture_output=True, check=True, timeout=35)
    _link_card(card, 720, 1280, "https://example.com/dp/TEST123?tag=approved")
    _convert(source, output, 720, 1280, card=card, duration=2)
    from pathlib import Path
    assert output.is_file() and output.stat().st_size > 2048
    # Find ffprobe alongside static-ffmpeg/ffmpeg, including on hosted CI.
    import shutil
    ffprobe = shutil.which("ffprobe") or str(Path(ffmpeg).with_name("ffprobe"))
    result = subprocess.run([
        ffprobe, "-v", "error", "-show_entries",
        "stream=codec_type,codec_name,width,height",
        "-of", "json", str(output)
    ], check=True, capture_output=True, text=True, timeout=20)
    streams = json.loads(result.stdout)["streams"]
    assert any(x.get("codec_type") == "video" and x.get("codec_name") == "h264"
               and x.get("width") == 720 and x.get("height") == 1280 for x in streams)
    assert any(x.get("codec_type") == "audio" and x.get("codec_name") == "aac" for x in streams)
