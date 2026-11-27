"""Facebook Page Reels. Owner-approved posts only, mirroring the Pinterest flow.

One Facebook Page, chosen once after connecting. Posting reuses the same
rendered vertical video and caption already made for YouTube/Pinterest/TikTok;
nothing here generates or edits media.
"""
import hashlib
import logging
import os
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from backend import ad_results, campaign_api
from backend import publishing_store as store
from backend.photo_video_api import video_guard

router = APIRouter(prefix="/api/v1/facebook")
stop = threading.Event()
worker = None
log = logging.getLogger(__name__)

API = "https://graph.facebook.com/v21.0/"
AUTH_URL = "https://www.facebook.com/v21.0/dialog/oauth"
COOKIE = "trafficlift_facebook_oauth"
CALLBACK = "/api/v1/facebook/callback"
# pages_show_list and pages_read_engagement let the owner pick and verify their
# own Page; pages_manage_posts is what lets TrafficLift publish Reels to it.
SCOPES = {"pages_show_list", "pages_read_engagement", "pages_manage_posts"}
MISSING = "Facebook publishing needs a connected Page."


def owner(request: Request):
    video_guard(request)
    if request.method == "POST" and request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Open Facebook posting in Owner Studio.")


def database():
    db = sqlite3.connect(campaign_api.root() / "facebook.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS reels(
        source TEXT, identifier TEXT, due REAL, state TEXT, title TEXT, description TEXT,
        video_path TEXT, video_id TEXT DEFAULT '', PRIMARY KEY(source,identifier))""")
    return db


def app_keys():
    """Facebook app ID and secret, kept in the deployment's private settings."""
    app_id, secret = os.getenv("FACEBOOK_APP_ID", ""), os.getenv("FACEBOOK_APP_SECRET", "")
    return (app_id, secret) if app_id and secret else None


def configured():
    return bool(app_keys()) and bool(os.getenv("TRAFFICLIFT_PUBLISHING_KEY"))


def page():
    """The connected owner's chosen Page: id, name and page access token."""
    return store.get("facebook", "owner")


def api(method, path, **kwargs):
    grant = page()
    if not grant:
        raise HTTPException(503, MISSING)
    params = kwargs.pop("params", {})
    params["access_token"] = grant["access_token"]
    response = requests.request(method, API + path, params=params, timeout=30,
                                allow_redirects=False, **kwargs)
    if response.status_code >= 400:
        log.warning("Facebook refused %s %s: %s %s", method, path.split("/")[-1],
                    response.status_code, response.text[:600])
    response.raise_for_status()
    return response.json()


@router.get("/status", dependencies=[Depends(owner)])
def status():
    grant = page()
    return {"configured": configured(), "connected": bool(grant),
            "page_name": grant.get("name") if grant else None,
            "choices": store.get("facebook", "pending_pages") or []}


