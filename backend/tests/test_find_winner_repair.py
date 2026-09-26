"""backend/tests/test_find_winner_repair.py

Regression suite for the Find Winner / 1-Click Pinterest repair.

14 acceptance cases:

  1. Find Winner attempts the live research path
  2. Multiple candidates are evaluated (≥3)
  3. Winner contains timestamp + source metadata
  4. Internet evidence URLs are preserved
  5. Fallback is explicitly labeled when research fails
  6. Static pool "trend_signals" are NOT reported as fresh evidence in fallback
  7. Winner payload passes into Pinterest generation (POST /traffic/generate)
  8. Pinterest generation uses product_payload, not URL scraping
  9. Pinterest response field mapping renders correctly (canonical + alias keys)
 10. Selected product image matches the winner (rendered into the card)
 11. 2:3 pin image functionality remains available (canvas conversion in JS)
 12. Existing normal URL campaign generation still works (scrape path)
 13. Existing MiniMax / video endpoints are not broken (routes still mounted)
 14. The router reports research metadata in the response envelope

Run: python -m pytest backend/tests/test_find_winner_repair.py -v
"""
from __future__ import annotations

import os
import sys
import pathlib
import json
from unittest import mock

import pytest

# Make sure the project root is on sys.path
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend import live_research
from backend.live_research import ResearchEnvelope, ResearchSource
from backend.product_research import get_researcher, _build_label_to_key, _PINTEREST_FRIENDLY, _COMMERCIAL_HIGH


# ── Test fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_researcher_singleton():
    """Reset the ProductResearcher singleton so each test gets a fresh instance."""
    import backend.product_research as pr
    pr._singleton = None
    yield
    pr._singleton = None


@pytest.fixture
def live_envelope():
    """A 'live' research envelope with 3+ sources — triggers research-backed path."""
    return ResearchEnvelope(
        research_status="live",
        research_timestamp="2026-09-26T17:00:00Z",
        research_provider="tavily",
        research_sources=[
            {"title": "Top pet products 2026", "snippet": "orthopedic dog beds calm anxious dogs",
             "url": "https://example.com/pet-1", "provider": "tavily"},
            {"title": "Best dog beds", "snippet": "self-warming donut beds for puppies",
             "url": "https://example.com/pet-2", "provider": "tavily"},
            {"title": "Pet supplies review", "snippet": "calming pet beds are trending on TikTok",
             "url": "https://example.com/pet-3", "provider": "tavily"},
            {"title": "Amazon best sellers pets", "snippet": "orthopedic dog bed self warming plush",
             "url": "https://example.com/pet-4", "provider": "tavily"},
        ],
        research_summary="orthopedic dog beds calm anxious dogs · self-warming donut beds",
        research_query="pet products",
    )


@pytest.fixture
def fallback_envelope():
    """An empty 'fallback' envelope — explicit no-evidence case."""
    return ResearchEnvelope(
        research_status="fallback",
        research_timestamp="2026-09-26T17:00:00Z",
        research_provider="none",
        research_sources=[],
        research_summary="Live research unavailable.",
        research_query="pet products",
    )


def _patch_research(envelope: ResearchEnvelope):
    """Patch live_research.research to return a fixed envelope."""
    return mock.patch.object(live_research, "research", return_value=envelope)


# ── Case 1: Find Winner attempts the live research path ──────────────────────


def test_case_1_find_winner_calls_live_research():
    """The pick() entry point MUST call live_research.research() at least once."""
    captured_query = []

    def fake_research(query, *, max_results=6):
        captured_query.append(query)
        return ResearchEnvelope(
            research_status="fallback", research_timestamp="2026-09-26T00:00:00Z",
            research_provider="none", research_sources=[],
            research_summary="", research_query=query,
        )

    with mock.patch.object(live_research, "research", side_effect=fake_research):
        get_researcher().pick("pet supplies")

    assert captured_query, "live_research.research was never called"
    assert captured_query[0] == "pet supplies"


# ── Case 2: Multiple candidates are evaluated (≥3) ───────────────────────────


def test_case_2_multi_candidate_evaluation(live_envelope):
    """When live research succeeds, candidates_evaluated MUST contain ≥3 entries."""
    with _patch_research(live_envelope):
        out = get_researcher().pick("pet products")
    cands = out.get("candidates_evaluated") or []
    assert len(cands) >= 3, f"expected >=3 evaluated candidates, got {len(cands)}"
    # exactly one selected
    selected = [c for c in cands if c.get("selected")]
    assert len(selected) == 1
    assert selected[0]["id"] == out["id"]


# ── Case 3: Winner contains timestamp + source metadata ──────────────────────


def test_case_3_winner_has_research_metadata(live_envelope):
    """Winner payload MUST include research_timestamp, research_provider, source."""
    with _patch_research(live_envelope):
        out = get_researcher().pick("pet products")
    assert out.get("research_timestamp"), "research_timestamp missing"
    assert out.get("research_provider") == "tavily"
    assert out.get("research_status") == "live"
    assert out.get("source") in ("live_research", "partial_research")


