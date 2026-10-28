"""One integrated, owned-fixture release acceptance: designed pin -> narrated master -> owner exports.

No external listing or paid services; local in-process HTTPS TestClient is NOT
an authenticated production session. The MP4 is genuinely rendered by the
separate FFmpeg/Kokoro smoke step, not fabricated in this acceptance test.
"""
from __future__ import annotations
import hashlib
import hmac
import time
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageStat

from backend import photo_video_api as api
from backend.media_exports import PLATFORMS
from backend.sales_script import build_sales_plan, marketing_readiness_gate

HOST = "https://trafficlift-pro.onrender.com"
PRODUCT = "Desk Organizer"
OWNER_SECRET = "owned-fixture-test-only-" + "x" * 40
JOB_ID = "d" * 32
FORBIDDEN = (
    "review listed features", "picture your setup", "key item features",
    "explore tech & gadgets ideas", "looking for a useful addition",
    "warranty and support",
)


def require_media(path: Path, quality: dict):
    assert path.is_file() and path.stat().st_size > 10000
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_name,codec_type,width,height",
         "-of", "json", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    info = json.loads(probe.stdout)
    streams = info["streams"]
    assert any(s.get("codec_type") == "video" and s.get("codec_name") == "h264"
               and s.get("width") == 1080 and s.get("height") == 1920 for s in streams)
    assert any(s.get("codec_type") == "audio" and s.get("codec_name") == "aac"
               for s in streams)
    assert 14 <= float(info["format"]["duration"]) <= 120
    decoded = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180,
    )
    assert decoded.returncode == 0, "Real master failed full video/audio decode"
    assert quality["status"] == "PASS"
    assert quality["rendered_creative"]["status"] == "PASS"
    assert quality["rendered_creative"]["rendered_subtitles"]["status"] == "PASS"
    assert quality["rendered_creative"]["rendered_subtitles"]["sampled_captions"] >= 2
    assert quality["photo_count"] == 1
    assert sum(s["layout"] == "seller_fact_card" for s in quality["storyboard"]) == 2
    serialized = json.dumps(quality).lower()
    assert all(phrase not in serialized for phrase in FORBIDDEN)
    assert PRODUCT.lower() in serialized
    return info


