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
from fastapi.responses import FileResponse

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


def safe_zone_review_html(platform):
    """Local, non-interactive review of the copied master; no render or network.

    Shaded bands are illustrative caution regions, NOT official measured
    pixel-safe zones. Actual UI changes with captions, device and app version.
    Only static platform configuration is interpolated, never product data.
    """
    from html import escape

    config = PLATFORMS[platform]
    page = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__LABEL__ — local safe-zone review</title>
<style>
  body { background: #121722; color: #f6f7fb; font: 16px/1.5 system-ui, sans-serif;
         max-width: 780px; margin: 0 auto; padding: 20px; }
  h1 { font-size: 1.45rem; }
  .preview { position: relative; width: min(360px, 90vw); aspect-ratio: 9 / 16;
             margin: 20px auto; background: #000; border: 2px solid #e1e7ef;
             --right: 18%; --bottom: 24%; }
  .preview.tiktok { --right: 18%; --bottom: 24%; }
  .preview.youtube-shorts { --right: 18%; --bottom: 21%; }
  .preview.instagram-reels { --right: 18%; --bottom: 27%; }
  video { display: block; width: 100%; height: 100%; object-fit: contain; }
  .caution { position: absolute; pointer-events: none;
             background: rgba(255, 160, 30, 0.18);
             border: 1px dashed rgba(255, 193, 78, 0.9); }
  .top { top: 0; left: 0; right: 0; height: 9%; }
  .right { right: 0; top: 9%; bottom: var(--bottom); width: var(--right); }
  .bottom { bottom: 0; left: 0; right: 0; height: var(--bottom); }
  .notice { border-left: 4px solid #ffc46b; padding: 12px; background: #283040; }
  a { color: #a9d8ff; }
</style>
</head>
<body>
<h1>__LABEL__ — safe-zone review</h1>
<p class="notice">Illustrative caution overlays only, <strong>not official measured
safe zones</strong>. Right and bottom controls and caption areas vary by device,
caption length and app version. Manual in-app preview and approval are required.</p>
<p>Extract this ZIP before opening this file. Play the exact copied
<code>video.mp4</code> below; no upload, remote assets or additional rendering.</p>
<div class="preview __PLATFORM__">
  <video src="video.mp4" controls playsinline preload="metadata"
         aria-label="Finished vertical MP4 master"></video>
  <div class="caution top" aria-hidden="true"></div>
  <div class="caution right" aria-hidden="true"></div>
  <div class="caution bottom" aria-hidden="true"></div>
</div>
<p>Platform-specific review: __GUIDANCE__</p>
<p>Check burned-in captions, product visibility, image rights, facts,
commercial disclosures and audio licensing before publication.
<a href="__SOURCE__" rel="noopener noreferrer">Official platform guidance</a>.
This review is not certification or publication.</p>
</body>
</html>
"""
    return (page.replace("__LABEL__", escape(config["label"]))
                .replace("__PLATFORM__", escape(platform))
                .replace("__GUIDANCE__", escape(config["safe_zone"]))
                .replace("__SOURCE__", escape(config["source"], quote=True)))


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
        "safe_zone_preview": "safe_zone_review.html",
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
            archive.writestr("safe_zone_review.html", safe_zone_review_html(platform), compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2), compress_type=zipfile.ZIP_DEFLATED)
            srt = captions_to_srt(directory / "captions.ass")
            if srt:
                archive.writestr("captions.srt", srt, compress_type=zipfile.ZIP_DEFLATED)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


class LockedExportResponse(FileResponse):
    """Keep the single-export slot until the ZIP is fully sent or aborted.

    Cleanup in an ASGI finally block also covers client disconnects, unlike
    releasing the slot immediately after archive creation.
    """
    def __init__(self, path, *, export_lock, **kwargs):
        super().__init__(path, **kwargs)
        self.export_lock = export_lock

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                Path(self.path).unlink(missing_ok=True)
            finally:
                self.export_lock.release()
