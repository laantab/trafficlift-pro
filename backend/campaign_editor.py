"""Release checks for the campaign compositor's still-image scenes and local speech."""
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit



class EditorialHold(ValueError):
    """A campaign failed a release check."""


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_copy(product, lines, drafts):
    from backend.video_editor_in_chief import _PLACEHOLDER, _RISKY_CLAIMS
    link = str(product["destination"])
    url = urlsplit(link)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise EditorialHold("Save a valid HTTPS purchase link.")
    if len(lines) != 4 or any(not line.strip() for line in lines):
        raise EditorialHold("The campaign needs four complete scenes.")
    if _PLACEHOLDER.search(" ".join(lines)) or _RISKY_CLAIMS.search(" ".join(lines)):
        raise EditorialHold("A claim or placeholder needs editorial review.")
    if product["name"].casefold() not in lines[-1].casefold():
        raise EditorialHold("The closing line must identify the advertised product.")
    if "on amazon" in lines[-1].casefold() and url.hostname not in {"amazon.com","www.amazon.com","amzn.to"}:
        raise EditorialHold("The spoken store and purchase link disagree.")
    if any(copy.splitlines()[-1].strip() != link for copy in drafts.values()):
        raise EditorialHold("The posting destination differs from the saved product.")
    if product["affiliate"] and any("commission" not in text.lower() for text in [*drafts.values()," ".join(lines)]):
        raise EditorialHold("The affiliate disclosure is missing.")


def review_frames(directory, images, frame_counts):
    """Check every encoded frame against the intended layout and original artwork."""
    import numpy as np
    from PIL import Image, ImageOps
    size = (90,160)
    refs = []
    for i, path in enumerate(images):
        with Image.open(directory/f"scene{i}.jpg") as scene, Image.open(path) as source:
            art = ImageOps.contain(source.convert("RGB"), (930,1120))
            left, top = (1080-art.width)//2, 805-art.height//2
            inserted = scene.crop((left,top,left+art.width,top+art.height))
            def pixels(im):
                return np.asarray(im.convert("RGB").resize(size),dtype=np.int16)
            if np.mean(np.abs(pixels(inserted)-pixels(art))) > 4:
                raise EditorialHold(f"Scene {i+1} does not show its intended source image.")
            refs.append(pixels(scene))
    boundaries = np.cumsum(frame_counts)
    cmd = ["ffmpeg","-v","error","-i",str(directory/"video.mp4"),"-an","-vf","scale=90:160",
           "-threads","1","-pix_fmt","rgb24","-f","rawvideo","pipe:1"]
    result = subprocess.run(cmd,capture_output=True,check=True,timeout=120)
    frames = np.frombuffer(result.stdout,dtype=np.uint8).reshape((-1,160,90,3))
    if len(frames) != boundaries[-1]:
        raise EditorialHold("The final scene timing differs from the approved storyboard.")
    for i,frame in enumerate(frames):
        scene = int(np.searchsorted(boundaries,i,side="right"))
        error = np.abs(frame.astype(np.int16)-refs[scene])
        if error.mean() > 6 or (error.mean(axis=2)>30).mean() > .07:
            raise EditorialHold(f"Unexpected visual at {i/25:.2f}s in scene {scene+1}.")
    return len(frames)


def review_speech(directory, tracks, rate):
    """Compare the decoded speech to the actual planned utterance in every scene."""
    import numpy as np
    cmd=["ffmpeg","-v","error","-i",str(directory/"video.mp4"),"-vn","-ac","1","-ar",str(rate),
         "-f","f32le","pipe:1"]
    data=subprocess.run(cmd,capture_output=True,check=True,timeout=90)
    samples=np.frombuffer(data.stdout,dtype="<f4")
    offset=0
    for i,reference in enumerate(tracks):
        part=samples[offset:offset+len(reference)]
        offset+=len(reference)
        if len(part)!=len(reference):
            raise EditorialHold("The exported narration ended early.")
        # Inspect short voiced windows so a missing final word cannot hide behind
        # the louder first part of an otherwise matching sentence.
        for start in range(0,len(part),rate//8):
            ref=reference[start:start+rate//8]
            decoded=part[start:start+len(ref)]
            power=float(np.linalg.norm(ref))
            if power<.015*np.sqrt(len(ref)):
                continue
            correlation=float(np.dot(ref,decoded))/(power*np.linalg.norm(decoded)+1e-12)
            if correlation < .88:
                raise EditorialHold(f"Speech differs from the recorded script in scene {i+1}.")
    return len(tracks)


def receipt(directory, frames, scenes):
    report={"version":1,"frames_checked":frames,"speech_scenes_checked":scenes,
            "basis":"Saved product artwork and text, rendered still-image scenes, locally generated speech",
            "video":digest(directory/"video.mp4"),"kit":digest(directory/"campaign.zip")}
    path=directory/"editorial.json"
    path.with_suffix(".tmp").write_text(json.dumps(report))
    path.with_suffix(".tmp").replace(path)


def verified(directory, asset):
    try:
        report=json.loads((directory/"editorial.json").read_text())
        return report["version"]==1 and report[asset]==digest(directory/("video.mp4" if asset=="video" else "campaign.zip"))
    except (KeyError, ValueError, OSError):
        return False
