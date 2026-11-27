"""Plan Reels go to the connected Facebook Page through the shared queue. No network."""
import datetime
import json
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from backend import facebook_publisher as fb, growth_api, growth_facebook, growth_plan as growth
from backend.campaign_editor import digest


@pytest.fixture
def site(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR", str(tmp_path))
    monkeypatch.setenv("TRAFFICLIFT_PUBLISHING_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(growth_api.studio, "storage", lambda: tmp_path)
    monkeypatch.setattr(growth_facebook.studio, "video_guard", lambda request: None)

    def connect():
        fb.store.put("facebook", "owner", {"id": "999", "name": "Intelligent innovation solutions",
                     "access_token": "page-token"})
    return connect


def video(day, body=b"plan video"):
    job = uuid.uuid4().hex
    folder = growth_api.root() / job
    folder.mkdir()
    (folder / "video.mp4").write_bytes(body)
    (folder / "editorial.json").write_text(json.dumps({"version": 1, "video": digest(folder / "video.mp4")}))
    with growth_api.database() as db:
        db.execute("INSERT INTO videos VALUES (?,?,?,?,?)", (job, day, "ready", "[]", "2026-11-27T09:00:00"))
    return job


def test_nothing_posts_until_the_page_is_connected_and_auto_is_on(site):
    first = video(1)
    growth_facebook.after_video(1, first)                       # no Page yet
    assert fb.status_for("growth", first) is None and growth_facebook.read(None)["connected"] is False
    with pytest.raises(HTTPException):
        growth_facebook.post(1, first)
    site()
    growth_facebook.after_video(1, first)                       # connected, but auto is still off
    assert fb.status_for("growth", first) is None
    growth_facebook.save(growth_facebook.Auto(auto=True), None)
    growth_facebook.after_video(1, first)
    assert fb.status_for("growth", first) == "scheduled"
    growth_facebook.after_video(1, video(1, b"a remake"))       # one Reel per plan day
    with fb.database() as db:
        assert db.execute("SELECT COUNT(*) FROM reels WHERE source='growth'").fetchone()[0] == 1
    page = growth_facebook.read(None)
    assert page["connected"] and page["auto"] and page["page_name"] == "Intelligent innovation solutions"
    assert [(r["day"], r["video"], r["state"]) for r in page["reels"]] == [(1, first, "scheduled")]


def test_owner_can_post_a_day_by_hand_and_unchecked_videos_are_refused(site):
    site()
    with pytest.raises(HTTPException):
        growth_facebook.post_now(growth_facebook.Day(day=2), None)          # no video yet
    second = video(2)
    (growth_api.root() / second / "video.mp4").write_bytes(b"tampered")
    with pytest.raises(HTTPException):
        growth_facebook.post_now(growth_facebook.Day(day=2), None)          # fails its receipt
    with fb.database() as db:
        assert db.execute("SELECT COUNT(*) FROM reels").fetchone()[0] == 0
    third = video(3)
    growth_facebook.post_now(growth_facebook.Day(day=3), None)
    assert fb.status_for("growth", third) == "scheduled"
    with pytest.raises(HTTPException):
        growth_facebook.post_now(growth_facebook.Day(day=3), None)          # already on its way


def test_a_video_made_before_auto_was_on_still_goes_out_on_its_day(site, monkeypatch):
    site()
    monkeypatch.setattr(growth_api, "today", lambda: datetime.date(2026, 11, 27))
    with growth_api.database() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES ('start','2026-11-26')")
    second = video(2)                           # tomorrow's video, made early
    growth_facebook.catch_up()                  # auto is off: nothing
    assert fb.status_for("growth", second) is None
    growth_facebook.save(growth_facebook.Auto(auto=True), None)
    growth_facebook.catch_up()
    growth_facebook.catch_up()                  # and only once
    with fb.database() as db:
        assert db.execute("SELECT COUNT(*) FROM reels").fetchone()[0] == 1
    assert fb.status_for("growth", second) == "scheduled"
