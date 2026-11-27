"""Facebook Reels for the 30-day plan: the same daily video, on the connected Page.

Posting lives here, apart from the plan and its video maker, and goes through
the same Facebook queue as a Comic Book Creator Autopilot Reel. Each plan day
is posted at most once.
"""
from contextlib import closing
import datetime
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend import facebook_publisher, growth_api, growth_plan as growth
from backend import photo_video_api as studio
from backend.campaign_editor import verified

router = APIRouter()
log = logging.getLogger(__name__)


def database():
    db = growth_api.database()
    db.execute("CREATE TABLE IF NOT EXISTS reels (day INTEGER PRIMARY KEY, video TEXT NOT NULL, queued TEXT NOT NULL)")
    return db


def connection():
    """The connected Facebook Page, or None when it is not ready to post."""
    return facebook_publisher.page()


def post(day, video):
    """Queue one plan video as a Facebook Reel. Raises HTTPException when it cannot."""
    found = connection()
    if not found:
        raise HTTPException(409, "Connect your Facebook Page first.")
    folder = growth_api.root() / video
    with closing(database()) as db, db:
        if db.execute("SELECT 1 FROM reels WHERE day=?", (day,)).fetchone():
            raise HTTPException(409, "This day's video is already on its way to Facebook.")
        row = db.execute("SELECT day FROM videos WHERE id=? AND status='ready'", (video,)).fetchone()
    if not row or row["day"] != day or not verified(folder, "video"):
        raise HTTPException(409, "This day has no checked video yet. Make the video first.")
    item = growth.stock_entry(day) or growth.entry(day)
    description = item["caption"] + "\n\n" + " ".join(item["hashtags"]) + "\n\n" + growth.SHOP_URL
    facebook_publisher.queue("growth", video, item["topic"], description, folder / "video.mp4")
    with closing(database()) as db, db:
        db.execute("INSERT OR IGNORE INTO reels VALUES (?,?,?)", (day, video, datetime.datetime.now().isoformat()))


def after_video(day, video):
    """Called when a plan video passes its checks. Never raises."""
    try:
        with closing(database()) as db:
            on = growth_api.setting(db, "facebook_auto") == "1"
        if on and connection():
            post(day, video)
    except HTTPException as waiting:
        log.info("Plan Reel not queued: %s", waiting.detail)
    except Exception:
        log.exception("Plan Reel could not be queued")


def catch_up():
    """Post today's video if it was made before automatic posting could send it. Never raises."""
    try:
        if not (studio.storage() / "growth" / "growth.sqlite").exists():
            return
        with closing(database()) as db, db:
            if growth_api.setting(db, "facebook_auto") != "1" or not growth_api.setting(db, "start"):
                return
            _, day = growth_api.campaign_day(db)
            if db.execute("SELECT 1 FROM reels WHERE day=?", (day,)).fetchone():
                return
            row = db.execute("SELECT id FROM videos WHERE day=? AND status='ready' ORDER BY made DESC", (day,)).fetchone()
        if row and connection():
            post(day, row["id"])
    except HTTPException as waiting:
        log.info("Plan Reel not queued: %s", waiting.detail)
    except Exception:
        log.exception("Plan Reel catch-up failed")


@router.get("/api/v1/growth/facebook")
def read(request: Request):
    studio.video_guard(request)
    found = connection()
    with closing(database()) as db, db:
        auto = growth_api.setting(db, "facebook_auto") == "1"
        rows = [dict(row) for row in db.execute("SELECT * FROM reels ORDER BY day")]
    reels = []
    for row in rows:
        state = facebook_publisher.status_for("growth", row["video"])
        item = growth.entry(row["day"])
        reels.append({"day": row["day"], "video": row["video"], "topic": item["topic"], "state": state or "unknown"})
    return {"configured": facebook_publisher.configured(), "connected": bool(found),
            "page_name": (found or {}).get("name"), "auto": auto, "reels": reels}


class Auto(BaseModel):
    auto: bool


@router.post("/api/v1/growth/facebook/settings")
def save(payload: Auto, request: Request):
    studio.video_guard(request)
    with closing(database()) as db, db:
        db.execute("INSERT OR REPLACE INTO settings VALUES ('facebook_auto',?)", ("1" if payload.auto else "0",))
    return {"auto": payload.auto}


class Day(BaseModel):
    day: int = Field(ge=1, le=growth.DAYS)


@router.post("/api/v1/growth/facebook/post")
def post_now(payload: Day, request: Request):
    studio.video_guard(request)
    with closing(database()) as db:
        row = db.execute("SELECT id FROM videos WHERE day=? AND status='ready' ORDER BY made DESC", (payload.day,)).fetchone()
    if not row:
        raise HTTPException(409, "This day has no checked video yet. Make the video first.")
    post(payload.day, row["id"])
    return {"queued": True}
