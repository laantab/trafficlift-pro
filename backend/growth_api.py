"""Owner-only 30-day TikTok growth planner, free video maker and results log.

Reuses the existing campaign renderer and its release checks. Nothing here
calls a paid provider, and nothing is ever posted: the owner downloads the
reviewed video and posts it personally.
"""
import base64
from contextlib import closing
import datetime
import io
import json
import logging
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend import growth_plan as growth
from backend import photo_video_api as studio
from backend.campaign_api import render_lock
from backend.campaign_editor import EditorialHold, verified
from backend.campaigns import render_campaign

router = APIRouter()
log = logging.getLogger(__name__)
MAX_VIDEOS = 60
FRAME = (930, 940)
NUMBERS = ("views", "watch_seconds", "retention", "likes", "comments", "shares", "saves",
           "profile_visits", "follower_change")


def root():
    folder = studio.storage() / "growth"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def database():
    db = sqlite3.connect(root() / "growth.sqlite", timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS videos (id TEXT PRIMARY KEY, day INTEGER, status TEXT, lines TEXT, made TEXT)")
    db.execute("""CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY AUTOINCREMENT, posted TEXT NOT NULL,
        day INTEGER, topic TEXT NOT NULL, category TEXT, format TEXT, views INTEGER, watch_seconds REAL,
        retention REAL, likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER,
        profile_visits INTEGER, follower_change INTEGER)""")
    return db


def setting(db, key, default=None):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def today():
    return datetime.date.today()


def campaign_day(db):
    start = setting(db, "start")
    if not start:
        start = today().isoformat()
        db.execute("INSERT OR REPLACE INTO settings VALUES ('start',?)", (start,))
    elapsed = (today() - datetime.date.fromisoformat(start)).days + 1
    return start, min(growth.DAYS, max(1, elapsed))


class Settings(BaseModel):
    start: Optional[datetime.date] = None
    followers: Optional[int] = Field(default=None, ge=0, le=100_000_000)


class Check(BaseModel):
    day: int = Field(ge=1, le=growth.DAYS)
    lines: list[str] = Field(min_length=4, max_length=4)


class Video(Check):
    request_id: uuid.UUID
    images: list[str] = Field(min_length=1, max_length=4)


class Post(BaseModel):
    posted: datetime.date
    day: Optional[int] = Field(default=None, ge=1, le=growth.DAYS)
    topic: str = Field(min_length=2, max_length=120)
    category: Optional[str] = None
    format: Optional[str] = None
    views: Optional[int] = Field(default=None, ge=0)
    watch_seconds: Optional[float] = Field(default=None, ge=0, le=600)
    retention: Optional[float] = Field(default=None, ge=0, le=100)
    likes: Optional[int] = Field(default=None, ge=0)
    comments: Optional[int] = Field(default=None, ge=0)
    shares: Optional[int] = Field(default=None, ge=0)
    saves: Optional[int] = Field(default=None, ge=0)
    profile_visits: Optional[int] = Field(default=None, ge=0)
    follower_change: Optional[int] = Field(default=None, ge=-100000, le=10_000_000)


@router.get("/growth")
def page():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "growth.html",
                        headers={"Cache-Control": "no-store"})


def overview(db):
    start, day = campaign_day(db)
    posts = [dict(row) for row in db.execute("SELECT * FROM posts ORDER BY posted DESC, id DESC")]
    followers = setting(db, "followers")
    followers = int(followers) if followers is not None else None
    logged = {post["day"] for post in posts if post["day"]}
    made = {row["day"]: row["id"] for row in
            db.execute("SELECT day,id FROM videos WHERE status='ready' ORDER BY made")}
    plan = []
    for item in growth.plan():
        checks = growth.review(item)
        plan.append({**item, "checks": checks, "ready": all(c["ok"] for c in checks),
                     "posted": item["day"] in logged, "video": made.get(item["day"])})
    return {"account": growth.ACCOUNT, "handle": growth.HANDLE, "profile": growth.PROFILE,
            "start": start, "day": day, "days": growth.DAYS,
            "start_followers": growth.START_FOLLOWERS, "goal": growth.GOAL_FOLLOWERS,
            "categories": growth.CATEGORIES,
            "formats": {key: {"name": name, "how": how} for key, (name, how) in growth.FORMATS.items()},
            "plan": plan, "posts": posts,
            "advice": growth.advice(posts, followers, day)}


@router.get("/api/v1/growth")
def read(request: Request):
    studio.video_guard(request)
    with closing(database()) as db, db:
        return overview(db)


@router.post("/api/v1/growth/settings")
def save_settings(payload: Settings, request: Request):
    studio.video_guard(request)
    with closing(database()) as db, db:
        if payload.start:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('start',?)", (payload.start.isoformat(),))
        if payload.followers is not None:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('followers',?)", (str(payload.followers),))
        return overview(db)


@router.post("/api/v1/growth/check")
def check(payload: Check, request: Request):
    studio.video_guard(request)
    checks = growth.review(growth.entry(payload.day), payload.lines)
    return {"checks": checks, "ready": all(c["ok"] for c in checks)}


