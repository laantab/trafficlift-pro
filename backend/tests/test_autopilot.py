import json
import zipfile
from datetime import datetime, timezone

from backend import autopilot, campaign_api, campaign_editor, campaigns, facebook_publisher as fb, pinterest_queue as pins, viral_hooks


def ready(job):
    """Stand in for a rendered, editor-approved campaign."""
    folder = campaign_api.root()/job
    (folder/"video.mp4").write_bytes(b"approved video")
    with zipfile.ZipFile(folder/"campaign.zip", "w") as archive:
        archive.writestr("cover-frame.jpg", b"cover")
        archive.writestr("facebook-reels.txt", "A memory for Christmas.\n"+campaign_api.HOLIDAY["destination"])
    campaign_editor.receipt(folder, 10, 4)
    with campaign_api.database() as db:
        db.execute("UPDATE jobs SET status='ready' WHERE id=?", (job,))


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR", str(tmp_path))
    monkeypatch.delenv("TRAFFICLIFT_HOSTED_VIDEO", raising=False)
    monkeypatch.setattr(autopilot, "start", lambda: None)
    monkeypatch.setattr(pins, "boards", lambda: [{"id": "123", "name": "Books"}])
    monkeypatch.setattr(campaign_api, "build", lambda *a, **k: campaign_api.render_lock.release())


