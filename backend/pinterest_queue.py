"""Owner-approved Video Pins. One publication per campaign and board."""
import base64
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from backend import ad_results, campaign_api
from backend import publishing_store as store
from backend.campaign_editor import verified
from backend.photo_video_api import video_guard

router = APIRouter(prefix="/api/v1/pinterest")
stop = threading.Event()
worker = None
log = logging.getLogger(__name__)
refreshing = threading.Lock()
SCOPES = {"boards:read", "pins:read", "pins:write", "user_accounts:read"}
TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"
COOKIE = "trafficlift_pinterest_oauth"
CALLBACK = "/api/v1/pinterest/callback"
MISSING = "Pinterest publishing needs a TrafficLift account connection."


def owner(request: Request):
    video_guard(request)
    if request.method == "POST" and request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Open Pinterest scheduling in Owner Studio.")


def database():
    db = sqlite3.connect(campaign_api.root() / "pinterest.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS pins(
        campaign TEXT, board TEXT, due REAL, state TEXT, title TEXT, description TEXT,
        destination TEXT, pin TEXT DEFAULT '', PRIMARY KEY(campaign,board))""")
    return db


def app_keys():
    """Pinterest app ID and secret, kept in the deployment's private settings."""
    app_id, secret = os.getenv("PINTEREST_APP_ID", ""), os.getenv("PINTEREST_APP_SECRET", "")
    return (app_id, secret) if app_id and secret else None


def exchange(fields):
    response = requests.post(TOKEN_URL, auth=app_keys(), data=fields, timeout=30, allow_redirects=False)
    response.raise_for_status()
    tokens = response.json()
    return {"access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token", ""),
            "expires_at": time.time() + int(tokens.get("expires_in", 3600))}


def token():
    """A connected owner's token, renewed shortly before it expires."""
    if not app_keys() or not os.getenv("TRAFFICLIFT_PUBLISHING_KEY"):
        return os.getenv("TRAFFICLIFT_PINTEREST_TOKEN")
    with refreshing:
        grant = store.get("pinterest", "owner")
        if not grant:
            return os.getenv("TRAFFICLIFT_PINTEREST_TOKEN")
        if grant["expires_at"] - time.time() < 300:
            if not grant.get("refresh_token"):
                return None
            renewed = exchange({"grant_type": "refresh_token", "refresh_token": grant["refresh_token"]})
            renewed["refresh_token"] = renewed["refresh_token"] or grant["refresh_token"]
            store.put("pinterest", "owner", renewed)
            grant = renewed
        return grant["access_token"]


def api(method, path, **kwargs):
    bearer = token()
    if not bearer:
        raise HTTPException(503, MISSING)
    response = requests.request(method, "https://api.pinterest.com/v5/" + path,
                                headers={"Authorization": "Bearer " + bearer}, timeout=30,
                                allow_redirects=False, **kwargs)
    if response.status_code >= 400:
        # Pinterest explains refusals in the reply body; keep it for the owner's logs.
        log.warning("Pinterest refused %s %s: %s %s", method, path.split("/")[0], response.status_code, response.text[:600])
    response.raise_for_status()
    return response.json()


@router.get("/connect", dependencies=[Depends(owner)])
def connect(request: Request):
    """Send the signed-in owner to Pinterest's own permission screen."""
    keys = app_keys()
    if not keys:
        raise HTTPException(503, "Pinterest connection is awaiting server setup.")
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    store.put("pinterest", "pending", {"state": hashlib.sha256(state.encode()).hexdigest(),
              "browser": hashlib.sha256(browser.encode()).hexdigest(), "expires_at": time.time() + 600})
    url = "https://www.pinterest.com/oauth/?" + urlencode({
        "client_id": keys[0], "redirect_uri": str(request.base_url).rstrip("/") + CALLBACK,
        "response_type": "code", "scope": ",".join(sorted(SCOPES)), "state": state})
    response = RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})
    response.set_cookie(COOKIE, browser, httponly=True, secure=True, samesite="lax", max_age=600, path=CALLBACK)
    return response


