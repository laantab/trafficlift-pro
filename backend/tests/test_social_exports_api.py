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