def test_one_video_a_day_then_pause_when_editor_holds(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    early = datetime(2026, 11, 26, 16, 30, tzinfo=timezone.utc)   # 8:30 AM Pacific
    rows = autopilot.save(autopilot.Plan(product="holiday", board="123", time="09:00", zone="America/Los_Angeles", enabled=True), now=early)
    assert rows[0]["enabled"] and rows[0]["board_name"] == "Books" and rows[0]["time"] == "09:00"
    due = datetime(2026, 11, 26, 17, 1, tzinfo=timezone.utc)      # 9:01 AM Pacific
    autopilot.tick(early)
    with campaign_api.database() as db:
        assert not db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    autopilot.tick(due)
    autopilot.tick(due)   # still rendering: no second video
    with campaign_api.database() as db:
        jobs = [r[0] for r in db.execute("SELECT id FROM jobs")]
    assert len(jobs) == 1
    ready(jobs[0])
    monkeypatch.setattr(pins.time, "time", lambda: due.timestamp())
    autopilot.tick(due)
    with pins.database() as db:
        queued = db.execute("SELECT campaign,board,state FROM pins").fetchall()
    assert [tuple(r) for r in queued] == [(jobs[0], "123", "scheduled")]
    autopilot.tick(datetime(2026, 11, 26, 23, 0, tzinfo=timezone.utc))   # same day: nothing new
    with campaign_api.database() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    autopilot.tick(datetime(2026, 11, 27, 17, 1, tzinfo=timezone.utc))   # next day
    with campaign_api.database() as db:
        second = [r[0] for r in db.execute("SELECT id FROM jobs") if r[0] != jobs[0]]
        db.execute("UPDATE jobs SET status='held' WHERE id=?", (second[0],))
    autopilot.tick(datetime(2026, 11, 27, 17, 2, tzinfo=timezone.utc))
    plan = autopilot.plans()[0]
    assert not plan["enabled"] and "video editor" in plan["note"]
    with pins.database() as db:
        assert db.execute("SELECT COUNT(*) FROM pins").fetchone()[0] == 1


def test_autopilot_reuses_oldest_opening_but_manual_does_not(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    from uuid import uuid4
    import pytest
    from fastapi import HTTPException
    for _ in range(3):
        campaign_api.launch("holiday", uuid4().hex)
        with campaign_api.database() as db:
            db.execute("UPDATE jobs SET status='ready'")
    with pytest.raises(HTTPException):
        campaign_api.launch("holiday", uuid4().hex)
    campaign_api.launch("holiday", "f"*32, rotate=True)
    with campaign_api.database() as db:
        assert db.execute("SELECT variant FROM jobs WHERE id=?", ("f"*32,)).fetchone()[0] == 0


def test_openings_fit_the_editor_and_ai_hooks_are_screened(monkeypatch):
    product = {"name": "Comic Book Creator", "destination": "https://keepitrealnow.com/#comic-book-creator",
               "feature": "A personalized comic book starring your child", "prompt": "", "affiliate": False}
    assert len(set(campaigns.PRODUCT_HOOKS)) == len(campaigns.PRODUCT_HOOKS) >= 12
    for index in range(len(campaigns.PRODUCT_HOOKS)):
        lines = campaigns.plan(product, index)
        campaign_editor.check_copy(product, lines, campaigns.posts(product, lines))
        short = campaigns.shorts_package(product, lines)
        assert len(short["title"]) <= 60 and 1 <= len(short["cover_text"].split()) <= 4
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert viral_hooks.write(product) is None
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(viral_hooks, "ask", lambda prompt: json.dumps({"hook": "What if your kid was the hero?", "cover": "Meet the hero"}))
    assert viral_hooks.write(product) == {"hook": "What if your kid was the hero?", "cover": "Meet the hero"}
    assert viral_hooks.write(product, ["what if your kid was the hero?"]) is None
    assert campaigns.shorts_package({**product, "cover_text": "Meet the hero"}, ["What if your kid was the hero?", "a", "b", "c"])["cover_text"] == "Meet the hero"
    for bad in ["Guaranteed to be the best gift", "Save 50% today on this", "See https://example.com now"]:
        monkeypatch.setattr(viral_hooks, "ask", lambda prompt, bad=bad: json.dumps({"hook": bad, "cover": "Look closer"}))
        assert viral_hooks.write(product) is None


def test_new_openings_run_before_the_original_three(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    from uuid import uuid4
    key = "a"*32
    (campaign_api.root()/key).mkdir()
    (campaign_api.root()/key/"0.jpg").write_bytes(b"photo")
    with campaign_api.database() as db:
        db.execute("INSERT INTO products VALUES(?,?)", (key, json.dumps({"name": "Comic Book Creator",
                   "destination": "https://keepitrealnow.com/", "feature": "A comic", "prompt": "", "affiliate": False})))
        db.execute("INSERT INTO jobs VALUES(?,?,?,?)", ("b"*32, key, 0, "ready"))
    job = uuid4().hex
    campaign_api.launch(key, job)
    with campaign_api.database() as db:
        assert db.execute("SELECT variant FROM jobs WHERE id=?", (job,)).fetchone()[0] == 3


def test_youtube_only_autopilot_starts_tomorrow_and_queues_a_short(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    from backend import publishing_api, youtube_publisher
    monkeypatch.setattr(youtube_publisher, "credentials", lambda grant_id=None: {"channel_id": "UC1"})
    sent = []
    monkeypatch.setattr(publishing_api, "queue_campaign_short", lambda job, choice: sent.append((job.hex, choice)))
    monkeypatch.setattr(publishing_api, "campaign_job", lambda job: None)
    after_nine = datetime(2026, 11, 26, 18, 30, tzinfo=timezone.utc)   # 10:30 AM Pacific
    plan = autopilot.Plan(product="holiday", time="09:00", zone="America/Los_Angeles", enabled=True,
                          pinterest=False, youtube=True)
    row = autopilot.save(plan, now=after_nine)[0]
    assert row["youtube"] and not row["pinterest"] and row["board"] == ""
    autopilot.tick(after_nine)                                    # turned on after 9:00: nothing today
    with campaign_api.database() as db:
        assert not db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    tomorrow = datetime(2026, 11, 27, 17, 1, tzinfo=timezone.utc)
    autopilot.tick(tomorrow)
    with campaign_api.database() as db:
        job = db.execute("SELECT id FROM jobs").fetchone()[0]
    ready(job)
    autopilot.tick(tomorrow)
    assert [j for j, _ in sent] == [job]
    choice = sent[0][1]
    assert choice.visibility == "public" and choice.made_for_kids is False and choice.synthetic_media is False
    with pins.database() as db:
        assert not db.execute("SELECT COUNT(*) FROM pins").fetchone()[0]
    assert "YouTube Shorts" in autopilot.plans()[0]["note"]
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        autopilot.save(autopilot.Plan(product="holiday", time="09:00", zone="America/Los_Angeles", enabled=True,
                                      pinterest=False, youtube=False))


def test_facebook_only_autopilot_queues_a_reel_with_the_campaign_caption(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    from cryptography.fernet import Fernet
    monkeypatch.setenv("TRAFFICLIFT_PUBLISHING_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(fb, "page", lambda: {"id": "999", "name": "Intelligent innovation solutions",
                        "access_token": "page-token"})
    after_nine = datetime(2026, 11, 26, 18, 30, tzinfo=timezone.utc)   # 10:30 AM Pacific
    plan = autopilot.Plan(product="holiday", time="09:00", zone="America/Los_Angeles", enabled=True,
                          pinterest=False, youtube=False, facebook=True)
    row = autopilot.save(plan, now=after_nine)[0]
    assert row["facebook"] and not row["pinterest"] and not row["youtube"]
    autopilot.tick(after_nine)                                    # turned on after 9:00: nothing today
    with campaign_api.database() as db:
        assert not db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    tomorrow = datetime(2026, 11, 27, 17, 1, tzinfo=timezone.utc)
    autopilot.tick(tomorrow)
    with campaign_api.database() as db:
        job = db.execute("SELECT id FROM jobs").fetchone()[0]
    ready(job)
    autopilot.tick(tomorrow)
    with fb.database() as db:
        row = db.execute("SELECT state,title,description FROM reels WHERE source='campaign' AND identifier=?",
                         (job,)).fetchone()
    assert row["state"] == "scheduled" and row["title"] == campaign_api.HOLIDAY["name"]
    assert row["description"].startswith("A memory for Christmas.")
    with pins.database() as db:
        assert not db.execute("SELECT COUNT(*) FROM pins").fetchone()[0]
    assert "Facebook" in autopilot.plans()[0]["note"]


def test_facebook_plan_needs_a_connected_page(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    monkeypatch.setattr(fb, "page", lambda: None)
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        autopilot.save(autopilot.Plan(product="holiday", time="09:00", zone="America/Los_Angeles", enabled=True,
                                      pinterest=False, youtube=False, facebook=True))
