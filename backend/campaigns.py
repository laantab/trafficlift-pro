"""Book and product campaigns using local narration and owner-supplied artwork."""
import json
import math
import os
import subprocess
import textwrap
import zipfile
from pathlib import Path

ASSETS = Path(__file__).with_name("campaign_assets")
HOLIDAY = {
    "name": "A Season for Everyone", "format": "paperback",
    "destination": "https://www.amazon.com/dp/B0HPQFZJQD",
    "feature": "Christmas coloring scenes paired with personal reflection prompts",
    "prompt": "Something that makes me smile is…", "affiliate": False,
}
HOLIDAY_HOOKS = [
    ("Who makes you smile before they even say hello?", "Write their name here."),
    ("What would you write after these words?", "Something that makes me smile is…"),
    ("Which Christmas memory would you put on paper?", "Start with someone who makes you smile."),
]
PRODUCT_HOOKS = [
    "What do you actually get with {name}?",
    "Curious about {name}? Take a closer look.",
    "Would {name} suit you? Start with this detail.",
]


def plan(product, index, holiday=False):
    name = product["name"]
    if holiday:
        hook, answer = HOLIDAY_HOOKS[index]
        middle = "Color a Christmas scene, then turn to the facing page and make the memory yours."
        ending = "Give someone a place for their memories. Find A Season for Everyone on Amazon."
    else:
        hook = PRODUCT_HOOKS[index].format(name=name)
        answer = product["feature"]
        middle = product.get("prompt") or "Here's a closer look."
        ending = f"Take a look at {name} through the product link."
    lines = [hook, answer, middle, ending]
    if product["affiliate"]:
        lines[1] = "I earn a commission through my link. " + lines[1]
    return lines


def shorts_package(product, lines):
    """Pair a product-led title with a short visual cue and the actual spoken hook."""
    name = product["name"].strip()
    holiday = product == HOLIDAY
    if holiday:
        index = next((i for i, (hook, _) in enumerate(HOLIDAY_HOOKS) if hook == lines[0]), 0)
        angle = ("Who makes you smile?", "Inside the Christmas journal", "A Christmas memory")[index]
        cover = ("That one person", "Finish this thought", "Remember when?")[index]
    else:
        index = next((i for i, hook in enumerate(PRODUCT_HOOKS)
                      if hook.format(name=product["name"]) == lines[0]), 0)
        angle = ("A closer look", "One detail to see", "Is it for you?")[index]
        cover = ("What's inside?", "Look closer", "See for yourself")[index]
    title = f"{name} | {angle}"
    if len(title) > 60:
        title = name
    disclosure = "I earn a commission through this link. " if product["affiliate"] else ""
    description = f"{disclosure}{name} — {product['feature'].rstrip('.')}.\n{lines[-1]}\n{product['destination']}"
    return {"title": title, "cover_text": cover, "hook": lines[0],
            "narration": lines, "description": description}


def posts(product, lines):
    name, link = product["name"], product["destination"]
    hook, detail = lines[0], product["feature"].rstrip(".")
    disclosure = "I earn a commission through this link. " if product["affiliate"] else ""
    short = shorts_package(product, lines)
    return {
        "youtube-shorts": short["title"] + "\n\n" + short["description"],
        "instagram-reels": f"{hook}\n\n{disclosure}A closer look inside {name}. {detail.capitalize()}.\n{link}",
        "tiktok": f"{disclosure}{hook} {name} • {detail}.\n{link}",
        "facebook-reels": f"{disclosure}{hook}\n\nTake a closer look at {name} in this reel. {lines[-1]}\n{link}",
        "linkedin": f"{disclosure}One detail worth a closer look in {name} is {detail}. This short demonstration shows it in context.\n{link}",
    }


