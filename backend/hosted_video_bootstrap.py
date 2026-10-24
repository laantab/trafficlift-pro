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
        status("ready", "Voice assets and FFmpeg tools are installed")
        return True
    except Exception as exc:
        status("failed", type(exc).__name__ + ": " + str(exc)[:250])
        return False
