"""30-day TikTok growth plan: content mix, script checks, tracking and the free video path."""
import base64
import io
import time
import uuid
from collections import Counter
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import trafficlift_pro as server
from backend import growth_api, growth_plan as growth
from backend.campaign_editor import check_copy
from backend.campaigns import HOLIDAY, plan as product_plan, posts, shorts_package


def test_thirty_days_follow_the_requested_mix():
    plan = growth.plan()
    assert [p["day"] for p in plan] == list(range(1, 31))
    assert Counter(p["category"] for p in plan) == {"gadgets": 11, "kitchen": 7, "health": 6, "money": 3, "books": 3}
    assert {p["format"] for p in plan} == set(growth.FORMATS)
    assert len({p["topic"] for p in plan}) == 30 and len({p["hook"] for p in plan}) == 30


def test_every_script_passes_its_checks_and_the_existing_editor():
    for item in growth.plan():
        assert growth.ready(item), (item["day"], [c for c in growth.review(item) if not c["ok"]])
        assert len(item["scenes"]) == 4 and all(s["say"] and s["show"] and s["text"] for s in item["scenes"])
        lines = [s["say"] for s in item["scenes"]]
        product = {"name": growth.ACCOUNT, "destination": growth.PROFILE, "feature": item["topic"],
                   "prompt": "", "affiliate": False, "cover_text": item["cover_text"]}
        check_copy(product, lines, posts(product, lines))
        assert shorts_package(product, lines)["cover_text"] == item["cover_text"]


def test_existing_product_campaigns_keep_their_opening_text():
    assert shorts_package(HOLIDAY, product_plan(HOLIDAY, 0, True))["cover_text"] == "That one person"
    saved = {"name": "Orbit Lamp", "destination": "https://seller.example/lamp", "feature": "An adjustable arm",
             "prompt": "", "affiliate": False}
    assert shorts_package(saved, product_plan(saved, 0))["cover_text"] == "What's inside?"


@pytest.mark.parametrize("line,check", [
    ("This cures back pain. Follow 4your Good Health.", "No unsupported health or results claims"),
    ("You will lose 20 pounds. Follow 4your Good Health.", "No unsupported health or results claims"),
    ("This video will go viral. Follow 4your Good Health.", "No unsupported health or results claims"),
    ("Comment YES to get the link. Follow 4your Good Health.", "Ending invites, without bait"),
    ("Tag 3 friends and follow 4your Good Health.", "Ending invites, without bait"),
    ("That is the whole trick from 4your Good Health.", "Ending invites, without bait"),
    ("Follow me for more kitchen hacks.", "Ending names your account"),
])
def test_unsafe_or_pushy_endings_are_flagged(line, check):
    item = growth.entry(2)
    lines = [s["say"] for s in item["scenes"][:3]] + [line]
    failed = {c["check"] for c in growth.review(item, lines) if not c["ok"]}
    assert check in failed


def test_off_topic_script_and_unlabeled_weight_loss_are_flagged():
    item = growth.entry(2)
    failed = {c["check"] for c in growth.review(item, ["Look at this.", "It is nice.", "I like it.",
                                                        "Follow 4your Good Health for more."]) if not c["ok"]}
    assert "Script matches the topic" in failed
    story = growth.entry(24)
    lines = [s["say"] for s in story["scenes"]]
    lines[1] = "I lost 25 pounds with routines like the ones in it."
    assert "Personal results are labeled" in {c["check"] for c in growth.review(story, lines) if not c["ok"]}


def test_advice_never_promises_and_names_what_to_repeat():
    start = growth.advice([], None, 1)
    assert start["remaining"] == 945 and start["pace_needed"] == 32
    assert "guarantee" in start["summary"][0]
    posts_logged = [
        {"category": "gadgets", "format": "demo", "views": 2400, "follower_change": 30, "retention": 45, "saves": 40, "shares": 20, "profile_visits": 90},
        {"category": "gadgets", "format": "demo", "views": 1800, "follower_change": 22, "retention": 41, "saves": 30, "shares": 12, "profile_visits": 70},
        {"category": "health", "format": "quick_tip", "views": 300, "follower_change": 1, "retention": 20, "saves": 1, "shares": 0, "profile_visits": 8},
    ]
    result = growth.advice(posts_logged, None, 4)
    assert result["followers"] == 55 + 53
    assert any("Amazing gadgets" in line for line in result["repeat"])
    assert any("Healthy lifestyle" in line for line in result["adjust"])
    assert not any(word in " ".join(result["repeat"] + result["adjust"] + result["summary"]).lower()
                   for word in ("will go viral", "you will reach", "guaranteed"))