def render_campaign(directory, product, images, lines):
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf
    from kokoro_onnx import Kokoro
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    from backend.photo_video import dependencies, check_video, check_narration_audio
    from backend import campaign_editor as editor
    short = shorts_package(product, lines)
    drafts = posts(product, lines)
    editor.check_copy(product, lines, drafts)
    model, voices = dependencies()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    engine = Kokoro.from_session(ort.InferenceSession(str(model), sess_options=options,
                                 providers=["CPUExecutionProvider"]), str(voices))
    clips = []
    for line in lines:
        audio, rate = engine.create(line, voice="af_heart", speed=1.0, lang="en-us")
        clips.append(audio)
    del engine
    fontfile = os.getenv("TRAFFICLIFT_FONT") or ("C:/Windows/Fonts/arial.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    timeline, tracks = [], []
    for i, (line, clip, img) in enumerate(zip(lines, clips, images)):
        duration = math.ceil((len(clip)/rate + (.95 if i == 3 else .38))*25)/25
        track = np.zeros(round(duration*rate), dtype=np.float32)
        start = round(.09*rate)
        track[start:start+len(clip)] = clip
        tracks.append(track)
        with Image.open(img) as source:
            art = ImageOps.contain(source.convert("RGB"), (930, 1120))
        canvas = Image.new("RGB", (1080, 1920), "#f8f1e7")
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.truetype(fontfile, 48)
        header = textwrap.fill(product["name"], 30)
        draw.multiline_text((520, 188), header, font=font, fill="#24433b", anchor="mm", align="center")
        canvas.paste(art, ((1080-art.width)//2, 805-art.height//2))
        caption = textwrap.fill(line, 34, break_long_words=True)
        while True:
            box = draw.multiline_textbbox((0,0), caption, font=font, spacing=9)
            if box[2]-box[0] <= 875 and box[3]-box[1] <= 290:
                break
            font = ImageFont.truetype(fontfile, font.size-2)
        draw.rounded_rectangle((54,1400,984,1730), radius=28, fill="#24433b")
        draw.multiline_text((519,1565), caption, font=font, fill="white", anchor="mm", align="center", spacing=9)
        if i == 0:
            draw.text((519, 1310), short["cover_text"], font=font, fill="#24433b", anchor="mm")
        canvas.save(directory/f"scene{i}.jpg", quality=89)
        timeline.extend([f"file scene{i}.jpg", f"duration {duration}"])
    timeline.append("file scene3.jpg")
    (directory/"frames").write_text("\n".join(timeline))
    audio = np.concatenate(tracks)
    sf.write(directory/"voice.wav", audio, rate)
    check_narration_audio(directory/"voice.wav")
    seconds = len(audio)/rate
    def encode():
        subprocess.run(["ffmpeg","-v","error","-y","-f","concat","-safe","0","-i","frames","-i","voice.wav",
                        "-vf","fps=25,scale=in_range=full:out_range=tv,format=yuv420p","-t",str(seconds),"-color_range","tv","-pix_fmt","yuv420p","-c:v","libx264","-threads","2","-preset","veryfast",
                        "-c:a","aac","-movflags","+faststart","video.mp4"], cwd=directory, check=True, timeout=300)
    def inspect():
        check_video(directory/"video.mp4", seconds)
        # Check decoded audio after muxing, including the closing section.
        subprocess.run(["ffmpeg","-v","error","-y","-i","video.mp4","-ss",str(sum(len(t) for t in tracks[:-1])/rate),
                        "-vn","-ac","1","closing.wav"],cwd=directory,check=True,timeout=45)
        check_narration_audio(directory/"closing.wav")
        frames = editor.review_frames(directory, images, [round(len(t)*25/rate) for t in tracks])
        scenes = editor.review_speech(directory, tracks, rate)
        return frames, scenes
    encode()
    try:
        frames, scenes = inspect()
    except (editor.EditorialHold, ValueError):
        # Rebuild the candidate once from the original art and speech, then run
        # every check again. No regeneration, new claims or provider charges.
        encode()
        frames, scenes = inspect()
    with zipfile.ZipFile(directory/"campaign.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(directory/"video.mp4", "video.mp4")
        archive.write(directory/"scene0.jpg", "cover-frame.jpg")
        archive.writestr("youtube-shorts-plan.json", json.dumps(short, ensure_ascii=False, indent=2))
        for platform, copy in drafts.items():
            archive.writestr(f"{platform}.txt", copy)
    (directory/"shorts.json").write_text(json.dumps(short), encoding="utf-8")
    editor.receipt(directory, frames, scenes)
    for name in ["voice.wav","closing.wav","frames",*(f"scene{i}.jpg" for i in range(4))]:
        (directory/name).unlink()