@router.get("/connect", dependencies=[Depends(owner)])
def connect(request: Request):
    """Send the signed-in owner to Facebook's own permission screen."""
    keys = app_keys()
    if not keys:
        raise HTTPException(503, "Facebook connection is awaiting server setup.")
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    store.put("facebook", "pending", {"state": hashlib.sha256(state.encode()).hexdigest(),
              "browser": hashlib.sha256(browser.encode()).hexdigest(), "expires_at": time.time() + 600})
    url = AUTH_URL + "?" + urlencode({
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
    keys = app_keys()
    if not keys:
        raise HTTPException(503, "Facebook connection is awaiting server setup.")
    pending = store.pop("facebook", "pending")
    browser = request.cookies.get(COOKIE, "")
    if (not pending or not state or len(state) > 128 or not browser or pending["expires_at"] < time.time()
            or not secrets.compare_digest(pending["state"], hashlib.sha256(state.encode()).hexdigest())
            or not secrets.compare_digest(pending["browser"], hashlib.sha256(browser.encode()).hexdigest())):
        raise HTTPException(400, "Facebook connection request expired. Connect again from Owner Studio.")
    if error or not code or len(code) > 4096:
        raise HTTPException(400, "Facebook connection was declined. Connect again when ready.")
    redirect_uri = str(request.base_url).rstrip("/") + CALLBACK
    try:
        short = requests.get(API + "oauth/access_token", params={
            "client_id": keys[0], "client_secret": keys[1],
            "redirect_uri": redirect_uri, "code": code}, timeout=30, allow_redirects=False)
        short.raise_for_status()
        short_token = short.json()["access_token"]
        long = requests.get(API + "oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": keys[0], "client_secret": keys[1],
            "fb_exchange_token": short_token}, timeout=30, allow_redirects=False)
        long.raise_for_status()
        user_token = long.json()["access_token"]
        accounts = requests.get(API + "me/accounts", params={
            "access_token": user_token, "fields": "id,name,access_token"}, timeout=30, allow_redirects=False)
        accounts.raise_for_status()
        pages = [{"id": p["id"], "name": p["name"], "access_token": p["access_token"]}
                 for p in accounts.json().get("data", [])]
    except (requests.RequestException, KeyError, ValueError):
        raise HTTPException(502, "Facebook did not complete the connection. Connect again.")
    if not pages:
        raise HTTPException(409, "No Facebook Page was found for this account. "
                                  "You need to manage at least one Page to post Reels.")
    store.put("facebook", "pending_pages", pages)
    response = RedirectResponse("/owner?facebook=choose-page", status_code=303,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.delete_cookie(COOKIE, path=CALLBACK)
    return response


class PageSelection(BaseModel):
    page_id: str = Field(min_length=1, max_length=64)


@router.post("/page", dependencies=[Depends(owner)])
def select_page(selection: PageSelection):
    pages = store.get("facebook", "pending_pages") or []
    chosen = next((p for p in pages if p["id"] == selection.page_id), None)
    if not chosen:
        raise HTTPException(422, "Choose a Page returned by your Facebook account.")
    store.put("facebook", "owner", {"id": chosen["id"], "name": chosen["name"],
              "access_token": chosen["access_token"]})
    store.pop("facebook", "pending_pages")
    start()
    return {"connected": True, "page_name": chosen["name"]}


@router.post("/disconnect", dependencies=[Depends(owner)])
def disconnect():
    store.pop("facebook", "owner")
    store.pop("facebook", "pending_pages")
    return {"connected": False}


def queue(source, identifier, title, description, video_path, when=None):
    """Schedule one Reel. Re-queuing a failed post from the same source tries again."""
    with closing(database()) as db, db:
        db.execute("""INSERT INTO reels(source,identifier,due,state,title,description,video_path)
                      VALUES(?,?,?,'scheduled',?,?,?)
                      ON CONFLICT(source,identifier) DO UPDATE SET due=excluded.due, title=excluded.title,
                      description=excluded.description, video_path=excluded.video_path,
                      state=CASE WHEN reels.state IN ('failed','check feed') THEN 'scheduled' ELSE reels.state END""",
                   (source, identifier, when or time.time(), title, description, str(video_path)))


def status_for(source, identifier):
    with closing(database()) as db:
        row = db.execute("SELECT state FROM reels WHERE source=? AND identifier=?", (source, identifier)).fetchone()
    return row["state"] if row else None


def change(row, state, video_id=""):
    with closing(database()) as db, db:
        db.execute("UPDATE reels SET state=?,video_id=? WHERE source=? AND identifier=?",
                   (state, video_id, row["source"], row["identifier"]))


def publish(row):
    media = Path(row["video_path"])
    if not media.is_file():
        raise ValueError("Media changed")
    size = media.stat().st_size
    grant = page()
    if not grant:
        raise ValueError("No connected Page")
    started = api("POST", grant["id"] + "/video_reels", params={"upload_phase": "start"})
    video_id = started["video_id"]
    with media.open("rb") as stream:
        upload = requests.post(started["upload_url"], headers={
            "Authorization": "OAuth " + grant["access_token"],
            "offset": "0", "file_size": str(size)}, data=stream, timeout=180)
        upload.raise_for_status()
    change(row, "submitting")
    for _ in range(60):
        checked = api("GET", video_id, params={"fields": "status"})
        video_status = checked.get("status", {}).get("video_status")
        if video_status == "ready":
            break
        if video_status == "error":
            raise ValueError("Video failed")
        time.sleep(5)
    else:
        raise TimeoutError()
    finished = api("POST", grant["id"] + "/video_reels", params={
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
        "description": row["description"][:2000]})
    if not finished.get("success"):
        raise ValueError("Publish not confirmed")
    change(row, "published", video_id)


def tick():
    with closing(database()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM reels WHERE state='scheduled' AND due<=? ORDER BY due LIMIT 1",
                         (time.time(),)).fetchone()
        if row:
            db.execute("UPDATE reels SET state='uploading' WHERE source=? AND identifier=?",
                      (row["source"], row["identifier"]))
    if row:
        try:
            publish(row)
        except Exception as problem:
            log.warning("Facebook Reel was not confirmed: %s", type(problem).__name__)
            with closing(database()) as db, db:
                db.execute("""UPDATE reels SET state=CASE WHEN state='submitting' THEN 'check feed' ELSE 'failed' END
                              WHERE source=? AND identifier=? AND state!='published'""",
                          (row["source"], row["identifier"]))
    with closing(database()) as db, db:
        for item in db.execute("SELECT * FROM reels WHERE state='published'").fetchall():
            with closing(ad_results.database()) as analytics, analytics:
                ad_results.add_subject(analytics, ad_results.Subject(
                    platform="Facebook", scope="Post", label=item["title"],
                    reference="https://www.facebook.com/reel/" + item["video_id"]))
            db.execute("UPDATE reels SET state='published and tracked' WHERE source=? AND identifier=?",
                      (item["source"], item["identifier"]))


def run():
    while not stop.wait(10):
        try:
            tick()
        except Exception:
            continue


@router.on_event("startup")
def start():
    global worker
    if app_keys() and (not worker or not worker.is_alive()):
        with closing(database()) as db, db:
            db.execute("UPDATE reels SET state='scheduled' WHERE state='uploading'")
            db.execute("UPDATE reels SET state='check feed' WHERE state='submitting'")
        stop.clear()
        worker = threading.Thread(target=run, daemon=True)
        worker.start()


@router.on_event("shutdown")
def shutdown():
    stop.set()
