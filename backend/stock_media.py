"""Free stock footage for owner videos: Pexels first, then Pixabay.

Only the two libraries' own hosts are ever contacted, and nothing is uploaded.
Both licenses allow free commercial use without credit; the source and creator
are still recorded beside each video so the owner can see where footage came from.
"""
import logging
import os
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)
HOSTS = ("pexels.com", "pixabay.com", "vimeo.com", "vimeocdn.com", "akamaized.net")
MAX_BYTES = 60 * 1024 * 1024
TIMEOUT = 20


def keys():
    return {"pexels": os.getenv("PEXELS_API_KEY", "").strip(), "pixabay": os.getenv("PIXABAY_API_KEY", "").strip()}


def configured():
    return any(keys().values())


def allowed(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and not parts.username and any(host == h or host.endswith("." + h) for h in HOSTS)


def get(url, **kwargs):
    if not allowed(url):
        raise ValueError("Unexpected stock footage address.")
    response = requests.get(url, timeout=TIMEOUT, allow_redirects=False, **kwargs)
    response.raise_for_status()
    return response


def portrait_file(files):
    """The smallest MP4 that still fills a 1080 x 1920 frame well, else the largest."""
    usable = [f for f in files if f.get("url") and f.get("width") and f.get("height")
              and str(f.get("type", "video/mp4")).endswith("mp4")]
    tall = sorted((f for f in usable if f["height"] >= 1200 and f["height"] <= 2200), key=lambda f: f["height"])
    return (tall or sorted(usable, key=lambda f: -f["height"]) or [None])[0]


def pexels_videos(query, key):
    for path in ("https://api.pexels.com/v1/videos/search", "https://api.pexels.com/videos/search"):
        try:
            data = get(path, headers={"Authorization": key},
                       params={"query": query, "orientation": "portrait", "per_page": 10}).json()
        except requests.HTTPError as problem:
            if problem.response is not None and problem.response.status_code == 404:
                continue
            raise
        for video in data.get("videos", []):
            if not 4 <= float(video.get("duration") or 0) <= 90:
                continue
            best = portrait_file([{"url": f.get("link"), "width": f.get("width"), "height": f.get("height"),
                                   "type": f.get("file_type")} for f in video.get("video_files", [])])
            if best:
                yield {"id": "pexels-video-%s" % video["id"], "kind": "video", "url": best["url"],
                       "source": "Pexels", "creator": (video.get("user") or {}).get("name", ""),
                       "page": video.get("url", "")}
        return


def pexels_photos(query, key):
    data = get("https://api.pexels.com/v1/search", headers={"Authorization": key},
               params={"query": query, "orientation": "portrait", "per_page": 10}).json()
    for photo in data.get("photos", []):
        url = (photo.get("src") or {}).get("large2x") or (photo.get("src") or {}).get("portrait")
        if url:
            yield {"id": "pexels-photo-%s" % photo["id"], "kind": "photo", "url": url, "source": "Pexels",
                   "creator": photo.get("photographer", ""), "page": photo.get("url", "")}


_FILLER = {"a", "an", "the", "of", "in", "on", "at", "to", "and", "for", "with", "from", "up"}


def on_topic(query, hits):
    """Pixabay returns loosely related results, so keep only hits whose own tags
    name most of what was asked for, best match first."""
    words = [w for w in query.lower().split() if w not in _FILLER]
    need = max(1, (len(words) + 1) // 2)
    scored = []
    for hit in hits:
        tags = [t.strip() for t in str(hit.get("tags", "")).lower().replace(",", " ").split()]
        score = sum(any(t.startswith(w[:4]) or w.startswith(t[:4]) for t in tags if len(t) > 2) for w in words)
        if score >= need:
            scored.append((score, hit))
    return [hit for _, hit in sorted(scored, key=lambda pair: -pair[0])]


def pixabay_videos(query, key):
    data = get("https://pixabay.com/api/videos/",
               params={"key": key, "q": query, "per_page": 30, "safesearch": "true"}).json()
    for hit in on_topic(query, data.get("hits", [])):
        if not 4 <= float(hit.get("duration") or 0) <= 90:
            continue
        best = portrait_file([{"url": v.get("url"), "width": v.get("width"), "height": v.get("height")}
                              for v in (hit.get("videos") or {}).values() if isinstance(v, dict)])
        if best:
            yield {"id": "pixabay-video-%s" % hit["id"], "kind": "video", "url": best["url"], "source": "Pixabay",
                   "creator": hit.get("user", ""), "page": hit.get("pageURL", "")}


def pixabay_photos(query, key):
    data = get("https://pixabay.com/api/", params={"key": key, "q": query, "image_type": "photo",
               "orientation": "vertical", "per_page": 30, "safesearch": "true"}).json()
    for hit in on_topic(query, data.get("hits", [])):
        if hit.get("largeImageURL"):
            yield {"id": "pixabay-photo-%s" % hit["id"], "kind": "photo", "url": hit["largeImageURL"],
                   "source": "Pixabay", "creator": hit.get("user", ""), "page": hit.get("pageURL", "")}


def candidates(query):
    """Moving clips before still photos; Pexels before Pixabay."""
    have = keys()
    searches = [(pexels_videos, have["pexels"]), (pixabay_videos, have["pixabay"]),
                (pexels_photos, have["pexels"]), (pixabay_photos, have["pixabay"])]
    for search, key in searches:
        if not key:
            continue
        try:
            yield from search(query, key)
        except Exception as problem:
            # Never log the address: Pixabay carries its key in the query string.
            status = getattr(getattr(problem, "response", None), "status_code", "")
            log.warning("Stock search unavailable (%s): %s %s", search.__name__, type(problem).__name__, status)


def download(item, target):
    url = item["url"]
    for _ in range(4):
        with get(url, stream=True) as response:
            if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                url = response.headers["location"]
                continue
            size = 0
            with open(target, "wb") as out:
                for chunk in response.iter_content(1 << 16):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("Stock clip is too large.")
                    out.write(chunk)
            if size < 2048:
                raise ValueError("Stock clip download was empty.")
            return target
    raise ValueError("Stock clip moved too many times.")


def collect(queries, folder):
    """One clip or photo per scene, never the same one twice. Returns files and credits."""
    used, files, credits = set(), [], []
    for index, query in enumerate(queries):
        chosen = None
        for item in candidates(query):
            if item["id"] in used:
                continue
            target = folder / ("stock%d.%s" % (index, "mp4" if item["kind"] == "video" else "jpg"))
            try:
                download(item, target)
            except Exception as problem:
                log.warning("Stock download skipped: %s", type(problem).__name__)
                target.unlink(missing_ok=True)
                continue
            chosen = (target, item)
            break
        if not chosen:
            raise LookupError("No free stock footage was found for: " + query)
        used.add(chosen[1]["id"])
        files.append(chosen[0])
        credits.append({"scene": index + 1, "kind": chosen[1]["kind"], "source": chosen[1]["source"],
                        "creator": chosen[1]["creator"], "page": chosen[1]["page"]})
    return files, credits
