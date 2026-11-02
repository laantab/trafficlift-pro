"""Premium Product Ad safety tests: no paid provider access and exact seller facts."""
from unittest.mock import patch, Mock
from pathlib import Path
import io
import json
import subprocess
import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.premium_ad_api import (
    _job_folder, _probe, _cards, _stream_mp4,
    start, _assemble, PremiumStart
)
from backend.photo_video_api import VideoRequest


def request_for(body=b"", headers=None):
    headers = headers or {"content-type": "video/mp4"}
    used = False

    async def receive():
        nonlocal used
        if used:
            return {"type": "http.request", "body": b"", "more_body": False}
        used = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({
        "type": "http", "method": "PUT", "path": "/",
        "scheme": "https", "server": ("testserver", 443),
        "client": ("testclient", 1),
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
    }, receive=receive)


def sample_product():
    return VideoRequest(
        name="Crosby Schoolhouse Desk Lamp",
        image_url="https://target.scene7.com/is/image/Target/selected-photo",
        product_url="https://www.target.com/p/crosby-schoolhouse-desk-lamp",
        benefit="",
        seconds=15,
    )


def test_premium_preflight_uses_exact_seller_photo_and_never_calls_paid_api(tmp_path):
    plan = {"mode": "benefit_led", "evidence": [
        {"text": "Adjustable stand"}, {"text": "Metal base"}]}
    photo = "https://target.scene7.com/is/image/Target/verified-photo"
    with patch("backend.premium_ad_api.video_guard"), \
         patch("backend.premium_ad_api.storage", return_value=tmp_path), \
         patch("backend.premium_ad_api.dependencies"), \
         patch("backend.premium_ad_api.prepare_video_plan", return_value=(plan, [photo])), \
         patch("backend.premium_ad_api.marketing_readiness_gate"), \
         patch("backend.wan_video_api.fal_client.submit") as paid:
        # fal_client is deliberately not imported by Premium Product Ad.
        result = start(PremiumStart(product=sample_product()), Mock())
    assert result["status"] == "awaiting_upload"
    record = json.loads((tmp_path/"premium"/result["id"]/"job.json").read_text())
    assert record["image_url"] == photo
    assert record["paid_provider_calls"] == 0
    paid.assert_not_called()


def test_premium_preflight_rejects_unverified_photo(tmp_path):
    plan = {"mode": "benefit_led", "evidence": [
        {"text": "Adjustable stand"}, {"text": "Metal base"}]}
    with patch("backend.premium_ad_api.video_guard"), \
         patch("backend.premium_ad_api.storage", return_value=tmp_path), \
         patch("backend.premium_ad_api.dependencies"), \
         patch("backend.premium_ad_api.prepare_video_plan", return_value=(plan, [])), \
         patch("backend.premium_ad_api.marketing_readiness_gate"):
        with pytest.raises(HTTPException) as exc:
            start(PremiumStart(product=sample_product()), Mock())
    assert exc.value.status_code == 422
    assert not list(tmp_path.glob("premium/*/job.json"))


def test_premium_stream_limits_mp4(tmp_path):
    target = tmp_path/"demo.mp4"
    from backend.premium_ad_api import _MAX_BYTES
    req = request_for(b"x" * (_MAX_BYTES+1))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_stream_mp4(req, target))
    assert exc.value.status_code == 413
    assert not target.exists()


def test_premium_stream_rejects_non_mp4(tmp_path):
    target = tmp_path/"demo.mp4"
    req = request_for(b"not a real mp4" * 300)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_stream_mp4(req, target))
    assert exc.value.status_code == 422


def test_premium_cards_use_exact_photo_without_distorting_shape(tmp_path):
    from PIL import Image
    image = tmp_path/"lamp.png"
    Image.new("RGB", (500, 900), (110, 80, 60)).save(image)
    _cards(tmp_path, image, {"name": "Crosby Schoolhouse Desk Lamp"}, ["a", "Metal base.", "c"])
    for name in ["intro.png", "outro.png", "caption.png"]:
        assert (tmp_path/name).is_file()
        with Image.open(tmp_path/name) as pic:
            assert pic.size == (720,1280)


def test_premium_full_15_second_mp4_composes_without_api_credits(tmp_path):
    """Real ffmpeg test with a synthetic human-stage stand-in and silent WAV.

    Runs all three scenes through the final compositor. Media is synthetic;
    appearance and audio quality still require a real user footage review.
    """
    import shutil
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg is required to verify final production composition")
    import numpy as np
    import soundfile as sf
    from PIL import Image
    source = tmp_path/"demo.mp4"
    try:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=360x640:r=24:d=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)
        ], check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("synthetic fixture encoder is unavailable in this runner")
    still = tmp_path/"lamp.jpg"
    Image.new("RGB", (500, 900), (70, 85, 95)).save(still)
    record = {
        "id": "a"*32, "name": "Crosby Schoolhouse Desk Lamp",
        "image_url": "https://target.scene7.com/is/image/Target/verified",
        "plan": {"evidence": [
            {"text": "Adjustable metal stand"}, {"text": "Metal base construction"}]},
    }
    def fake_voice(folder, captions):
        sf.write(folder/"voice.wav", np.zeros(24000*15, dtype=np.float32), 24000)
    with patch("backend.premium_ad_api.dependencies"), \
         patch("backend.photo_video.dependencies"), \
         patch("backend.premium_ad_api._download_product_image", return_value=still), \
         patch("backend.premium_ad_api._voice", side_effect=fake_voice), \
         patch("backend.premium_ad_api._probe", wraps=_probe):
        quality = _assemble(tmp_path, record)
    assert (tmp_path/"video.mp4").stat().st_size > 100000
    assert quality["width"] == 720
    assert quality["height"] == 1280
    assert 14.5 <= quality["duration"] <= 15.5
    receipt = json.loads((tmp_path/"quality.json").read_text())
    assert receipt["publication_approved"] is False
    assert receipt["paid_provider_calls"] == 0