def build(job, product, images, lines):
    try:
        render_campaign(root() / job, product, images, lines)
        status = "ready"
    except EditorialHold:
        log.exception("Growth video held by editorial review")
        status = "held"
    except Exception:
        log.exception("Growth video render failed")
        status = "failed"
    finally:
        try:
            with closing(database()) as db, db:
                db.execute("UPDATE videos SET status=? WHERE id=?", (status, job))
        finally:
            render_lock.release()


@router.post("/api/v1/growth/videos", status_code=202)
def create(payload: Video, request: Request):
    """Render one free, narrated vertical video from the owner's own photos."""
    from PIL import Image, ImageOps
    studio.video_guard(request)
    item = growth.entry(payload.day)
    lines = [line.strip() for line in payload.lines]
    failed = [c for c in growth.review(item, lines) if not c["ok"]]
    if failed:
        raise HTTPException(422, "Fix this before making the video: " + failed[0]["check"] + ". " + failed[0]["note"])
    job = payload.request_id.hex
    with closing(database()) as db, db:
        if db.execute("SELECT 1 FROM videos WHERE id=?", (job,)).fetchone():
            return {"id": job}
        if db.execute("SELECT COUNT(*) FROM videos").fetchone()[0] >= MAX_VIDEOS:
            raise HTTPException(409, "Video storage for this plan is full. Ask for older videos to be cleared.")
    if not render_lock.acquire(False):
        raise HTTPException(409, "Another video is rendering. Try again when it finishes.")
    folder = root() / job
    try:
        folder.mkdir()
        photos = []
        for i, encoded in enumerate(payload.images):
            if len(encoded) > 3_000_000:
                raise ValueError("photo too large")
            with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as picture:
                if picture.width * picture.height > 12_000_000:
                    raise ValueError("photo too large")
                clean = ImageOps.contain(ImageOps.exif_transpose(picture).convert("RGB"), FRAME)
            # Fit every photo into one frame shape on the video's own background,
            # so a tall phone photo never covers the opening words under it.
            framed = Image.new("RGB", FRAME, "#f8f1e7")
            framed.paste(clean, ((FRAME[0] - clean.width) // 2, (FRAME[1] - clean.height) // 2))
            framed.save(folder / f"photo{i}.jpg", quality=92)
            photos.append(folder / f"photo{i}.jpg")
        images = [photos[min(i, len(photos) - 1)] for i in range(4)]
        studio.reserve_hosted_render()
        with closing(database()) as db, db:
            db.execute("INSERT INTO videos VALUES (?,?,?,?,?)",
                       (job, payload.day, "rendering", json.dumps(lines), datetime.datetime.now().isoformat()))
        product = {"name": growth.ACCOUNT, "destination": growth.PROFILE, "feature": item["topic"],
                   "prompt": "", "affiliate": False, "cover_text": item["cover_text"]}
        threading.Thread(target=build, args=(job, product, images, lines), daemon=True).start()
    except (ValueError, OSError) as exc:
        render_lock.release()
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, "A photo could not be read. Use clear JPG or PNG photos.") from exc
    except Exception:
        render_lock.release()
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {"id": job}


@router.on_event("startup")
def recover_interrupted_videos():
    if (studio.storage() / "growth" / "growth.sqlite").exists():
        with closing(database()) as db, db:
            db.execute("UPDATE videos SET status='failed' WHERE status='rendering'")


@router.get("/api/v1/growth/videos/{job}")
def status(job: uuid.UUID, request: Request):
    studio.video_guard(request)
    with closing(database()) as db, db:
        row = db.execute("SELECT day,status,lines FROM videos WHERE id=?", (job.hex,)).fetchone()
    if not row:
        raise HTTPException(404, "Video was not found.")
    state = row["status"]
    if state == "ready" and not verified(root() / job.hex, "video"):
        state = "held"
    return {"status": state, "day": row["day"], "lines": json.loads(row["lines"])}


@router.get("/api/v1/growth/videos/{job}/video")
def download(job: uuid.UUID, request: Request, save: int = 0):
    state = status(job, request)
    if state["status"] != "ready":
        raise HTTPException(409, "This video is not ready. Make it again.")
    name = f"4your-Good-Health-Day-{state['day']:02d}.mp4" if save else None
    return FileResponse(root() / job.hex / "video.mp4", media_type="video/mp4", filename=name,
                        headers={"Cache-Control": "private, no-store"})


@router.post("/api/v1/growth/posts")
def log_post(payload: Post, request: Request):
    studio.video_guard(request)
    if payload.category and payload.category not in growth.CATEGORIES:
        raise HTTPException(422, "Choose one of the five content categories.")
    if payload.format and payload.format not in growth.FORMATS:
        raise HTTPException(422, "Choose one of the listed formats.")
    data = payload.model_dump()
    data["posted"] = payload.posted.isoformat()
    fields = ("posted", "day", "topic", "category", "format", *NUMBERS)
    with closing(database()) as db, db:
        db.execute(f"INSERT INTO posts ({','.join(fields)}) VALUES ({','.join('?' * len(fields))})",
                   [data[f] for f in fields])
        return overview(db)


@router.delete("/api/v1/growth/posts/{post}")
def remove_post(post: int, request: Request):
    studio.video_guard(request)
    with closing(database()) as db, db:
        db.execute("DELETE FROM posts WHERE id=?", (post,))
        return overview(db)