@router.get("/callback")
def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    # Keep the one-time code out of the access log.
    request.scope["query_string"] = b""
    request.scope["raw_path"] = request.url.path.encode()
    if not app_keys():
        raise HTTPException(503, "Pinterest connection is awaiting server setup.")
    pending = store.pop("pinterest", "pending")
    browser = request.cookies.get(COOKIE, "")
    if (not pending or not state or len(state) > 128 or not browser or pending["expires_at"] < time.time()
            or not secrets.compare_digest(pending["state"], hashlib.sha256(state.encode()).hexdigest())
            or not secrets.compare_digest(pending["browser"], hashlib.sha256(browser.encode()).hexdigest())):
        raise HTTPException(400, "Pinterest connection request expired. Connect again from Owner Studio.")
    if error or not code or len(code) > 4096:
        raise HTTPException(400, "Pinterest connection was declined. Connect again when ready.")
    try:
        grant = exchange({"grant_type": "authorization_code", "code": code,
                          "redirect_uri": str(request.base_url).rstrip("/") + CALLBACK,
                          "continuous_refresh": "true"})
    except (requests.RequestException, KeyError, ValueError):
        raise HTTPException(502, "Pinterest did not complete the connection. Connect again.")
    store.put("pinterest", "owner", grant)
    start()
    response = RedirectResponse("/owner?pinterest=connected", status_code=303,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.delete_cookie(COOKIE, path=CALLBACK)
    return response


@router.post("/disconnect", dependencies=[Depends(owner)])
def disconnect():
    store.pop("pinterest", "owner")
    return {"connected": False}


def boards():
    result = []
    bookmark = None
    while True:
        params = {"page_size": 100}
        if bookmark:
            params["bookmark"] = bookmark
        page = api("GET", "boards", params=params)
        result.extend({"id": b["id"], "name": b["name"]} for b in page["items"])
        bookmark = page.get("bookmark")
        if not bookmark:
            return result


@router.get("/boards", dependencies=[Depends(owner)])
def board_list():
    try:
        return boards()
    except requests.RequestException:
        raise HTTPException(503, "Check the Pinterest account connection.")


class Schedule(BaseModel):
    board: str = Field(pattern=r"^[0-9]{1,30}$")
    when: datetime


@router.post("/{campaign}", dependencies=[Depends(owner)])
def schedule(campaign: uuid.UUID, selection: Schedule):
    key = campaign.hex
    when = selection.when
    if when.tzinfo is None or when.timestamp() <= time.time():
        raise HTTPException(422, "Choose a future posting time.")
    folder = campaign_api.root()/key
    if not verified(folder, "kit") or not verified(folder, "video"):
        raise HTTPException(409, "The campaign needs a verified video and posting kit.")
    if selection.board not in {b["id"] for b in board_list()}:
        raise HTTPException(422, "Choose a board on the connected account.")
    with closing(campaign_api.database()) as db:
        job = db.execute("SELECT product FROM jobs WHERE id=? AND status='ready'", (key,)).fetchone()
        if not job:
            raise HTTPException(409, "Campaign is unavailable.")
        product = campaign_api.HOLIDAY if job[0]=="holiday" else json.loads(db.execute(
            "SELECT data FROM products WHERE id=?", (job[0],)).fetchone()[0])
    with zipfile.ZipFile(folder/"campaign.zip") as archive:
        description = archive.read("facebook-reels.txt").decode().rsplit("\n",1)[0]
    with closing(database()) as db, db:
        db.execute("INSERT OR IGNORE INTO pins(campaign,board,due,state,title,description,destination) VALUES(?,?,?,'scheduled',?,?,?)",
                   (key,selection.board,when.timestamp(),product["name"],description[:800],product["destination"]))
    return history(campaign)


@router.get("/{campaign}", dependencies=[Depends(owner)])
def history(campaign: uuid.UUID):
    with closing(database()) as db:
        return [dict(r) for r in db.execute("SELECT board,due,state,pin FROM pins WHERE campaign=? ORDER BY due",(campaign.hex,))]


def change(row, state, pin=""):
    with closing(database()) as db, db:
        db.execute("UPDATE pins SET state=?,pin=? WHERE campaign=? AND board=?",
                   (state,pin,row["campaign"],row["board"]))


def publish(row):
    folder = campaign_api.root()/row["campaign"]
    if not verified(folder, "kit") or not verified(folder, "video"):
        raise ValueError("Media changed")
    media = api("POST", "media", json={"media_type":"video"})
    with (folder/"video.mp4").open("rb") as stream:
        response = requests.post(media["upload_url"], data=media["upload_parameters"],
                                 files={"file":("video.mp4", stream, "video/mp4")}, timeout=120)
        response.raise_for_status()
    for _ in range(60):
        state = api("GET", "media/"+media["media_id"])["status"]
        if state == "succeeded":
            break
        if state == "failed":
            raise ValueError("Media failed")
        time.sleep(5)
    else:
        raise TimeoutError()
    with zipfile.ZipFile(folder/"campaign.zip") as archive:
        cover = base64.b64encode(archive.read("cover-frame.jpg")).decode()
    change(row, "submitting")
    result = api("POST", "pins", json={"board_id":row["board"], "title":row["title"],
                 "description":row["description"], "link":row["destination"],
                 "media_source":{"source_type":"video_id","media_id":media["media_id"],
                                 "cover_image_content_type":"image/jpeg","cover_image_data":cover}})
    pin = str(result["id"])
    if not pin.isdigit():
        raise ValueError("Missing Pin ID")
    change(row, "published", pin)


def tick():
    with closing(database()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM pins WHERE state='scheduled' AND due<=? ORDER BY due LIMIT 1",(time.time(),)).fetchone()
        if row:
            db.execute("UPDATE pins SET state='uploading' WHERE campaign=? AND board=?", (row["campaign"],row["board"]))
    if row:
        try:
            publish(row)
        except Exception as problem:
            log.warning("Pinterest video Pin was not confirmed: %s", type(problem).__name__)
            with closing(database()) as db, db:
                db.execute("""UPDATE pins SET state=CASE WHEN state='submitting' THEN 'check board' ELSE 'failed' END
                              WHERE campaign=? AND board=? AND state!='published'""",(row["campaign"],row["board"]))
    with closing(database()) as db, db:
        for item in db.execute("SELECT * FROM pins WHERE state='published'").fetchall():
            with closing(ad_results.database()) as analytics, analytics:
                ad_results.add_subject(analytics, ad_results.Subject(platform="Pinterest",scope="Post",
                    label=item["title"],reference="https://www.pinterest.com/pin/"+item["pin"]+"/"))
            change_state = "published and tracked"
            db.execute("UPDATE pins SET state=? WHERE campaign=? AND board=?", (change_state,item["campaign"],item["board"]))


def run():
    while not stop.wait(10):
        try:
            tick()
        except Exception:
            continue


@router.on_event("startup")
def start():
    global worker
    if (os.getenv("TRAFFICLIFT_PINTEREST_TOKEN") or app_keys()) and (not worker or not worker.is_alive()):
        with closing(database()) as db, db:
            db.execute("UPDATE pins SET state='scheduled' WHERE state='uploading'")
            db.execute("UPDATE pins SET state='check board' WHERE state='submitting'")
        stop.clear()
        worker = threading.Thread(target=run, daemon=True)
        worker.start()


@router.on_event("shutdown")
def shutdown():
    stop.set()
