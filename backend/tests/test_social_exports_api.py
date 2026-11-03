"""Offline checks for social export sizing and owner/provider safeguards."""
from pathlib import Path
from unittest.mock import Mock, patch
import zipfile

import pytest
from fastapi import HTTPException

from backend.social_exports_api import _FORMATS, _public_https, social_exports


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
            social_exports("finished_video_123", request)
        assert error.value.status_code == 401
        provider.assert_not_called()


def test_export_checks_existing_job_and_never_submits_paid_video():
    request = Mock()
    with patch("backend.social_exports_api.video_guard"), \
         patch("backend.social_exports_api.configured", return_value=True), \
         patch("backend.social_exports_api.provider_request",
               return_value={"status": "processing"}) as provider:
        with pytest.raises(HTTPException) as error:
            social_exports("finished_video_123", request)
        assert error.value.status_code == 409
        assert provider.call_args.args[0] == "GET"


def test_three_complete_mp4s_are_packaged_from_one_source(tmp_path):
    request = Mock()
    produced = []

    def fake_download(url, target):
        Path(target).write_bytes(b"source bytes")

    def fake_convert(source, destination, width, height):
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
         patch("backend.social_exports_api._convert", side_effect=fake_convert):
        response = social_exports("finished_video_123", request)
        assert produced == [(720, 1280), (864, 1080), (768, 768)]
        assert response.filename.endswith(".zip")
        with zipfile.ZipFile(response.path) as z:
            assert z.namelist() == [entry[0] for entry in _FORMATS]
