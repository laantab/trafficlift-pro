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


def _probe(video):
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
    if not 3 <= duration <= 12:
        raise ValueError("Presenter clip must be between 3 and 12 seconds.")
    return {"duration": duration, "width": width, "height": height}


def _cards(folder, image_path, record, captions):
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    product = Image.open(image_path).convert("RGB")
    font_path = os.environ.get("TRAFFICLIFT_FONT") or (
        "C:/Windows/Fonts/arial.ttf" if os.name == "nt"
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    for key, headline, sub in (
        ("intro", "TAKE A CLOSER LOOK", record["name"]),
        ("outro", "SEE PRICE & DETAILS", "View the seller listing"),
    ):
        card = Image.new("RGB", (720, 1280), (20, 25, 36))
        # Contain rather than crop the product; keep the full silhouette.
        thumb = ImageOps.contain(product, (650, 875))
        card.paste(thumb, ((720-thumb.width)//2, (1280-thumb.height)//2-30))
        draw = ImageDraw.Draw(card)
        f1 = ImageFont.truetype(font_path, 40)
        f2 = ImageFont.truetype(font_path, 27)
        draw.rectangle((0, 990, 720, 1280), fill=(20, 25, 36))
        draw.text((360, 1052), headline, fill=(255, 241, 210),
                  anchor="mm", font=f1, stroke_width=1)
        sub = clean(sub, 90)
        if f2.getlength(sub) > 665:
            sub = sub[:48].rstrip() + "..."
        draw.text((360, 1123), sub, fill="white", anchor="mm", font=f2)
        card.save(folder / (key + ".png"))
    mid = Image.new("RGBA", (720, 1280), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mid)
    draw.rectangle((20, 1075, 700, 1195), fill=(10, 16, 27, 210))
    font = ImageFont.truetype(font_path, 32)
    text = captions[1]
    if font.getlength(text) > 630:
        text = text[:40].rstrip() + "..."
    draw.text((360, 1130), text, font=font, anchor="mm", fill="white")
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
    # Narrate only sourced short facts. No claim that AI footage proves function.
    def short_fact(f):
        words = f.split()
        return " ".join(words[:8]).rstrip(".!?") + "."
    captions = ["Take a closer look.", short_fact(evidence[0]),
                "See price and details."]
    _cards(folder, image_path, record, captions)
    _voice(folder, captions)
    filters = (
        "[0:v]scale=720:1280,setsar=1,trim=duration=5,setpts=PTS-STARTPTS[v0];"
        "[1:v]scale=660:1100:force_original_aspect_ratio=decrease,"
        "pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=0x141924,"
        "fps=24,tpad=stop_mode=clone:stop_duration=7,trim=duration=5,setpts=PTS-STARTPTS,"
        "format=rgba[demo];"
        "[3:v]format=rgba[overlay];[demo][overlay]overlay=0:0:shortest=1[v1];"
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
    qa = _probe(folder/"video.mp4")
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
