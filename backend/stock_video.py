"""Narrated vertical video over stock clips or photos, with captions burned in.

Same free local voice as the campaign renderer and the same audio checks. The
picture check differs: footage moves, so the editor confirms shape, length and
that every spoken scene matches its script.
"""
import json
import math
import os
import subprocess
import textwrap


def overlay(path, name, line, cover):
    from PIL import Image, ImageDraw, ImageFont
    fontfile = os.getenv("TRAFFICLIFT_FONT") or ("C:/Windows/Fonts/arial.ttf" if os.name == "nt"
                                                 else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    canvas = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    small = ImageFont.truetype(fontfile, 40)
    box = draw.textbbox((0, 0), name, font=small)
    draw.rounded_rectangle((540 - (box[2] - box[0]) // 2 - 28, 150, 540 + (box[2] - box[0]) // 2 + 28, 226),
                           radius=38, fill=(20, 51, 43, 205))
    draw.text((540, 188), name, font=small, fill="white", anchor="mm")
    if cover:
        big = ImageFont.truetype(fontfile, 84)
        while draw.textbbox((0, 0), cover, font=big)[2] > 920:
            big = ImageFont.truetype(fontfile, big.size - 4)
        draw.text((540, 420), cover, font=big, fill="white", anchor="mm", stroke_width=7, stroke_fill=(20, 51, 43))
    font = ImageFont.truetype(fontfile, 52)
    caption = textwrap.fill(line, 30, break_long_words=True)
    while True:
        box = draw.multiline_textbbox((0, 0), caption, font=font, spacing=10)
        if box[2] - box[0] <= 880 and box[3] - box[1] <= 300:
            break
        font = ImageFont.truetype(fontfile, font.size - 2)
    draw.rounded_rectangle((54, 1370, 1026, 1720), radius=30, fill=(20, 51, 43, 225))
    draw.multiline_text((540, 1545), caption, font=font, fill="white", anchor="mm", align="center", spacing=10)
    canvas.save(path)


def speak(lines):
    import onnxruntime as ort
    from kokoro_onnx import Kokoro
    from backend.photo_video import dependencies
    model, voices = dependencies()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    engine = Kokoro.from_session(ort.InferenceSession(str(model), sess_options=options,
                                 providers=["CPUExecutionProvider"]), str(voices))
    clips, rate = [], 24000
    for line in lines:
        audio, rate = engine.create(line, voice="af_heart", speed=1.0, lang="en-us")
        clips.append(audio)
    return clips, rate


def render_stock_video(directory, product, media, lines):
    """media: four paths, each an .mp4 clip or a .jpg photo, in scene order."""
    import numpy as np
    import soundfile as sf
    from backend.photo_video import check_video, check_narration_audio
    from backend import campaign_editor as editor
    from backend.campaigns import posts
    editor.check_copy(product, lines, posts(product, lines))
    clips, rate = speak(lines)
    tracks, parts = [], []
    for i, (line, clip, source) in enumerate(zip(lines, clips, media)):
        duration = math.ceil((len(clip) / rate + (.95 if i == 3 else .38)) * 25) / 25
        track = np.zeros(round(duration * rate), dtype=np.float32)
        start = round(.09 * rate)
        track[start:start + len(clip)] = clip
        tracks.append(track)
        overlay(directory / f"overlay{i}.png", product["name"], line, product.get("cover_text") if i == 0 else "")
        still = str(source).lower().endswith((".jpg", ".jpeg", ".png"))
        feed = ["-loop", "1", "-i", str(source)] if still else ["-stream_loop", "-1", "-i", str(source)]
        subprocess.run(["ffmpeg", "-v", "error", "-y", *feed, "-i", f"overlay{i}.png", "-filter_complex",
                        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=25,setsar=1[bg];"
                        "[bg][1:v]overlay=0:0,scale=in_range=full:out_range=tv,format=yuv420p[v]",
                        "-map", "[v]", "-an", "-frames:v", str(round(duration * 25)), "-color_range", "tv",
                        "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", f"part{i}.mp4"],
                       cwd=directory, check=True, timeout=300)
        parts.append(f"file part{i}.mp4")
    (directory / "parts").write_text("\n".join(parts))
    audio = np.concatenate(tracks)
    sf.write(directory / "voice.wav", audio, rate)
    check_narration_audio(directory / "voice.wav")
    seconds = len(audio) / rate
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "parts", "-i", "voice.wav",
                    "-t", str(seconds), "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", "video.mp4"],
                   cwd=directory, check=True, timeout=300)
    check_video(directory / "video.mp4", seconds)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "video.mp4", "-ss", str(sum(len(t) for t in tracks[:-1]) / rate),
                    "-vn", "-ac", "1", "closing.wav"], cwd=directory, check=True, timeout=45)
    check_narration_audio(directory / "closing.wav")
    scenes = editor.review_speech(directory, tracks, rate)
    report = {"version": 1, "frames_checked": round(seconds * 25), "speech_scenes_checked": scenes,
              "basis": "Free stock footage, written script, locally generated speech",
              "video": editor.digest(directory / "video.mp4")}
    (directory / "editorial.tmp").write_text(json.dumps(report))
    (directory / "editorial.tmp").replace(directory / "editorial.json")
    for name in ["voice.wav", "closing.wav", "parts", *(f"part{i}.mp4" for i in range(4)),
                 *(f"overlay{i}.png" for i in range(4))]:
        (directory / name).unlink(missing_ok=True)
