"""Plan Shorts go to the tips channel through the shared queue. No network."""
import json
import time
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from backend import growth_api, growth_plan as growth, growth_youtube
from backend import publishing_api as api
from backend import publishing_store as store
from backend import youtube_publisher as youtube
from backend.campaign_editor import digest

MAIN, TIPS = "UC" + "a" * 22, "UC" + "b" * 22


@pytest.fixture
def site(monkeypatch, tmp_path):
    for name, value in {"TRAFFICLIFT_VIDEO_DIR": str(tmp_path), "TRAFFICLIFT_PUBLISHING_KEY": Fernet.generate_key().decode(),
                        "TRAFFICLIFT_YOUTUBE_ENABLED": "1", "YOUTUBE_CLIENT_ID": "id", "YOUTUBE_CLIENT_SECRET": "secret",
                        "YOUTUBE_REDIRECT_URI": "https://trafficlift.test/api/v1/publishing/youtube/callback"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("TRAFFICLIFT_HOSTED_VIDEO", raising=False)
    monkeypatch.setattr(growth_api.studio, "storage", lambda: tmp_path)
    monkeypatch.setattr(api, "inspect_media", lambda path: {"width": 1080, "height": 1920, "duration": 15.0})
    monkeypatch.setattr(growth_youtube.studio, "video_guard", lambda request: None)
    monkeypatch.setattr(api, "guard", lambda request, mutation=False: None)
    monkeypatch.setattr(growth_youtube, "numbers", lambda found, ids: {"subscribers": 12, "videos": {}})
    grant = lambda name, channel, title: store.put("youtube", name, {
        "grant_id": name + "-grant", "access_token": "a", "refresh_token": "r", "expires_at": time.time() + 3600,
        "channel_id": channel, "channels": [{"id": channel, "title": title}]})
    grant("owner", MAIN, "Lonnie Brown")
    return grant


def video(day, body=b"plan video"):
    job = uuid.uuid4().hex
    folder = growth_api.root() / job
    folder.mkdir()
    (folder / "video.mp4").write_bytes(body)
    (folder / "editorial.json").write_text(json.dumps({"version": 1, "video": digest(folder / "video.mp4")}))
    with growth_api.database() as db:
        db.execute("INSERT INTO videos VALUES (?,?,?,?,?)", (job, day, "ready", "[]", "2026-11-27T09:00:00"))
    return job


def test_nothing_posts_until_the_tips_channel_is_connected_and_auto_is_on(site):
    first = video(1)
    growth_youtube.after_video(1, first)                      # no tips channel yet
    assert store.jobs() == [] and growth_youtube.read(None)["ready"] is False
    with pytest.raises(HTTPException):
        growth_youtube.post(1, first)
    site("tips", TIPS, "4your Good Health")
    growth_youtube.after_video(1, first)                      # connected, but auto is still off
    assert store.jobs() == []
    growth_youtube.save(growth_youtube.Auto(auto=True), None)
    growth_youtube.after_video(1, first)
    jobs = store.jobs()
    assert len(jobs) == 1 and jobs[0]["state"] == "queued"
    snapshot = jobs[0]["snapshot"]
    assert snapshot["channel_id"] == TIPS and snapshot["grant_id"] == "tips-grant" and snapshot["source"] == "growth"
    assert snapshot["visibility"] == "public" and snapshot["made_for_kids"] is False
    assert snapshot["title"] == growth.entry(1)["topic"] and "#shorts" in snapshot["description"]
    assert growth.stock_entry(1)["caption"] in snapshot["description"]
    growth_youtube.after_video(1, video(1, b"a remake"))     # one Short per plan day
    assert len(store.jobs()) == 1
    page = growth_youtube.read(None)
    assert page["ready"] and page["auto"] and page["channel"] == "4your Good Health" and page["subscribers"] == 12
    assert [(s["day"], s["state"]) for s in page["shorts"]] == [(1, "queued")]
    # The queued upload finds the tips connection by its grant, never the main channel's.
    assert youtube.credentials("tips-grant")["channel_id"] == TIPS
    assert youtube.credentials()["channel_id"] == MAIN


def test_owner_can_post_a_day_by_hand_and_unchecked_videos_are_refused(site):
    site("tips", TIPS, "4your Good Health")
    with pytest.raises(HTTPException):
        growth_youtube.post_now(growth_youtube.Day(day=2), None)          # no video yet
    second = video(2)
    (growth_api.root() / second / "video.mp4").write_bytes(b"tampered")
    with pytest.raises(HTTPException):
        growth_youtube.post_now(growth_youtube.Day(day=2), None)          # fails its receipt
    assert store.jobs() == []
    third = video(3)
    assert growth_youtube.post_now(growth_youtube.Day(day=3), None)["state"] == "queued"
    with pytest.raises(HTTPException):
        growth_youtube.post_now(growth_youtube.Day(day=3), None)          # already on its way


def test_each_connection_keeps_its_own_channel(site, monkeypatch):
    site("tips", TIPS, "4your Good Health")
    monkeypatch.setattr(youtube, "channels", lambda connection: [{"id": MAIN, "title": "Lonnie Brown"}])
    with pytest.raises(HTTPException) as refused:
        api.select_channel(api.ChannelSelection(channel_id=MAIN, slot="tips"), None)
    assert "already used" in refused.value.detail
    with pytest.raises(HTTPException):
        api.select_channel(api.ChannelSelection(channel_id=MAIN, slot="elsewhere"), None)
    monkeypatch.setattr(youtube, "call", lambda *a, **k: type("R", (), {"status_code": 200})())
    api.disconnect(None, slot="tips")
    assert store.get("youtube", "tips") is None and store.get("youtube", "owner")["channel_id"] == MAIN


def test_a_video_made_before_auto_was_on_still_goes_out_on_its_day(site, monkeypatch):
    import datetime
    site("tips", TIPS, "4your Good Health")
    monkeypatch.setattr(growth_api, "today", lambda: datetime.date(2026, 11, 27))
    with growth_api.database() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES ('start','2026-11-26')")
    video(2)                                   # tomorrow's video, made early
    growth_youtube.catch_up()                  # auto is off: nothing
    assert store.jobs() == []
    growth_youtube.save(growth_youtube.Auto(auto=True), None)
    growth_youtube.catch_up()
    growth_youtube.catch_up()                  # and only once
    jobs = store.jobs()
    assert len(jobs) == 1 and jobs[0]["snapshot"]["title"] == growth.entry(2)["topic"]