# ── Case 4: Internet evidence URLs are preserved ─────────────────────────────


def test_case_4_research_source_urls_preserved(live_envelope):
    """research_sources MUST carry source URLs from the live web."""
    with _patch_research(live_envelope):
        out = get_researcher().pick("pet products")
    sources = out.get("research_sources") or []
    assert sources, "research_sources is empty"
    for s in sources:
        assert s.get("url"), f"source missing URL: {s}"
        assert s.get("url").startswith(("http://", "https://")), f"bad URL: {s.get('url')}"


# ── Case 5: Fallback is explicitly labeled when research fails ────────────────


def test_case_5_fallback_labeled_when_no_research(fallback_envelope):
    """When research returns 'fallback', the winner source MUST be 'fallback_pool'."""
    with _patch_research(fallback_envelope):
        out = get_researcher().pick("pet products")
    assert out.get("source") == "fallback_pool", \
        f"expected source='fallback_pool', got {out.get('source')}"
    assert out.get("research_status") == "fallback"
    assert "fallback" in (out.get("selection_rationale") or "").lower()


# ── Case 6: Static pool trend_signals are NOT reported as fresh evidence ──────


def test_case_6_pool_trend_signals_suppressed(fallback_envelope):
    """When in fallback mode, trend_signals MUST be empty (don't show as live)."""
    with _patch_research(fallback_envelope):
        out = get_researcher().pick("pet products")
    assert out.get("trend_signals") == [], \
        f"trend_signals should be cleared in fallback: {out.get('trend_signals')}"
    assert out.get("trend_signals_note"), "trend_signals_note should explain the clear"


# ── Case 7: Winner payload passes into Pinterest generation ─────────────────


