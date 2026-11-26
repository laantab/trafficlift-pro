"""Autopilot: one new campaign video per product per day, posted to Pinterest and/or YouTube Shorts.

Every video still passes the campaign editor before it can be queued. A video
the editor holds, or any posting problem, pauses that product's Autopilot until
the owner turns it back on.
"""
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend import campaign_api, pinterest_queue

router = APIRouter(prefix="/api/v1/autopilot")
stop = threading.Event()
worker = None
KEEP_DAYS = 7


def database():
    db = sqlite3.connect(campaign_api.root() / "autopilot.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS plans(
        product TEXT PRIMARY KEY, board TEXT, board_name TEXT, minute INTEGER, zone TEXT,
        enabled INTEGER, last_day TEXT DEFAULT '', job TEXT DEFAULT '', note TEXT DEFAULT '',
        last_job TEXT DEFAULT '')""")
    db.execute("CREATE TABLE IF NOT EXISTS runs(job TEXT PRIMARY KEY, product TEXT, made REAL)")
    have = {row[1] for row in db.execute("PRAGMA table_info(plans)")}
    for column, kind in (("pinterest", "INTEGER DEFAULT 1"), ("youtube", "INTEGER DEFAULT 0"),
                         ("kids", "INTEGER DEFAULT 0"), ("synthetic", "INTEGER DEFAULT 0")):
        if column not in have:
            db.execute(f"ALTER TABLE plans ADD COLUMN {column} {kind}")
    return db


def product_names():
    with closing(campaign_api.database()) as db:
        import json
        names = {row["id"]: json.loads(row["data"])["name"] for row in db.execute("SELECT * FROM products")}
    names["holiday"] = campaign_api.HOLIDAY["name"]
    return names


class Plan(BaseModel):
    product: str = Field(min_length=1, max_length=40)
    board: str = Field(default="", pattern=r"^[0-9]{0,30}$")
    time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    zone: str = Field(min_length=1, max_length=64)
    enabled: bool
    pinterest: bool = True
    youtube: bool = False
    # The channel owner's standing YouTube declarations for these videos.
    kids: bool = False
    synthetic: bool = False


@router.get("", dependencies=[Depends(pinterest_queue.owner)])
def plans():
    names = product_names()
    with closing(database()) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM plans ORDER BY rowid")]
    for row in rows:
        row["name"] = names.get(row["product"], "Removed product")
        row["time"] = "%02d:%02d" % divmod(row.pop("minute"), 60)
        for flag in ("enabled", "pinterest", "youtube", "kids", "synthetic"):
            row[flag] = bool(row[flag])
        row.pop("job")
        row.pop("last_job")
    return rows


@router.post("", dependencies=[Depends(pinterest_queue.owner)])
def save_plan(plan: Plan):
    return save(plan)


def save(plan, now=None):
    if plan.product not in product_names():
        raise HTTPException(404, "Choose a saved product.")
    try:
        zone = ZoneInfo(plan.zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "This device's time zone was not recognized.")
    if not plan.pinterest and not plan.youtube:
        raise HTTPException(422, "Choose Pinterest, YouTube Shorts, or both.")
    board = {"id": "", "name": ""}
    if plan.pinterest:
        board = next((b for b in pinterest_queue.board_list() if b["id"] == plan.board), None)
        if not board:
            raise HTTPException(422, "Choose a board on the connected account.")
    if plan.youtube:
        from backend import youtube_publisher
        if not youtube_publisher.credentials().get("channel_id"):
            raise HTTPException(409, "Choose your YouTube channel in Launch Studio first.")
    hour, minute = map(int, plan.time.split(":"))
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    with closing(database()) as db, db:
        before = db.execute("SELECT enabled,last_day FROM plans WHERE product=?", (plan.product,)).fetchone()
        last_day = before["last_day"] if before else ""
        if plan.enabled and not (before and before["enabled"]) and local.hour*60 + local.minute >= hour*60 + minute:
            # Turned on after today's posting time: the first video goes out tomorrow.
            last_day = local.strftime("%Y-%m-%d")
        db.execute("""INSERT INTO plans(product,board,board_name,minute,zone,enabled,note,pinterest,youtube,kids,synthetic,last_day)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(product) DO UPDATE SET board=excluded.board, board_name=excluded.board_name,
                      minute=excluded.minute, zone=excluded.zone, enabled=excluded.enabled, note=excluded.note,
                      pinterest=excluded.pinterest, youtube=excluded.youtube, kids=excluded.kids,
                      synthetic=excluded.synthetic, last_day=excluded.last_day""",
                   (plan.product, board["id"], board["name"], hour*60+minute, plan.zone, int(plan.enabled),
                    "On. A new video posts each day at this time." if plan.enabled else "Off.",
                    int(plan.pinterest), int(plan.youtube), int(plan.kids), int(plan.synthetic), last_day))
    start()
    return plans()


def update(product, **fields):
    with closing(database()) as db, db:
        db.execute("UPDATE plans SET " + ",".join(k+"=?" for k in fields) + " WHERE product=?",
                   (*fields.values(), product))


def pause(product, reason):
    update(product, enabled=0, job="", note="Paused: " + reason)


def clear_old_videos():
    """Free space by removing Autopilot videos that finished posting a week ago."""
    with closing(database()) as db:
        old = [r["job"] for r in db.execute("SELECT job FROM runs WHERE made<?", (time.time()-KEEP_DAYS*86400,))]
    for job in old:
        with closing(pinterest_queue.database()) as db:
            waiting = db.execute("SELECT 1 FROM pins WHERE campaign=? AND state NOT IN ('published and tracked','failed')",
                                 (job,)).fetchone()
        if waiting:
            continue
        shutil.rmtree(campaign_api.root()/job, ignore_errors=True)
        with closing(campaign_api.database()) as db, db:
            db.execute("DELETE FROM jobs WHERE id=?", (job,))
        with closing(database()) as db, db:
            db.execute("DELETE FROM runs WHERE job=?", (job,))


def tick(now=None):
    now = now or datetime.now(timezone.utc)
    with closing(database()) as db:
        active = [dict(r) for r in db.execute("SELECT * FROM plans WHERE enabled=1")]
    for plan in active:
        product = plan["product"]
        try:
            if plan["last_job"]:
                with closing(pinterest_queue.database()) as db:
                    pin = db.execute("SELECT state FROM pins WHERE campaign=?", (plan["last_job"],)).fetchone()
                if pin and pin[0] in ("failed", "check board"):
                    update(product, last_job="")
                    pause(product, "Pinterest did not confirm the last video. Check the Pinterest connection.")
                    continue
                if plan["youtube"]:
                    from backend import publishing_api
                    short = publishing_api.campaign_job(plan["last_job"])
                    if short and short["state"] in ("failed", "needs_attention"):
                        update(product, last_job="")
                        pause(product, "YouTube did not confirm the last video. Open Launch Studio to check it.")
                        continue
            if plan["job"]:
                with closing(campaign_api.database()) as db:
                    row = db.execute("SELECT status FROM jobs WHERE id=?", (plan["job"],)).fetchone()
                status = row[0] if row else "failed"
                if status == "rendering":
                    continue
                if status != "ready":
                    pause(product, "the video editor stopped today's video. Nothing was posted."
                          if status == "held" else "today's video could not be made. Nothing was posted.")
                    continue
                sent = []
                if plan["youtube"]:
                    from backend import publishing_api
                    publishing_api.queue_campaign_short(uuid.UUID(plan["job"]), publishing_api.CampaignShort(
                        visibility="public", made_for_kids=bool(plan["kids"]),
                        synthetic_media=bool(plan["synthetic"]), confirm=True))
                    sent.append("YouTube Shorts")
                if plan["pinterest"]:
                    pinterest_queue.schedule(uuid.UUID(plan["job"]), pinterest_queue.Schedule(
                        board=plan["board"], when=now + timedelta(seconds=60)))
                    sent.append("Pinterest")
                update(product, job="", last_job=plan["job"],
                       note="Sent a new video to " + " and ".join(sent) + " on " + plan["last_day"] + ".")
                continue
            local = now.astimezone(ZoneInfo(plan["zone"]))
            day = local.strftime("%Y-%m-%d")
            if plan["last_day"] == day or local.hour*60 + local.minute < plan["minute"]:
                continue
            clear_old_videos()
            job = uuid.uuid4().hex
            try:
                campaign_api.launch(product, job, rotate=True)
            except HTTPException as busy:
                if "rendering" in str(busy.detail):
                    continue  # Another video is being made; try again shortly.
                raise
            with closing(database()) as db, db:
                db.execute("INSERT INTO runs VALUES(?,?,?)", (job, product, time.time()))
            update(product, last_day=day, job=job, note="Making today's video…")
        except HTTPException as problem:
            pause(product, str(problem.detail))
        except Exception:
            pause(product, "something went wrong. Nothing was posted.")


def run():
    while not stop.wait(30):
        try:
            tick()
        except Exception:
            continue


@router.on_event("startup")
def start():
    global worker
    # Look without creating folders, so a site that never used Autopilot is untouched.
    if not (campaign_api.studio.storage()/"campaigns"/"autopilot.sqlite").exists():
        return
    if not worker or not worker.is_alive():
        # A video being made when the site restarted cannot finish.
        with closing(database()) as db, db:
            for row in db.execute("SELECT product FROM plans WHERE job!='' AND enabled=1").fetchall():
                db.execute("UPDATE plans SET job='', note=? WHERE product=?",
                           ("The site restarted while making a video. The next one posts tomorrow.", row[0]))
        stop.clear()
        worker = threading.Thread(target=run, daemon=True)
        worker.start()


@router.on_event("shutdown")
def shutdown():
    stop.set()