def main():
    output = Path(os.environ.get("TRAFFICLIFT_TEST_OUTPUT_DIR", "/tmp"))
    real = os.environ.get("TRAFFICLIFT_TEST_REAL_KOKORO") == "1"
    video = output / ("trafficlift-phase2-real-kokoro.mp4" if real else "trafficlift-phase2-smoke.mp4")
    quality = json.loads((output / "trafficlift-phase2-quality.json").read_text())
    pin = Path("/tmp/trafficlift-integrated-pin.jpg")
    frames = output / ("trafficlift-phase2-real-kokoro-frames.jpg" if real else "trafficlift-phase2-smoke-frames.jpg")
    with Image.open(pin) as image:
        assert image.size == (1000, 1500)
        assert max(ImageStat.Stat(image.convert("RGB")).stddev) > 20, "Pin appears blank"
    with Image.open(frames) as image:
        assert image.width > 500 and image.height > 500, "Missing actual frame contact sheet"
        assert max(ImageStat.Stat(image.convert("RGB")).stddev) > 20, "Frames appear blank"
    media_info = require_media(video, quality)

    # Evidence comes from our own deliberately synthetic drawing, not a seller.
    plan = build_sales_plan(
        PRODUCT, "Tall rectangular body visible in this synthetic fixture",
        "https://seller.example/product", 120,
        buyer_need="Considering the Desk Organizer",
        feature_details=["Contrasting orange lower panel visible in this synthetic fixture"],
        evidence_origin="synthetic_fixture",
    )
    assert marketing_readiness_gate(plan)["distinct_product_facts"] == 2
    assert all(word not in json.dumps(plan).lower() for word in FORBIDDEN)

    with tempfile.TemporaryDirectory(prefix="trafficlift-integrated-") as tmp:
        os.environ["TRAFFICLIFT_HOSTED_VIDEO"] = "1"
        os.environ["TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN"] = OWNER_SECRET
        os.environ["TRAFFICLIFT_VIDEO_DIR"] = tmp
        folder = Path(tmp) / JOB_ID
        folder.mkdir()
        # Reuse the already-rendered, fully decoded master. Do not invoke a worker.
        (folder / "video.mp4").write_bytes(video.read_bytes())
        (folder / "captions.ass").write_text(
            "[Events]\nDialogue: 0,0:00:01.20,0:00:03.50,Default,,0,0,0,,"
            "Desk Organizer: tall rectangular body visible in this synthetic fixture\n",
            encoding="utf-8")
        api.save({
            "id": JOB_ID, "name": PRODUCT, "status": "succeeded",
            "product_url": "https://seller.example/product",
            "video_url": f"/api/v1/photo-videos/{JOB_ID}/video",
            "sales_plan": plan, "message": "Verified local synthetic fixture",
        }, folder)
        original_worker, original_reserve = api.worker, api.reserve_hosted_render
        def forbidden_render(*_args, **_kwargs):
            raise AssertionError("Exports or session recovery started a second render")
        api.worker = forbidden_render
        api.reserve_hosted_render = forbidden_render
        try:
            app = FastAPI()
            app.include_router(api.router)
            with TestClient(app, base_url=HOST) as owner, TestClient(app, base_url=HOST) as stranger:
                url = f"/api/v1/photo-videos/{JOB_ID}"
                assert stranger.get("/api/v1/photo-videos").status_code == 401
                assert stranger.get(url).status_code == 401
                assert stranger.get(url + "/video").status_code == 401
                assert owner.post("/api/v1/video-owner-login", json={"access_code": OWNER_SECRET},
                                  headers={"Origin": HOST}).status_code == 200
                assert owner.get(url).json()["video"]["status"] == "succeeded"
                # Exercise a genuinely expired, correctly signed cookie (not just logout).
                owner.cookies.clear()
                issued = str(int(time.time()) - 8 * 60 * 60 - 60)
                signature = hmac.new(OWNER_SECRET.encode(),
                    ("video-session:" + issued).encode(), hashlib.sha256).hexdigest()
                owner.cookies.set("trafficlift_video_session", issued + "." + signature,
                                  domain="trafficlift-pro.onrender.com", path="/")
                assert owner.get(url).status_code == 401
                owner.cookies.clear()
                assert owner.post("/api/v1/video-owner-login", json={"access_code": OWNER_SECRET},
                                  headers={"Origin": HOST}).status_code == 200
                assert any(item["id"] == JOB_ID for item in owner.get("/api/v1/photo-videos").json()["videos"])
                assert owner.get(url).json()["video"]["status"] == "succeeded"
                master = owner.get(url + "/video")
                assert master.status_code == 200
                assert "private" in master.headers["cache-control"]
                assert "no-store" in master.headers["cache-control"]
                digest = hashlib.sha256(master.content).hexdigest()
                assert digest == hashlib.sha256(video.read_bytes()).hexdigest()
                kits = {}
                for platform in PLATFORMS:
                    endpoint = url + "/exports/" + platform
                    assert stranger.get(endpoint).status_code == 401
                    response = owner.get(endpoint)
                    assert response.status_code == 200, f"{platform}: {response.status_code}"
                    assert "private" in response.headers["cache-control"]
                    assert "no-store" in response.headers["cache-control"]
                    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                        assert hashlib.sha256(archive.read("video.mp4")).hexdigest() == digest
                        manifest = json.loads(archive.read("manifest.json"))
                        assert manifest["platform"] == platform
                        assert manifest["video_master_reused"] is True
                        assert manifest["video_reencoded"] is False
                        assert manifest["safe_zone_approval"] == "MANUAL_REVIEW_REQUIRED"
                        assert PRODUCT in archive.read("post.txt").decode()
                        assert PRODUCT in archive.read("captions.srt").decode()
                        assert "safe_zone_review.html" in archive.namelist()
                        # Fixture-only evidence: preserve the actual authenticated
                        # response ZIP for offline reviewer inspection.
                        kit_path = output / ("trafficlift-integrated-" + platform + ".zip")
                        kit_path.write_bytes(response.content)
                        kits[platform] = {"zip_bytes": len(response.content),
                                          "master_sha256": digest,
                                          "artifact": kit_path.name}
        finally:
            api.worker, api.reserve_hosted_render = original_worker, original_reserve

    receipt = {
        "status": "PASS", "scope": "offline in-process owner-authenticated production-like fixture",
        "production_authenticated": False, "real_listing_verified": False,
        "photo_rights": "owned synthetic fixture; no retailer image used",
        "product": PRODUCT, "pin": str(pin), "pin_dimensions": [1000, 1500],
        "master": str(video), "master_sha256": digest,
        "duration_seconds": float(media_info["format"]["duration"]),
        "video_codec": "H264", "audio_codec": "AAC", "dimensions": [1080, 1920],
        "owner_expiry_relogin_same_job": "PASS", "new_render_posts": 0,
        "platform_kits": kits, "frame_sheet": str(frames),
        "creative_review": "Automated quality gate PASS; actual JAS11242 marketing review NOT VERIFIED",
    }
    (output / "trafficlift-integrated-acceptance.json").write_text(json.dumps(receipt, indent=2))
    print("PASS: same owned fixture -> real Chromium pin -> real narrated H264/AAC master -> "
          "secure in-process relogin -> 3 matching export ZIPs; no second render")


if __name__ == "__main__":
    main()
