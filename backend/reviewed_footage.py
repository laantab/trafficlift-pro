"""Recognize previously reviewed material. Unrecognized footage remains unapproved."""
import base64
import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageStat, ImageChops

REFERENCE = Path(__file__).with_name("editor_references.json")


def rejected_master(media_hash):
    return media_hash == json.loads(REFERENCE.read_text())["rejected_master"]


def classify_samples(samples):
    references = json.loads(REFERENCE.read_text())["wrong_pour"]
    refs = [Image.frombytes("RGB",(24,24),base64.b64decode(item)) for item in references]
    matches = []
    for second, frame in samples:
        reduced = frame.convert("RGB").resize((24,24),Image.Resampling.BOX)
        if min(sum(ImageStat.Stat(ImageChops.difference(reduced, ref)).mean)/3 for ref in refs) <= 6:
            matches.append(second)
    return matches


def decision(source, samples):
    references=json.loads(REFERENCE.read_text())
    with source.open("rb") as stream:
        known=hashlib.file_digest(stream,"sha256").hexdigest()==references["approved_creative"]
    if known:
        return {"decision":"owner_accepted_reference","reason":"Exact reviewed Mom’s laugh creative master."}
    hits=classify_samples(samples)
    if len(hits) >= 2:
        return {"decision":"reject","reason":"Matches the reviewed ThermoFlask two-cup pouring shot.",
                "first_seen":min(hits),"last_seen":max(hits)}
    return {"decision":"needs_review","reason":"Footage has no confirmed product-action review."}


def replace_ending(source, destination, cutoff, duration):
    """Keep the original audio and replace only the rejected visual tail."""
    if not 2 < cutoff < duration:
        raise ValueError("No usable footage before the rejected ending.")
    command=["ffmpeg","-v","error","-y","-i",str(source),"-map","0:v:0","-map","0:a:0",
             "-vf",f"trim=end={cutoff:.4f},tpad=stop_mode=clone:stop_duration={duration-cutoff+1:.4f}",
             "-t",f"{duration:.4f}","-c:v","libx264","-threads","2","-pix_fmt","yuv420p","-c:a","copy",
             "-movflags","+faststart",str(destination)]
    subprocess.run(command,check=True,timeout=180)
