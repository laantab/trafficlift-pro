"""Revocable trusted-browser access. The cookie is scoped to the owner entrance."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import closing

from fastapi import HTTPException

COOKIE = "trafficlift_owner_device"
PATH = "/api/v1/owner-device"
LIFETIME = 90 * 86400


def fingerprint(token, secret):
    return hmac.new(secret.encode(), b"owner-device:" + token.encode(), hashlib.sha256).hexdigest()


def record(directory, operation, token, secret):
    directory.mkdir(parents=True, exist_ok=True)
    key = fingerprint(token, secret)
    now = int(time.time())
    with closing(sqlite3.connect(directory/"owner-access.sqlite3", timeout=5)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS devices (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL)")
        db.execute("DELETE FROM devices WHERE expires<=?", (now,))
        if operation == "register":
            db.execute("INSERT INTO devices VALUES (?,?)", (key, now+LIFETIME))
        elif operation == "forget":
            db.execute("DELETE FROM devices WHERE digest=?", (key,))
        else:
            return db.execute("SELECT 1 FROM devices WHERE digest=?", (key,)).fetchone() is not None


def remember(directory, response, secret):
    token = secrets.token_urlsafe(32)
    record(directory, "register", token, secret)
    response.set_cookie(COOKIE, token, max_age=LIFETIME, path=PATH, secure=True,
                        httponly=True, samesite="strict")


def check_origin(request):
    if request.headers.get("origin","").rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Open the owner entrance on the TrafficLift website.")


def token_from(request):
    token = request.cookies.get(COOKIE,"")
    return token if re.fullmatch(r"[A-Za-z0-9_-]{43}",token) else None
