"""Opt-in preparation of hosted video assets on an attached persistent disk.

Does not authorize customer video requests. Provision in a background thread so
the site remains responsive while assets download. Every file is verified by
scripts.install_hosted_voice_assets before marked ready.
"""
import json
import os
import shutil
import subprocess
import traceback
from pathlib import Path
from datetime import datetime, timezone


def provision():
    if os.getenv("TRAFFICLIFT_HOSTED_VIDEO_PREPARE") != "1":
        return False
    home = Path(os.getenv("TRAFFICLIFT_HOSTED_MODEL_DIR", "/var/data/models")).resolve()
    status_file = home / "provision-status.json"
    home.mkdir(parents=True, exist_ok=True)

    def status(state, detail):
        temporary = status_file.with_suffix(".partial")
        temporary.write_text(json.dumps({"state": state, "detail": detail,
             "checked_at": datetime.now(timezone.utc).isoformat()}))
        temporary.replace(status_file)
        import logging
        logging.getLogger("trafficlift.hosted_video").info("Hosted video preparation: %s", state)

    try:
        status("preparing", "Installing checksummed voice assets")
        from scripts.install_hosted_voice_assets import install
        install(home)
        os.environ["TRAFFICLIFT_VOICE_MODEL"] = str(home / "kokoro-v1.0.int8.onnx")
        os.environ["TRAFFICLIFT_VOICES"] = str(home / "voices-v1.0.bin")
        os.environ.setdefault("TRAFFICLIFT_VIDEO_DIR", "/var/data/trafficlift-videos")
        status("preparing", "Checking FFmpeg executables")
        import static_ffmpeg
        static_ffmpeg.add_paths(weak=True)
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("FFmpeg/FFprobe executables were not found")
        for name in ("ffmpeg", "ffprobe"):
            subprocess.run([name, "-version"], check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=30)
        if os.getenv("TRAFFICLIFT_HOSTED_VIDEO_SMOKE") == "1":
            # A real narrated MP4, not a mocked HTTP fixture. Save the sample
            # on the existing disk for authenticated owner download and review.
            status("preparing", "Rendering local server acceptance video")
            import sys
            import tempfile
            acceptance_dir = home / "owner-acceptance"
            receipt_path = acceptance_dir / "receipt.json"
            receipt_path.unlink(missing_ok=True)  # never reuse a stale pass
            with tempfile.TemporaryDirectory(prefix="hosted-smoke-", dir=home) as smoke_tmp:
                env = dict(os.environ)
                env["TRAFFICLIFT_TEST_REAL_KOKORO"] = "1"
                env["TRAFFICLIFT_TEST_OUTPUT_DIR"] = smoke_tmp
                result = subprocess.run(
                    [sys.executable, "-m", "backend.tests.video_phase2_mp4_smoke"],
                    env=env, capture_output=True, text=True, timeout=900,
                )
                if result.returncode:
                    raise RuntimeError("Hosted video smoke failed: " + (result.stderr or result.stdout)[-250:])
                sample = Path(smoke_tmp) / "trafficlift-phase2-real-kokoro.mp4"
                quality_file = Path(smoke_tmp) / "trafficlift-phase2-quality.json"
                if not sample.is_file() or sample.stat().st_size <= 10000 or not quality_file.is_file():
                    raise RuntimeError("Hosted video smoke produced no verified MP4 and QA report")
                quality = json.loads(quality_file.read_text())
                if (quality.get("status") != "PASS"
                        or quality.get("motion", {}).get("status") != "PASS"
                        or quality.get("narration_audio", {}).get("status") != "PASS"):
                    raise RuntimeError("Hosted video smoke quality checks did not pass")
                acceptance_dir.mkdir(parents=True, exist_ok=True)
                for source, name in ((sample, "real-kokoro.mp4"), (quality_file, "quality.json")):
                    destination = acceptance_dir / name
                    partial = acceptance_dir / (name + ".partial")
                    shutil.copyfile(source, partial)
                    partial.replace(destination)
                receipt = {
                    "state": "passed",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "video_size_bytes": sample.stat().st_size,
                    "sample_only": True,
                }
                partial_receipt = acceptance_dir / "receipt.partial"
                partial_receipt.write_text(json.dumps(receipt))
                partial_receipt.replace(receipt_path)
            status("ready", "Real Kokoro server MP4 acceptance passed")
        else:
            status("ready", "Voice assets and FFmpeg tools are installed")
        return True
    except Exception as exc:
        status("failed", type(exc).__name__ + ": " + str(exc)[:250])
        return False
