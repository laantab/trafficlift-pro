"""Owner-only video production editor: inspect actual HeyGen footage and edit shots.

No paid generation. Provider video is inspected once per request and never
modified. Visual scene samples and technical flags are not a semantic model:
a human must still confirm the exact vessel/product and narration.
"""
import base64
import io
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from PIL import Image, ImageChops, ImageStat

from backend.photo_video_api import video_guard
from backend.heygen_video_api import BASE, configured, provider_request
from backend.social_exports_api import _download, _ffmpeg, _video_duration

router = APIRouter()
_ID = re.compile(r"^[A-Za-z0-9_-]{6,128}$")


def _source_from_heygen(video_id, directory):
    if not _ID.fullmatch(video_id):
        raise HTTPException(404, "Unknown video.")
    if not configured():
        raise HTTPException(503, "HeyGen video access is unavailable.")
    job = provider_request("GET", BASE + "/" + video_id)
    state = job.get("status") or (job.get("data") or {}).get("status")
    url = job.get("video_url") or (job.get("data") or {}).get("video_url")
    if state != "completed" or not isinstance(url, str):
        raise HTTPException(409, "The completed video is not available for editing.")
    source = directory / "original.mp4"
    _download(url, source)
    return source


def _metadata(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        try:
            import static_ffmpeg
            static_ffmpeg.add_paths()
            ffprobe = shutil.which("ffprobe")
        except Exception:
            pass
    if not ffprobe:
        raise HTTPException(503, "Editor requires FFprobe for media inspection.")
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "stream=codec_type,width,height,codec_name:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=18,
        )
        data = json.loads(proc.stdout)
        streams = data.get("streams") or []
        video = next(x for x in streams if x.get("codec_type") == "video")
        duration = float(data.get("format", {}).get("duration", 0))
        if not (2 <= duration <= 46) or not video.get("width") or not video.get("height"):
            raise ValueError("Unsupported media dimensions or duration")
        return {
            "duration": round(duration, 2), "width": video["width"],
            "height": video["height"],
            "has_audio": any(x.get("codec_type") == "audio" for x in streams),
        }
    except (StopIteration, KeyError, ValueError, OSError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as exc:
        raise HTTPException(422, "The original video is not a supported reviewable file.") from exc


def _sample_frame(source, second):
    result = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin",
         "-ss", f"{second:.2f}", "-i", str(source),
         "-frames:v", "1", "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
         "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
        capture_output=True, timeout=20, check=True,
    )
    if len(result.stdout) < 200:
        raise ValueError("No visible frame at this timestamp")
    image = Image.open(io.BytesIO(result.stdout)).convert("RGB")
    # Small enough for safe owner-only JSON review, no public media URL.
    with io.BytesIO() as out:
        image.save(out, "JPEG", quality=70, optimize=True)
        return image, base64.b64encode(out.getvalue()).decode("ascii")


def inspect_footage(source):
    """Sample *real frames*, detect technical anomalies, and list scene times.

    This is not a classifier for different cups, handles, logos or actions.
    Unknown semantic defects always require owner inspection.
    """
    meta = _metadata(source)
    duration = meta["duration"]
    count = min(12, max(5, math.ceil(duration / 1.5)))
    timestamps = [round((i + .5) * duration / count, 2) for i in range(count)]
    frames, warnings = [], []
    prev = None
    for second in timestamps:
        try:
            image, jpeg = _sample_frame(source, second)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(422, "Editor could not inspect a frame of the actual footage.") from exc
        gray = image.convert("L").resize((80, 80))
        lightness = ImageStat.Stat(gray).mean[0]
        if lightness < 10:
            warnings.append({"time": second, "issue": "Very dark frame", "kind": "technical"})
        if prev is not None:
            diff = ImageStat.Stat(ImageChops.difference(prev, gray)).mean[0]
            if diff < .18:
                warnings.append({"time": second, "issue": "Little or no visual motion between samples; inspect for a frozen shot", "kind": "technical"})
            if diff > 58:
                warnings.append({"time": second, "issue": "Abrupt scene transition; inspect for a substituted product", "kind": "review"})
        prev = gray
        frames.append({"second": second, "jpeg_base64": jpeg})
    if not meta["has_audio"]:
        warnings.append({"time": 0, "issue": "No audio track; narrated advertisement is not ready", "kind": "technical"})
    return {
        **meta, "frames": frames, "flags": warnings,
        "approval": "NOT APPROVED",
        "scene_identity_analysis": "Manual comparison required; product and action identity are not automatically certified.",
        "instructions": "Compare each scene to the original product photo. If a different cup, lid or product appears, reject that time range. Edits may affect narration and require a complete re-review.",
        "provider_credits_used": False,
    }


