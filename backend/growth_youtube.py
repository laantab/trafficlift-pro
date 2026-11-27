"""YouTube Shorts for the 30-day plan: the same daily video, on the tips channel.

The owner asked for these to post automatically. Posting lives here, apart from
the plan and its video maker, and goes through the same encrypted, deduplicating
YouTube queue as every other upload. Each plan day is posted at most once.
"""
from contextlib import closing
import datetime
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend import growth_api, growth_plan as growth
from backend import photo_video_api as studio
from backend import publishing_api, publishing_store as store
from backend import youtube_publisher as youtube
from backend.campaign_editor import verified

router = APIRouter()
log = logging.getLogger(__name__)
SLOT = "tips"
GOAL = 1000
_stats = {"at": 0.0, "value": None}


def database():
    db = growth_api.database()
    db.execute("CREATE TABLE IF NOT EXISTS shorts (day INTEGER PRIMARY KEY, video TEXT NOT NULL, queued TEXT NOT NULL)")
    return db


def connection():
    """The tips channel connection, or None when it is not ready to post."""
    if not youtube.configured():
        return None
    try:
        found = youtube.credentials(slot=SLOT)
    except HTTPException:
        return None
    return found if found.get("channel_id") else None


def post(day, video):
    """Queue one plan video as a public Short. Raises HTTPException when it cannot."""
    found = connection()
    if not found:
        raise HTTPException(409, "Connect your tips channel to YouTube first.")
    folder = growth_api.root() / video
    with closing(database()) as db, db:
        if db.execute("SELECT 1 FROM shorts WHERE day=?", (day,)).fetchone():
            raise HTTPException(409, "This day's video is already on its way to YouTube.")
        row = db.execute("SELECT day FROM videos WHERE id=? AND status='ready'", (video,)).fetchone()
    if not row or row["day"] != day or not verified(folder, "video"):
        raise HTTPException(409, "This day has no checked video yet. Make the video first.")
    item = growth.stock_entry(day) or growth.entry(day)
    description = item["caption"] + "\n\n" + " ".join(item["hashtags"] + ["#shorts"])
    product = {"id": "growth-day-%02d" % day, "title": item["topic"], "product_url": growth.PROFILE}
    choice = publishing_api.CampaignShort(visibility="public", made_for_kids=False, synthetic_media=False, confirm=True)
    job = publishing_api.queue_short(folder / "video.mp4", product, item["topic"], description, "",
                                     choice, "growth", video, found)
    with closing(database()) as db, db:
        db.execute("INSERT OR IGNORE INTO shorts VALUES (?,?,?)", (day, video, datetime.datetime.now().isoformat()))
    return job


def after_video(day, video):
    """Called when a plan video passes its checks. Never raises."""
    try:
        with closing(database()) as db, db:
            on = growth_api.setting(db, "youtube_auto") == "1"
        if on and connection():
            post(day, video)
    except HTTPException as waiting:
        log.info("Plan Short not queued: %s", waiting.detail)
    except Exception:
        log.exception("Plan Short could not be queued")


def numbers(found, video_ids):
    """Subscriber and per-Short counts straight from YouTube, refreshed at most every ten minutes."""
    if _stats["value"] is not None and time.time() - _stats["at"] < 600 and set(video_ids) <= set(_stats["value"]["videos"]):
        return _stats["value"]
    value = {"subscribers": None, "videos": {}}
    try:
        channel = youtube.api_get("channels", found, part="statistics", id=found["channel_id"])
        counts = (channel.get("items") or [{}])[0].get("statistics", {})
        if not counts.get("hiddenSubscriberCount"):
            value["subscribers"] = int(counts.get("subscriberCount", 0))
        if video_ids:
            listed = youtube.api_get("videos", found, part="statistics", id=",".join(video_ids[:50]))
            for item in listed.get("items", []):
                stats = item.get("statistics", {})
                value["videos"][item["id"]] = {"views": int(stats.get("viewCount", 0)), "likes": int(stats.get("likeCount", 0)),
                                               "comments": int(stats.get("commentCount", 0))}
    except (HTTPException, KeyError, ValueError, TypeError):
        return value
    _stats.update(at=time.time(), value=value)
    return value


@router.get("/api/v1/growth/youtube")
def read(request: Request):
    studio.video_guard(request)
    record = store.get("youtube", SLOT) if youtube.configured() else None
    found = connection()
    with closing(database()) as db, db:
        auto = growth_api.setting(db, "youtube_auto") == "1"
        rows = [dict(row) for row in db.execute("SELECT * FROM shorts ORDER BY day")]
    shorts = []
    for row in rows:
        job = publishing_api.campaign_job(row["video"], "growth")
        item = growth.entry(row["day"])
        shorts.append({"day": row["day"], "topic": item["topic"], "state": job["state"] if job else "unknown",
                       "message": (job or {}).get("message", ""), "video_id": (job or {}).get("video_id"),
                       "url": "https://www.youtube.com/shorts/" + job["video_id"] if job and job.get("video_id") else None})
    live = numbers(found, [s["video_id"] for s in shorts if s["video_id"]]) if found else {"subscribers": None, "videos": {}}
    for short in shorts:
        short.update(live["videos"].get(short.pop("video_id") or "", {}))
    title = next((c["title"] for c in (record or {}).get("channels", []) if c["id"] == record.get("channel_id")), None)
    return {"configured": youtube.configured(), "connected": bool(record), "ready": bool(found),
            "channels": (record or {}).get("channels", []), "channel": title, "auto": auto,
            "subscribers": live["subscribers"], "goal": GOAL, "shorts": shorts}


class Auto(BaseModel):
    auto: bool


@router.post("/api/v1/growth/youtube/settings")
def save(payload: Auto, request: Request):
    studio.video_guard(request)
    with closing(database()) as db, db:
        db.execute("INSERT OR REPLACE INTO settings VALUES ('youtube_auto',?)", ("1" if payload.auto else "0",))
    return {"auto": payload.auto}


class Day(BaseModel):
    day: int = Field(ge=1, le=growth.DAYS)


@router.post("/api/v1/growth/youtube/post")
def post_now(payload: Day, request: Request):
    studio.video_guard(request)
    with closing(database()) as db, db:
        row = db.execute("SELECT id FROM videos WHERE day=? AND status='ready' ORDER BY made DESC", (payload.day,)).fetchone()
    if not row:
        raise HTTPException(409, "This day has no checked video yet. Make the video first.")
    return post(payload.day, row["id"])