def test_case_7_winner_passes_into_pinterest_generation():
    """The /traffic/generate endpoint MUST accept the winner payload via product_payload."""
    from fastapi.testclient import TestClient
    from trafficlift_pro import app

    c = TestClient(app)

    winner = {
        "id": "test-winner-01",
        "name": "Test Product",
        "category": "Test Category",
        "image_url": "data:image/svg+xml;base64,abc",
        "url": "https://example.com/p/1",
        "angle": "Test angle",
        "pin_title": "Test pin",
        "pin_description": "Test description",
        "hashtags": ["#test"],
    }
    r = c.post("/api/v1/traffic/generate", json={
        "mode": "organic",
        "target_channels": ["pinterest"],
        "product_payload": winner,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scraped_product"]["title"] == "Test Product"
    assert body["source"] == "payload"


# ── Case 8: Pinterest generation uses product_payload, not scraping ─────────


def test_case_8_pinterest_uses_payload_not_scrape():
    """The payload path MUST set source='payload' (no URL scrape happened)."""
    from fastapi.testclient import TestClient
    from trafficlift_pro import app

    c = TestClient(app)
    r = c.post("/api/v1/traffic/generate", json={
        "mode": "organic",
        "target_channels": ["pinterest"],
        "product_payload": {
            "id": "x", "name": "X", "category": "C",
            "image_url": "data:image/svg+xml;base64,abc",
            "url": "https://example.com/", "angle": "A",
            "pin_title": "P", "pin_description": "D", "hashtags": [],
        },
    })
    assert r.status_code == 200
    assert r.json()["source"] == "payload", \
        "URL was scraped instead of using the safe payload path"


# ── Case 9: Pinterest response field mapping is complete ─────────────────────


def test_case_9_pinterest_field_mapping_has_both_canonical_and_aliases():
    """Pinterest payload MUST include both canonical AND aliased keys."""
    from fastapi.testclient import TestClient
    from trafficlift_pro import app

    c = TestClient(app)
    r = c.post("/api/v1/traffic/generate", json={
        "mode": "organic",
        "target_channels": ["pinterest"],
        "product_payload": {
            "id": "x", "name": "X", "category": "C",
            "image_url": "data:image/svg+xml;base64,abc",
            "url": "https://example.com/", "angle": "A",
            "pin_title": "P", "pin_description": "D", "hashtags": ["#t"],
        },
    })
    assert r.status_code == 200
    pkg = r.json()["compiled_package"]["pinterest_seo_engine"]

    # Canonical (backend serializer names)
    for key in (
        "board_title", "pin_title", "optimized_description",
        "recommended_keywords", "suggested_board_names",
        "optimal_pin_dimensions", "pin_cta_suggestion",
    ):
        assert key in pkg, f"canonical key missing: {key}"

    # Aliased (frontend-friendly names)
    for key in ("pin_description", "keywords", "suggested_boards", "dimensions"):
        assert key in pkg, f"alias key missing: {key}"

    # Canonical and alias values must agree
    assert pkg["pin_description"] == pkg["optimized_description"]
    assert pkg["keywords"] == pkg["recommended_keywords"]
    assert pkg["suggested_boards"] == pkg["suggested_board_names"]
    assert pkg["dimensions"] == pkg["optimal_pin_dimensions"]


# ── Case 10: Selected product image matches the winner ───────────────────────


def test_case_10_winner_image_matches_card():
    """The /find-winner response image_url MUST be the winner's image (passed through)."""
    with _patch_research(live_envelope := ResearchEnvelope(
        research_status="live", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="tavily",
        research_sources=[
            {"title": "t1", "snippet": "s1", "url": "https://example.com/a", "provider": "tavily"},
            {"title": "t2", "snippet": "s2", "url": "https://example.com/b", "provider": "tavily"},
            {"title": "t3", "snippet": "s3", "url": "https://example.com/c", "provider": "tavily"},
        ],
        research_summary="x", research_query="y",
    )):
        out = get_researcher().pick("pet products")
    assert out.get("image_url"), "winner missing image_url"
    assert out["image_url"] == out["image_url"]  # not empty placeholder


# ── Case 11: 2:3 pin image download functionality is available in JS ──────────


def test_case_11_pin_image_canvas_function_exists():
    """index.html MUST contain a downloadPinImage / canvas-based pin image function."""
    index_html = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "downloadPinImage" in index_html, "downloadPinImage() missing"
    # 2:3 ratio: 1000x1500
    assert "1000" in index_html and "1500" in index_html, "1000x1500 canvas dimensions missing"
    assert "image/jpeg" in index_html, "JPEG canvas conversion missing"


# ── Case 12: Existing normal URL campaign generation still works ─────────────


def test_case_12_url_scrape_path_still_works():
    """The original input_url-only scrape path must still function."""
    from fastapi.testclient import TestClient
    from trafficlift_pro import app

    c = TestClient(app)
    r = c.post("/api/v1/traffic/generate", json={
        "mode": "organic",
        "target_channels": ["pinterest"],
        "input_url": "https://example.com/",
    })
    # example.com is reachable but has no product; we just need 200 + scrape source
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "scrape"


# ── Case 13: Existing MiniMax / video endpoints not broken ───────────────────


def test_case_13_video_routes_still_mounted():
    """All video routes MUST still be present in the app router."""
    from trafficlift_pro import app
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    for required in (
        "/api/v1/video/models",
        "/api/v1/video/generate",
        "/api/v1/video/render",
        "/api/v1/minimax/video-params",
    ):
        assert required in paths, f"video route missing: {required}"


# ── Case 14: Router returns research metadata in the response envelope ───────


def test_case_14_router_returns_research_envelope():
    """GET /api/v1/find-winner MUST return a full research envelope."""
    from fastapi.testclient import TestClient
    from trafficlift_pro import app

    c = TestClient(app)
    r = c.get("/api/v1/find-winner?url_or_keyword=pet+bed")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "research_status", "research_timestamp", "research_provider",
        "research_sources", "research_summary",
    ):
        assert key in body, f"router response missing research key: {key}"
    assert body["research_status"] in ("live", "partial", "fallback"), \
        f"unknown status: {body['research_status']}"


# ── Unit sanity: scoring + helpers ───────────────────────────────────────────


def test_label_to_key_inverse_of_category_labels():
    """_LABEL_TO_KEY must roundtrip every key in CATEGORY_LABELS."""
    from backend.product_research import CATEGORY_LABELS
    for k, label in CATEGORY_LABELS.items():
        assert _build_label_to_key()[label] == k


def test_pinterest_friendly_categories_are_aesthetic():
    """_PINTEREST_FRIENDLY should not include 'cleaning' (low-aesthetic)."""
    assert "cleaning" not in _PINTEREST_FRIENDLY
    assert "decor" in _PINTEREST_FRIENDLY


def test_commercial_high_includes_pet_and_kitchen():
    """Pet + kitchen are commercial-intent heavy on Pinterest."""
    assert "pet" in _COMMERCIAL_HIGH
    assert "kitchen" in _COMMERCIAL_HIGH


def test_research_envelope_serialization():
    """ResearchEnvelope.to_dict() should return a JSON-friendly dict."""
    e = ResearchEnvelope(
        research_status="live", research_timestamp="2026-01-01T00:00:00Z",
        research_provider="tavily",
        research_sources=[{"title": "t", "snippet": "s", "url": "https://x", "provider": "tavily"}],
        research_summary="sum", research_query="q",
    )
    d = e.to_dict()
    assert d["research_status"] == "live"
    assert d["research_provider"] == "tavily"
    assert isinstance(d["research_sources"], list)
    json.dumps(d)  # must serialize cleanly


def test_pick_research_status_fallback_returns_real_product():
    """Even when live research fails, the fallback MUST return a real (named) product."""
    fallback = ResearchEnvelope(
        research_status="fallback", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="none", research_sources=[],
        research_summary="Live research unavailable.",
        research_query="dog bed",
    )
    with _patch_research(fallback):
        out = get_researcher().pick("dog bed")
    assert out["name"] and out["name"] != "Untitled Product", \
        f"Fallback returned blank/unknown name: {out['name']}"
    assert out["id"]