def cut_unusable_scene(source, destination, start, end):
    """Create an actual corrected video, cutting bad shot AND synchronized audio.

    Never fabricates replacement imagery or redubs claims. Refuse a cut that
    leaves too little usable content or consumes most of the advertisement.
    """
    info = _metadata(source)
    duration = info["duration"]
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Use numeric start and end seconds for the rejected shot.") from exc
    if (not math.isfinite(start) or not math.isfinite(end) or
            start < 0 or end > duration + .05 or end - start < .35):
        raise HTTPException(422, "Rejected scene time range is invalid.")
    end = min(end, duration)
    if duration - (end - start) < 5 or (end - start) > duration * .45:
        raise HTTPException(422, "Too much footage would be lost. This video needs a replacement shot instead of an unsafe automatic edit.")
    if not info["has_audio"]:
        raise HTTPException(422, "Source narration is missing. Editing cannot manufacture a truthful voiceover.")
    keep = [(a, b) for a, b in ((0, start), (end, duration)) if b - a > .09]
    filters, vs, audios = [], [], []
    for i, (a, b) in enumerate(keep):
        filters += [
            f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]",
            f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]",
        ]
        vs.append(f"[v{i}]")
        audios.append(f"[a{i}]")
    filters.append("".join(s for pair in zip(vs, audios) for s in pair) +
                   f"concat=n={len(keep)}:v=1:a=1[vout][aout]")
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-i", str(source), "-filter_complex", ";".join(filters),
           "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
           str(destination)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=110)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise HTTPException(502, "Editor could not safely cut this scene. Original video is preserved.") from exc
    if not destination.exists() or destination.stat().st_size < 2048:
        raise HTTPException(502, "Editor produced an incomplete draft. Original video is preserved.")
    output = _metadata(destination)
    if not output["has_audio"] or output["duration"] >= duration - .1:
        raise HTTPException(502, "Edited video did not pass duration/audio checks. Original video is preserved.")
    return {"before": duration, "after": output["duration"], "removed": [start, end]}


@router.get("/api/v1/video-editor-in-chief/heygen/{video_id}/review")
def review_heygen(video_id: str, request: Request):
    video_guard(request)
    work = Path(tempfile.mkdtemp(prefix="trafficlift-scene-review-"))
    try:
        source = _source_from_heygen(video_id, work)
        return inspect_footage(source)
    finally:
        shutil.rmtree(work, ignore_errors=True)


class CutRequest(BaseModel):
    remove_start: float = Field(ge=0, le=45)
    remove_end: float = Field(ge=0, le=46)


@router.post("/api/v1/video-editor-in-chief/heygen/{video_id}/preview-cut")
def preview_cut(video_id: str, body: CutRequest, request: Request):
    """Return an actual edited MP4 for mandatory re-review, no paid generation."""
    video_guard(request)
    work = Path(tempfile.mkdtemp(prefix="trafficlift-edited-preview-"))
    try:
        original = _source_from_heygen(video_id, work)
        candidate = work / "editor-draft.mp4"
        cut_unusable_scene(original, candidate, body.remove_start, body.remove_end)
        return FileResponse(
            candidate, media_type="video/mp4", filename="TrafficLift-Editor-Draft.mp4",
            headers={"Cache-Control": "private, no-store"},
            background=BackgroundTask(shutil.rmtree, work, ignore_errors=True),
        )
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
