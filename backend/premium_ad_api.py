"""Owner-only, no-paid-API assembly of a presenter clip and verified listing image.

Three 5-second scenes: exact-product introduction, uploaded human footage,
and exact-product CTA. Kokoro narration, centered captions, MP4 technical QA.
The external presenter clip is unverified; a HUMAN must check product identity.
"""
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from backend.photo_video_api import (
    VideoRequest, video_guard, prepare_video_plan, reserve_hosted_render,
    storage, dependencies,
)
from backend.sales_script import marketing_readiness_gate, clean
from backend.heygen_video_api import verified_listing_image
from local_video.trafficlift_video_bridge import _download_product_image

router = APIRouter()
_lock = threading.Lock()
_render_lock = threading.Lock()
_MAX_BYTES = 28 * 1024 * 1024
_ID = re.compile(r"[0-9a-f]{32}")


class PremiumStart(BaseModel):
    product: VideoRequest


def _job_folder(job_id):
    if not _ID.fullmatch(job_id):
        raise HTTPException(404, "Unknown Premium Product Ad.")
    path = storage() / "premium" / job_id
    if not (path / "job.json").is_file():
        raise HTTPException(404, "Premium Product Ad not found.")
    return path


def _load(folder):
    return json.loads((folder / "job.json").read_text(encoding="utf-8"))


def _save(folder, data):
    tmp = folder / "job.tmp"
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(folder / "job.json")


@router.post("/api/v1/premium-ad/start", status_code=201)
def start(payload: PremiumStart, request: Request):
    video_guard(request)
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "A Premium Product Ad is already being prepared.")
    try:
        plan, photos = prepare_video_plan(payload.product, require_renderable=True)
        marketing_readiness_gate(plan)
        image_url = verified_listing_image(photos)
        dependencies()
        root = storage() / "premium"
        root.mkdir(parents=True, exist_ok=True)
        if len(list(root.glob("*/job.json"))) >= 40:
            raise HTTPException(409, "Premium Ad storage is full. Archive older projects.")
        if shutil.disk_usage(root).free < 300 * 1024 * 1024:
            raise HTTPException(409, "Insufficient storage to prepare a premium ad.")
        job_id = uuid.uuid4().hex
        folder = root / job_id
        folder.mkdir(mode=0o700)
        record = {
            "id": job_id, "name": payload.product.name,
            "product_url": payload.product.product_url,
            "image_url": image_url, "plan": plan,
            "status": "awaiting_upload", "message": "Upload existing human demo MP4",
            "created": time.time(), "video_url": None,
            "paid_provider_calls": 0,
            "marketing_review": "NEEDS_VISUAL_REVIEW",
        }
        _save(folder, record)
        return {"id": job_id, "status": record["status"]}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        _lock.release()


async def _stream_mp4(request, destination):
    size = 0
    with destination.open("wb") as out:
        async for chunk in request.stream():
            size += len(chunk)
            if size > _MAX_BYTES:
                out.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(413, "Human-demo video must be 28 MB or smaller.")
            out.write(chunk)
    if size < 2048:
        destination.unlink(missing_ok=True)
        raise HTTPException(422, "Select a real MP4 video, not an empty file.")
    header = destination.open("rb").read(64)
    if b"ftyp" not in header[:32]:
        destination.unlink(missing_ok=True)
        raise HTTPException(422, "Uploaded file must be an MP4.")


@router.put("/api/v1/premium-ad/{job_id}/demo", status_code=202)
async def upload_demo(job_id: str, request: Request):
    video_guard(request)
    if request.headers.get("content-type", "").split(";")[0] != "video/mp4":
        raise HTTPException(415, "Upload an MP4 video.")
    folder = _job_folder(job_id)
    if not _render_lock.acquire(blocking=False):
        raise HTTPException(409, "Another premium assembly is running.")
    if not _lock.acquire(blocking=False):
        _render_lock.release()
        raise HTTPException(409, "Premium upload is busy.")
    try:
        data = _load(folder)
        if data["status"] != "awaiting_upload":
            raise HTTPException(409, "This job already has a clip. Do not upload again.")
        video = folder / "presenter.mp4"
        await _stream_mp4(request, video)
        data["status"] = "queued"
        data["message"] = "Preparing the narrated 15-second commercial"
        _save(folder, data)
        thread = threading.Thread(target=_worker, args=(folder,), daemon=True)
        thread.start()
        return {"id": job_id, "status": "queued", "paid_provider_calls": 0}
    except Exception:
        _render_lock.release()
        raise
    finally:
        _lock.release()


