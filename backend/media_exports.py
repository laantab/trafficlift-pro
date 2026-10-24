"""Owner-protected platform kits: copy the verified master MP4; never render again.

Each ZIP contains the same H.264/AAC master, a posting draft, safe-zone review
notes, and timed SRT captions when the renderer's ASS file is available.
Platform UI overlays vary: these kits do not certify publication or safe zones.
"""
import json
import re
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path

PLATFORMS = {
    "tiktok": {
        "label": "TikTok",
        "hashtags": "#ProductFinds #ShoppingTips",
        "safe_zone": "Keep critical words and the product away from right-side controls and the lower caption/CTA area. TikTok's safe zone varies with caption length and UI; preview in TikTok before posting.",
        "source": "https://ads.tiktok.com/resources/help/article/tiktok-auction-in-feed-ads",
    },
    "youtube-shorts": {
        "label": "YouTube Shorts",
        "hashtags": "#Shorts #ProductFinds",
        "safe_zone": "Keep key text away from the lower title/controls and the right-side action buttons. Review in the Shorts viewer on a phone before posting.",
        "source": "https://support.google.com/youtube/answer/15424877",
    },
    "instagram-reels": {
        "label": "Instagram Reels",
        "hashtags": "#Reels #ProductFinds",
        "safe_zone": "Keep text, faces and the product in the central area, clear of the lower caption and right-side controls. Meta provides a Reels safe-zone checker; verify in the Instagram preview.",
        "source": "https://www.facebook.com/business/ads/facebook-instagram-reels-ads",
    },
}
# Only one temporary archive is built at a time on the existing instance.
EXPORT_LOCK = threading.BoundedSemaphore(1)
MAX_MASTER_BYTES = 300 * 1024 * 1024


def _srt_time(ass_time):
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})\.(\d{2})", ass_time.strip())
    if not match:
        raise ValueError("Invalid caption timing")
    hours, minutes, seconds, centiseconds = map(int, match.groups())
    return f"{hours:02}:{minutes:02}:{seconds:02},{centiseconds * 10:03}"


def captions_to_srt(path):
    """Translate existing, approximate ASS dialogue timing; no second subtitle render."""
    if not path.is_file():
        return ""
    cues = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue: "):
            continue
        fields = line[len("Dialogue: "):].split(",", 9)
        if len(fields) != 10:
            continue
        try:
            start, end = _srt_time(fields[1]), _srt_time(fields[2])
        except ValueError:
            continue
        caption = re.sub(r"\{[^}]*\}", "", fields[9]).replace(r"\N", "\n").replace(r"\h", " ").strip()
        if caption:
            cues.append(f"{len(cues) + 1}\n{start} --> {end}\n{caption}\n")
    return "\n".join(cues)


def make_export_bundle(record, directory, platform):
    """Return a temporary ZIP path. Caller must remove it after FileResponse."""
    if platform not in PLATFORMS:
        raise ValueError("Unsupported platform")
    directory = Path(directory)
    master = directory / "video.mp4"
    if not master.is_file() or not master.stat().st_size:
        raise FileNotFoundError("The finished master MP4 is missing.")
    size = master.stat().st_size
    if size > MAX_MASTER_BYTES:
        raise ValueError("This video is too large for a lightweight export kit.")
    if shutil.disk_usage(tempfile.gettempdir()).free < size + 32 * 1024 * 1024:
        raise OSError("Not enough temporary disk space for a platform kit.")
    config = PLATFORMS[platform]
    name = " ".join(str(record.get("name") or "Product").split())[:90]
    title = (name + " | Product overview")[:100] if platform == "youtube-shorts" else name
    description = (
        f"{name} — a closer look. Check the seller listing for exact features, "
        f"availability and current pricing.\n{config['hashtags']}"
    )
    source_url = record.get("product_url") or ""
    manifest = {
        "platform": platform,
        "platform_name": config["label"],
        "job_id": record["id"],
        "title": title,
        "description": description,
        "seller_url": source_url,
        "video": "video.mp4",
        "video_master_reused": True,
        "video_reencoded": False,
        "expected_master": "1080x1920 vertical H.264 video with AAC audio",
        "captions": "Already burned into the master MP4; captions.srt is an optional transcript when available.",
        "safe_zone": config["safe_zone"],
        "safe_zone_approval": "MANUAL_REVIEW_REQUIRED",
        "marketing_approval": "MANUAL_REVIEW_REQUIRED",
        "specification_reference": config["source"],
    }
    notes = (
        f"{config['label']} posting kit — review before publishing\n\n"
        f"Title: {title}\n\nDescription:\n{description}\n\n"
        f"Seller listing: {source_url}\n\n"
        "Video: video.mp4 is an exact copy of the existing finished master; no new rendering.\n"
        "Captions are burned into the MP4. Optional captions.srt uses approximate phrase timing; "
        "do not add it as a second visible caption layer.\n\n"
        f"Safe-zone review: {config['safe_zone']}\n\n"
        "Check the complete video, text visibility, seller facts, image rights, affiliate disclosure "
        "when applicable, and music licensing before publication. This is not an upload or approval.\n"
        f"Official guidance: {config['source']}\n"
    )
    tmp = tempfile.NamedTemporaryFile(prefix="trafficlift-platform-", suffix=".zip", delete=False)
    tmp.close()
    path = Path(tmp.name)
    try:
        with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
            archive.write(master, "video.mp4", compress_type=zipfile.ZIP_STORED)
            archive.writestr("post.txt", notes, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2), compress_type=zipfile.ZIP_DEFLATED)
            srt = captions_to_srt(directory / "captions.ass")
            if srt:
                archive.writestr("captions.srt", srt, compress_type=zipfile.ZIP_DEFLATED)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path