def photo():
    buffer = io.BytesIO()
    Image.new("RGB", (640, 800), "#3a7").save(buffer, "JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def owner(monkeypatch, tmp_path):
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.delenv("TRAFFICLIFT_HOSTED_VIDEO", raising=False)
    monkeypatch.setenv("TRAFFICLIFT_FREE_VIDEO", "1")
    monkeypatch.setattr(growth_api.studio, "storage", lambda: tmp_path)
    return TestClient(server.app)


def test_plan_page_and_data_are_owner_only_on_the_hosted_site(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN", "s" * 40)
    monkeypatch.setattr(growth_api.studio, "storage", lambda: tmp_path)
    client = TestClient(server.app, base_url="https://trafficlift-pro.onrender.com")
    assert client.get("/growth").status_code == 200
    assert client.get("/api/v1/growth").status_code == 401
    assert client.post("/api/v1/growth/posts", json={"posted": "2026-11-27", "topic": "x y"}).status_code == 401
    assert client.post("/api/v1/growth/videos", json={"day": 1, "lines": ["a"] * 4, "images": [photo()],
                                                      "request_id": str(uuid.uuid4())}).status_code == 401


def test_overview_tracks_followers_and_logged_posts(owner):
    data = owner.get("/api/v1/growth").json()
    assert data["account"] == "4your Good Health" and data["day"] == 1 and len(data["plan"]) == 30
    assert data["advice"]["followers"] == 55 and all(p["ready"] for p in data["plan"])
    data = owner.post("/api/v1/growth/settings", json={"followers": 80}).json()
    assert data["advice"]["remaining"] == 920
    item = data["plan"][0]
    data = owner.post("/api/v1/growth/posts", json={
        "posted": "2026-11-27", "day": 1, "topic": item["topic"], "category": item["category"], "format": item["format"],
        "views": 900, "watch_seconds": 6.5, "retention": 38, "likes": 40, "comments": 3, "shares": 2, "saves": 9,
        "profile_visits": 30, "follower_change": 12}).json()
    assert data["plan"][0]["posted"] and data["posts"][0]["views"] == 900 and data["posts"][0]["follower_change"] == 12
    assert owner.post("/api/v1/growth/posts", json={"posted": "2026-11-27", "topic": "Other", "category": "nope"}).status_code == 422
    data = owner.delete(f"/api/v1/growth/posts/{data['posts'][0]['id']}").json()
    assert data["posts"] == [] and not data["plan"][0]["posted"]


def test_edited_script_is_rechecked_before_any_render(owner):
    item = growth.entry(1)
    lines = [s["say"] for s in item["scenes"]]
    assert owner.post("/api/v1/growth/check", json={"day": 1, "lines": lines}).json()["ready"]
    bad = lines[:3] + ["This cures sore hands. Follow 4your Good Health."]
    assert not owner.post("/api/v1/growth/check", json={"day": 1, "lines": bad}).json()["ready"]
    with patch.object(growth_api, "render_campaign") as render:
        reply = owner.post("/api/v1/growth/videos", json={"day": 1, "lines": bad, "images": [photo()],
                                                          "request_id": str(uuid.uuid4())})
    assert reply.status_code == 422 and "health" in reply.json()["detail"]
    render.assert_not_called()


def test_video_uses_the_free_renderer_with_the_owners_photos_and_script(owner, tmp_path):
    item = growth.entry(1)
    lines = [s["say"] for s in item["scenes"]]
    seen = {}

    def fake_render(directory, product, images, spoken):
        seen.update(product=product, images=[i.name for i in images], lines=spoken)
        (directory / "video.mp4").write_bytes(b"mp4")
        (directory / "campaign.zip").write_bytes(b"zip")
        from backend.campaign_editor import digest
        import json
        (directory / "editorial.json").write_text(json.dumps(
            {"version": 1, "video": digest(directory / "video.mp4"), "kit": digest(directory / "campaign.zip")}))

    request_id = str(uuid.uuid4())
    body = {"day": 1, "lines": lines, "images": [photo(), photo()], "request_id": request_id}
    with patch.object(growth_api, "render_campaign", side_effect=fake_render):
        job = owner.post("/api/v1/growth/videos", json=body)
        assert job.status_code == 202
        for _ in range(100):
            state = owner.get("/api/v1/growth/videos/" + job.json()["id"]).json()
            if state["status"] != "rendering":
                break
            time.sleep(.05)
        assert owner.post("/api/v1/growth/videos", json=body).json() == job.json()
    assert state == {"status": "ready", "day": 1, "lines": lines}
    assert seen["lines"] == lines and seen["images"] == ["photo0.jpg", "photo1.jpg", "photo1.jpg", "photo1.jpg"]
    assert seen["product"]["name"] == "4your Good Health" and seen["product"]["affiliate"] is False
    assert seen["product"]["destination"] == "https://www.tiktok.com/@hammer2085"
    video = owner.get(f"/api/v1/growth/videos/{job.json()['id']}/video?save=1")
    assert video.content == b"mp4" and "4your-Good-Health-Day-01.mp4" in video.headers["content-disposition"]
    assert owner.get("/api/v1/growth").json()["plan"][0]["video"] == job.json()["id"]
    assert not growth_api.render_lock.locked()
    (tmp_path / "growth" / job.json()["id"] / "video.mp4").write_bytes(b"tampered")
    assert owner.get("/api/v1/growth/videos/" + job.json()["id"]).json()["status"] == "held"


def test_no_paid_provider_or_publishing_code_in_the_growth_feature():
    import inspect
    source = inspect.getsource(growth_api) + inspect.getsource(growth)
    for word in ("heygen", "krea", "fal_client", "minimax", "openai", "youtube_publisher", "pinterest", "requests.", "httpx"):
        assert word not in source.lower()


def test_stock_scripts_pass_every_check_and_make_no_personal_claims():
    import re
    from backend import campaign_editor, campaigns
    assert sorted(set(range(1, 31)) - set(growth._STOCK)) == [5, 14, 24]      # the owner's own books
    for day in growth._STOCK:
        item = growth.stock_entry(day)
        lines = [scene["say"] for scene in item["scenes"]]
        assert growth.ready(item), (day, [c for c in growth.review(item) if not c["ok"]])
        assert len(item["searches"]) == 4 and all(item["searches"])
        for text in lines + [item["caption"]]:
            claim = re.sub(r"Tell me|If you use my link, I earn a commission\.", "", text)
            assert not re.search(r"\b(?:I|I'm|I'll|I'd|my|me|mine)\b", claim), (day, text)
        product = {"name": growth.ACCOUNT, "destination": growth.PROFILE, "feature": item["topic"],
                   "prompt": "", "affiliate": False}
        campaign_editor.check_copy(product, lines, campaigns.posts(product, lines))
    assert growth.stock_entry(5) is None


def test_stock_video_is_built_from_found_footage_and_keeps_its_credits(owner, tmp_path, monkeypatch):
    import json
    from backend import stock_media
    from backend.campaign_editor import digest
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    body = {"day": 1, "lines": [s["say"] for s in growth.stock_entry(1)["scenes"]], "request_id": str(uuid.uuid4())}
    plan = owner.get("/api/v1/growth").json()
    assert plan["stock_ready"] is False and plan["plan"][0]["stock"] and not plan["plan"][4]["stock"]
    assert plan["plan"][0]["scenes"][0]["say"] == "Tight jar lid that will not budge?"
    assert "searches" not in plan["plan"][0]
    assert owner.post("/api/v1/growth/stock-videos", json=body).status_code == 503
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    assert owner.post("/api/v1/growth/stock-videos", json={**body, "day": 5}).status_code == 422
    seen = {}

    def fake_collect(queries, folder):
        seen["queries"] = queries
        files = []
        for i in range(4):
            (folder / f"stock{i}.mp4").write_bytes(b"clip")
            files.append(folder / f"stock{i}.mp4")
        return files, [{"scene": i + 1, "kind": "video", "source": "Pexels", "creator": "A", "page": ""} for i in range(4)]

    def fake_render(directory, product, media, spoken):
        seen.update(product=product, media=[m.name for m in media], lines=spoken)
        (directory / "video.mp4").write_bytes(b"mp4")
        (directory / "editorial.json").write_text(json.dumps({"version": 1, "video": digest(directory / "video.mp4")}))

    with patch.object(stock_media, "collect", side_effect=fake_collect), \
            patch.object(growth_api, "render_stock_video", side_effect=fake_render):
        job = owner.post("/api/v1/growth/stock-videos", json=body)
        assert job.status_code == 202
        for _ in range(100):
            state = owner.get("/api/v1/growth/videos/" + job.json()["id"]).json()
            if state["status"] != "rendering":
                break
            time.sleep(.05)
    assert state["status"] == "ready" and len(state["credits"]) == 4 and state["credits"][0]["source"] == "Pexels"
    assert seen["queries"] == growth.stock_entry(1)["searches"] and seen["lines"] == body["lines"]
    assert seen["product"]["cover_text"] == "Stuck lid?" and seen["media"] == [f"stock{i}.mp4" for i in range(4)]
    folder = tmp_path / "growth" / job.json()["id"]
    assert not list(folder.glob("stock*")) and not growth_api.render_lock.locked()
    assert owner.get(f"/api/v1/growth/videos/{job.json()['id']}/video").content == b"mp4"


def test_stock_search_prefers_clips_and_only_contacts_the_stock_libraries(monkeypatch, tmp_path):
    from backend import stock_media
    monkeypatch.setenv("PEXELS_API_KEY", "k1")
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    assert stock_media.allowed("https://videos.pexels.com/video-files/1/a.mp4")
    assert stock_media.allowed("https://cdn.pixabay.com/video/a.mp4")
    for bad in ("http://videos.pexels.com/a.mp4", "https://pexels.com.evil.example/a.mp4",
                "https://user@pexels.com/a.mp4", "https://169.254.169.254/latest", "https://localhost/a"):
        assert not stock_media.allowed(bad)
    calls = []

    class Reply:
        status_code, is_redirect, headers = 200, False, {}
        def __init__(self, data=None, body=b""): self.data, self.body = data, body
        def json(self): return self.data
        def raise_for_status(self): pass
        def iter_content(self, size): yield self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_get(url, **kwargs):
        calls.append(url)
        if "videos/search" in url:
            same = {"id": 7, "duration": 9, "url": "https://www.pexels.com/video/7", "user": {"name": "Ann"},
                    "video_files": [{"link": "https://videos.pexels.com/video-files/7/small.mp4", "width": 540, "height": 960, "file_type": "video/mp4"},
                                    {"link": "https://videos.pexels.com/video-files/7/hd.mp4", "width": 1080, "height": 1920, "file_type": "video/mp4"}]}
            return Reply({"videos": [same]})
        if url.endswith("/v1/search"):
            return Reply({"photos": [{"id": 3, "photographer": "Bo", "url": "https://www.pexels.com/photo/3",
                                      "src": {"large2x": "https://images.pexels.com/photos/3/p.jpg"}}]})
        return Reply(body=b"x" * 5000)

    monkeypatch.setattr(stock_media.requests, "get", fake_get)
    files, credits = stock_media.collect(["jar", "jar"], tmp_path)
    assert [f.name for f in files] == ["stock0.mp4", "stock1.jpg"]            # the clip is never reused
    assert [(c["kind"], c["creator"]) for c in credits] == [("video", "Ann"), ("photo", "Bo")]
    assert "https://videos.pexels.com/video-files/7/hd.mp4" in calls
    assert all(stock_media.allowed(url) for url in calls)


def test_pixabay_results_must_actually_match_the_scene():
    from backend import stock_media
    hits = [{"id": 1, "tags": "bokeh, colorful, abstract"}, {"id": 2, "tags": "bell, ornament, tree"},
            {"id": 3, "tags": "jar, lid, kitchen, hands"}, {"id": 4, "tags": "jars, pantry"},
            {"id": 5, "tags": "opening, countdown, numbers"}]
    assert [h["id"] for h in stock_media.on_topic("opening jar lid", hits)] == [3]
    assert [h["id"] for h in stock_media.on_topic("jar", hits)] == [3, 4]
    assert stock_media.on_topic("milk frother", hits) == []