def _run(args, *, timeout=180):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("The video compositor could not complete this step. Check MP4 format, dimensions and audio setup.") from exc


def _probe(video, *, max_seconds=12):
    data = json.loads(_run(["ffprobe", "-v", "error", "-show_format",
                            "-show_streams", "-of", "json", str(video)], timeout=20).stdout)
    stream = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if not stream:
        raise ValueError("Uploaded MP4 contains no video.")
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    duration = float(data["format"].get("duration") or 0)
    if width < 240 or height < 240 or width > 4096 or height > 4096:
        raise ValueError("Presenter MP4 has unsupported dimensions.")
    if not 3 <= duration <= max_seconds:
        raise ValueError("Presenter clip must be between 3 and 12 seconds.")
    return {"duration": duration, "width": width, "height": height}


def _display_name(value):
    """Create a concise product label without seller suffixes or duplicated metadata."""
    name = re.sub(r"^\s*#{1,6}\s*", "", str(value or "")).strip()
    name = re.sub(r"\s*[-|]\s*(?:Walmart\.com|Amazon\.com|Target(?:\.com)?)\s*$",
                  "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" ,-")
    # A common marketplace listing: brand, product, color, tiers and retailer.
    if re.search(r"drawer organizer", name, flags=re.I) and (
            len(name) > 48 or re.search(r"\bdesk\s+drawer\s+organizer\b", name, flags=re.I)):
        # "Desk" is often marketplace keyword stuffing in titles that already
        # identify the exact product as an expandable multi-tier drawer
        # organizer. Keep brand, expandability and tier count; remove the
        # redundant placement keyword from the customer-facing card.
        brand = name.split(",")[0].split(" ")[0].strip()
        tiers = re.search(r"\b([2-9])\s*[- ]?\s*tier\b", name, flags=re.I)
        if tiers:
            prefix = (brand + " ") if brand and len(brand) < 18 else ""
            expanded = "Expandable " if re.search(r"\bexpandable\b", name, re.I) else ""
            return f"{prefix}{expanded}{tiers.group(1)}-Tier Drawer Organizer"
    if len(name) > 54:
        name = name[:55].rsplit(" ", 1)[0].rstrip(" ,-/")
    return clean(name, 90).strip() or "Featured product"


def _usable_feature(fact, product_name):
    """Reject listing titles, rather than turning eight arbitrary words into a sentence."""
    fact = re.sub(r"^\s*#{1,6}\s*", "", str(fact or "")).strip(" \t\r\n")
    fact = re.sub(r"\s+", " ", fact)
    if not fact or "http" in fact.lower():
        return ""
    normalized = re.sub(r"[^a-z0-9]+", " ", fact.lower()).strip()
    title = re.sub(r"[^a-z0-9]+", " ", product_name.lower()).strip()
    if normalized == title or (len(normalized.split()) >= 7 and
                               (normalized in title or title in normalized)):
        return ""
    # Marketplace titles often repeat the category as keyword stuffing.
    if len(normalized.split()) > 9 and len(re.findall(r"drawer organizer", normalized)) >= 2:
        return ""
    # A full concise clause is preferable to cutting off mid-phrase.
    clauses = re.split(r"[.!?;]", fact)
    for clause in clauses:
        clause = clause.strip(" ,-:")
        words = clause.split()
        if 2 <= len(words) <= 9 and len(clause) <= 72 and not re.search(
                r"\b(?:for|with|and|of|the|to|in|by|on|a|an)$", clause, re.I):
            return clause.rstrip(".") + "."
    # Extract only seller-confirmed attributes, in complete standalone phrases.
    features = (
        (r"\bexpandable\b", "Expandable design."),
        (r"\b(?:[2-9])\s*[- ]?\s*tier\b", "Multi-tier organization."),
        (r"\badjustable stand\b", "Adjustable stand."),
        (r"\bmetal base\b", "Metal base."),
        (r"\b(?:storage compartments|organizing compartments)\b", "Storage compartments."),
    )
    for pattern, copy in features:
        if re.search(pattern, fact, re.I):
            return copy
    return ""


def _premium_ad_copy(record, evidence):
    """Return brief complete narration, visuals, and an evidence-grounded hook."""
    name = _display_name(record["name"])
    features = []
    for item in evidence:
        phrase = _usable_feature(item, name)
        if phrase and phrase.casefold() not in {x.casefold() for x in features}:
            features.append(phrase)
    if not features:
        raise ValueError("No concise verified seller features are available. Check the listing details.")
    exact_context = " ".join(evidence).lower()
    if "drawer" in (name + " " + exact_context).lower() and (
            "organizer" in name.lower() or "organizer" in exact_context):
        hook = "ORGANIZE YOUR DRAWER"
    elif re.search(r"\badjustable\b", exact_context):
        hook = "TAKE A CLOSER LOOK"
    else:
        hook = "DISCOVER THE DETAILS"
    return {
        "name": name,
        "hook": hook,
        "narration": ["Take a closer look.", features[0], "See price and details."],
        "feature": features[0],
    }


def _fit_lines(text, font, max_width=610, max_lines=2):
    """Fit caption text by *rendered pixel width*, never by character count."""
    words = str(text).split()
    lines = []
    for word in words:
        candidate = (lines[-1] + " " + word) if lines else word
        if lines and font.getlength(candidate) > max_width:
            if len(lines) == max_lines:
                break
            lines.append(word)
        elif lines:
            lines[-1] = candidate
        else:
            lines.append(candidate)
        if font.getlength(lines[-1]) > max_width:
            # Long seller tokens/URLs must never extend beyond the safe area.
            while lines[-1] and font.getlength(lines[-1] + "…") > max_width:
                lines[-1] = lines[-1][:-1]
            lines[-1] = lines[-1].rstrip() + "…"
            break
    rendered_words = " ".join(lines).replace("…", "").split()
    if len(rendered_words) < len(words) and lines:
        last = lines[-1].rstrip("…. ")
        while last and font.getlength(last + "…") > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines[:max_lines]


def _draw_lines(draw, lines, font, center_y, *, fill="white", spacing=10):
    if not lines:
        return
    line_height = font.getbbox("Ag")[3] - font.getbbox("Ag")[1] + spacing
    for i, line in enumerate(lines):
        y = int(center_y + (i - (len(lines) - 1) / 2) * line_height)
        draw.text((360, y), line, fill=fill, anchor="mm", font=font)


def _cards(folder, image_path, record, captions):
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    product = Image.open(image_path).convert("RGB")
    font_path = os.environ.get("TRAFFICLIFT_FONT") or (
        "C:/Windows/Fonts/arial.ttf" if os.name == "nt"
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    name = _display_name(record["name"])
    scenes = (
        ("intro", record.get("hook", "TAKE A CLOSER LOOK"), name),
        ("outro", "SEE PRICE & DETAILS", "Visit the seller's listing"),
    )
    for key, headline, subtitle in scenes:
        # Use the exact seller image, contained at its natural aspect ratio.
        card = Image.new("RGB", (720, 1280), (20, 25, 36))
        thumb = ImageOps.contain(product, (650, 785))
        card.paste(thumb, ((720 - thumb.width) // 2, 140 + (785 - thumb.height) // 2))
        draw = ImageDraw.Draw(card)
        title_font = ImageFont.truetype(font_path, 39)
        sub_font = ImageFont.truetype(font_path, 30)
        draw.rounded_rectangle((28, 966, 692, 1210), radius=24, fill=(12, 19, 31))
        _draw_lines(draw, _fit_lines(headline, title_font), title_font, 1032,
                    fill=(255, 241, 210))
        _draw_lines(draw, _fit_lines(subtitle, sub_font), sub_font, 1120)
        card.save(folder / (key + ".png"))

    # Dynamic caption, wrapped to a maximum of two center-safe lines.
    mid = Image.new("RGBA", (720, 1280), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mid)
    draw.rounded_rectangle((28, 1020, 692, 1192), radius=22,
                           fill=(10, 16, 27, 224))
    font = ImageFont.truetype(font_path, 34)
    _draw_lines(draw, _fit_lines(captions[1], font), font, 1102)
    mid.save(folder / "caption.png")


def _voice(folder, captions):
    import numpy as np
    import soundfile as sf
    import onnxruntime as ort
    from kokoro_onnx import Kokoro
    model, voices = dependencies()
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    engine = Kokoro.from_session(
        ort.InferenceSession(str(model), sess_options=opts,
                             providers=["CPUExecutionProvider"]), str(voices))
    rate = 24000
    audio = np.zeros(rate * 15, dtype=np.float32)
    for ix, text in enumerate(captions):
        clip, rate = engine.create(text, voice="af_heart", speed=1.15, lang="en-us")
        if len(clip) > 4.7 * rate:
            raise ValueError("Premium ad narration exceeds its five-second scene. Shorten the seller facts.")
        start = ix * 5 * rate + int(.18 * rate)
        audio[start:start+len(clip)] = clip
    sf.write(folder / "voice.wav", audio, rate)


def _assemble(folder, record):
    from backend.photo_video import dependencies as setup
    setup()
    clip = folder / "presenter.mp4"
    info = _probe(clip)
    image_dir = folder / "source"
    image_dir.mkdir(exist_ok=True)
    image_path = _download_product_image(record["image_url"], image_dir)
    evidence = [clean(str(x.get("text", "")), 160)
                for x in record["plan"].get("evidence", []) if x.get("text")]
    if len(evidence) < 2:
        raise ValueError("Two exact-product seller facts are required.")
    copy = _premium_ad_copy(record, evidence)
    captions = copy["narration"]
    record = {**record, "name": copy["name"], "hook": copy["hook"]}
    _cards(folder, image_path, record, captions)
    _voice(folder, captions)
    filters = (
        "[0:v]scale=720:1280,setsar=1,trim=duration=5,setpts=PTS-STARTPTS[v0];"
        "[1:v]scale=660:1100:force_original_aspect_ratio=decrease,"
        "pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=0x141924,"
        "fps=24,tpad=stop_mode=clone:stop_duration=7,trim=duration=5,setpts=PTS-STARTPTS,"
        "format=rgba[demo];"
        "[3:v]format=rgba[overlay];[demo][overlay]overlay=0:0:shortest=1,setsar=1[v1];"
        "[2:v]scale=720:1280,setsar=1,trim=duration=5,setpts=PTS-STARTPTS[v2];"
        "[v0][v1][v2]concat=n=3:v=1:a=0,format=yuv420p[v]"
    )
    # Separate 3 scene segments: still, uploaded existing presenter, still.
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
          "-loop", "1", "-framerate", "24", "-t", "5", "-i", str(folder/"intro.png"),
          "-i", str(clip),
          "-loop", "1", "-framerate", "24", "-t", "5", "-i", str(folder/"outro.png"),
          "-loop", "1", "-framerate", "24", "-t", "5", "-i", str(folder/"caption.png"),
          "-i", str(folder/"voice.wav"),
          "-filter_complex", filters, "-map", "[v]", "-map", "4:a",
          "-t", "15", "-r", "24", "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac",
          "-movflags", "+faststart", str(folder/"video.mp4")], timeout=240)
    qa = _probe(folder/"video.mp4", max_seconds=16)
    if not 14.5 <= qa["duration"] <= 15.5 or qa["width"] != 720 or qa["height"] != 1280:
        raise ValueError("Completed commercial failed video duration or framing checks.")
    if (folder/"video.mp4").stat().st_size < 100000:
        raise ValueError("Completed commercial is unexpectedly small.")
    (folder/"quality.json").write_text(json.dumps({
        "video": qa, "scene_count": 3, "audio": "Kokoro",
        "human_identity": "UNVERIFIED_AI_GENERATED",
        "seller_product_identity": "NEEDS_VISUAL_REVIEW",
        "product_image": "from exact seller listing",
        "paid_provider_calls": 0,
        "publication_approved": False,
    }, indent=2))
    return qa


def _worker(folder):
    record = _load(folder)
    try:
        record.update(status="running", message="Assembling product intro, human clip, captions and narration")
        _save(folder, record)
        quality = _assemble(folder, record)
        record.update(status="succeeded", message="Draft ready; human review required before publication",
                      video_url="/api/v1/premium-ad/" + record["id"] + "/video",
                      quality=quality)
    except Exception as exc:
        record.update(status="failed", message=str(exc)[:250], video_url=None)
    finally:
        _save(folder, record)
        _render_lock.release()


@router.get("/api/v1/premium-ad/{job_id}")
def get_job(job_id: str, request: Request):
    video_guard(request)
    data = _load(_job_folder(job_id))
    return JSONResponse({"job": {
        "id": data["id"], "status": data["status"],
        "message": data["message"], "video_url": data["video_url"],
        "marketing_review": data["marketing_review"],
        "paid_provider_calls": 0,
    }}, headers={"Cache-Control": "private, no-store"})


@router.get("/api/v1/premium-ad/{job_id}/video")
def download(job_id: str, request: Request):
    video_guard(request)
    folder = _job_folder(job_id)
    data = _load(folder)
    if data["status"] != "succeeded":
        raise HTTPException(409, "Premium video is not complete.")
    video = folder / "video.mp4"
    if not video.is_file():
        raise HTTPException(404, "Video missing.")
    return FileResponse(video, media_type="video/mp4",
                        filename="trafficlift-premium-draft-" + job_id[:8] + ".mp4",
                        headers={"Cache-Control": "private, no-store"})
