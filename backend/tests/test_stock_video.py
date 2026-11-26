"""The stock-footage renderer on real FFmpeg, with a stand-in voice. No network."""
import json
import shutil
import subprocess

import numpy as np
import pytest

from backend import growth_plan as growth
from backend import stock_video
from backend.campaign_editor import verified

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg not installed")


def tone(lines):
    rate, clips = 24000, []
    for i, line in enumerate(lines):
        t = np.arange(int(rate * (1.2 + .05 * len(line.split())))) / rate
        clips.append((.3 * np.sin(2 * np.pi * (180 + 40 * i) * t) * (.6 + .4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32))
    return clips, rate


def test_clips_and_photos_become_one_checked_vertical_video(tmp_path, monkeypatch):
    monkeypatch.setattr(stock_video, "speak", tone)
    wide, tall, photo = tmp_path / "wide.mp4", tmp_path / "tall.mp4", tmp_path / "photo.jpg"
    for source, target, extra in (("testsrc2=size=1280x720:rate=30", wide, ["-t", "2"]),
                                  ("testsrc=size=720x1280:rate=24", tall, ["-t", "1"]),
                                  ("color=c=teal:size=1600x1200", photo, ["-frames:v", "1"])):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, *extra, "-pix_fmt",
                        "yuvj420p" if target is photo else "yuv420p", str(target)], check=True)
    item = growth.stock_entry(1)
    lines = [scene["say"] for scene in item["scenes"]]
    product = {"name": growth.ACCOUNT, "destination": growth.PROFILE, "feature": item["topic"], "prompt": "",
               "affiliate": False, "cover_text": item["cover_text"]}
    out = tmp_path / "job"
    out.mkdir()
    stock_video.render_stock_video(out, product, [wide, tall, photo, wide], lines)   # short clips loop to fit
    assert sorted(p.name for p in out.iterdir()) == ["editorial.json", "video.mp4"]
    assert verified(out, "video") and json.loads((out / "editorial.json").read_text())["speech_scenes_checked"] == 4
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out / "video.mp4")]))
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["pix_fmt"]) == (1080, 1920, "yuv420p")
