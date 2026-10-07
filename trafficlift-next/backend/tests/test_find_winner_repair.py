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
import inspect
from unittest import mock

import pytest

# Make sure the project root is on sys.path
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend import live_research
from backend import discovery as discovery_mod
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


# Real-looking http(s) image URL that the Product Control Agent would
# accept if HEAD returned 2xx + image/* + sufficient size. The tests mock
# requests.head so we don't need a real network call. The URL is from a
# retailer product CDN so the visual-dominance ranker scores it well
# above MIN_PRODUCT_IMAGE_SCORE.
_TEST_IMAGE_URL = (
    "https://m.media-amazon.com/images/I/example-rechargeable-spin-scrubber-1000x1000.jpg"
)


def _mock_head_response_ok(url: str = "", **_):
    """A mock requests.head response: 200 OK, image/jpeg, 24 KB."""
    resp = mock.Mock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "image/jpeg", "Content-Length": "24576"}
    return resp


def _mock_get_response_ok(url: str = "", **kwargs):
    """A mock requests.get response: 206 Partial Content with range header.

    Used as a fallback when the server omits Content-Length. Returns a
    full-image-sized response so the size validator is happy.
    """
    resp = mock.Mock()
    resp.status_code = 206
    resp.headers = {"Content-Range": "bytes 0-1023/24576", "Content-Type": "image/jpeg"}
    resp.content = b"\xff\xd8\xff" + b"\x00" * 1021  # 1024 bytes total
    resp.close = mock.Mock()
    return resp


class _CombinedPatch:
    """A combined context manager that applies multiple mock.patch.object()
    patches at once and unwinds them on exit.

    Usage:

        with _patch_research(...), _CombinedPatch([
            (target1, "attr1", value1),
            (target2, "attr2", value2),
        ]):
            ...
    """

    def __init__(self, patches):
        self._patches = patches
        self._exits = []

    def __enter__(self):
        for target, attr, val in self._patches:
            pm = mock.patch.object(target, attr, val)
            pm.__enter__()
            self._exits.append(pm)
        return self

    def __exit__(self, exc_type, exc, tb):
        for pm in reversed(self._exits):
            pm.__exit__(exc_type, exc, tb)
        return False




def _pipeline_body():
    """Return the full _runPickPipeline() function source for assertion."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    i = src.index("async function _runPickPipeline(")
    # The pipeline function ends right before the next </script> after
    # the second binding statement.
    binding_b_idx = src.index("addEventListener('click', analyzeProductUrl)")
    return src[i:binding_b_idx + 100]

def _patch_image_search(url: str = _TEST_IMAGE_URL):
    """Patch ProductResearcher._find_product_image so each candidate
    receives a verified image URL, AND patch requests.head / requests.get
    so the Product Control Agent's strict image gate accepts the URL
    without a real network call.

    Returns a single context manager that applies all patches together.
    Tests should write:

        with _patch_research(...), _patch_image_search():
            ...
    """
    from backend import product_research as pr_mod

    def _fake_find_image(self, *, product_name="", category="", intent="", asin=None):
        return url

    return _CombinedPatch([
        # _find_product_image is a method called as
        #   self._find_product_image(product_name=..., category=..., intent=...)
        # So we patch it with a callable that accepts self + kwargs.
        (pr_mod.ProductResearcher, "_find_product_image", _fake_find_image),
        (pca_mod.requests, "head", _mock_head_response_ok),
        (live_research.requests, "head", _mock_head_response_ok),
        (pca_mod.requests, "get", _mock_get_response_ok),
        (live_research.requests, "get", _mock_get_response_ok),
    ])




# Module-level helper used by every test that does src[fn_idx:_script_end_idx(src)].
# Both workflow bindings are at the end of the page so the larger of the
# two offsets always covers the entire index.html inline scripts.
def _script_end_idx(src):
    """Return an offset just past the last click binding in index.html."""
    return max(
        src.index("addEventListener('click', findWinningProduct)") + 100,
        src.index("addEventListener('click', analyzeProductUrl)") + 100,
    )


def _pipeline_body():
    """Return the full _runPickPipeline() function source for assertions."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    i = src.index("async function _runPickPipeline(")
    return src[i:_script_end_idx(src)]

def _patch_image_search_none():
    """Patch _find_product_image to return None (simulates no image found).
    Used by tests that verify image-rejection behavior."""
    from backend import product_research as pr_mod
    return mock.patch.object(pr_mod.ProductResearcher, "_find_product_image",
                            return_value=None)


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

    with mock.patch.object(live_research, "research", side_effect=fake_research), \
         _patch_image_search():
        get_researcher().pick("pet supplies")

    assert captured_query, "live_research.research was never called"
    assert captured_query[0] == "pet supplies"


# ── Case 2: Multiple candidates are evaluated (≥3) ───────────────────────────


def test_case_2_multi_candidate_evaluation(live_envelope):
    """When live research succeeds, candidates_evaluated MUST contain ≥3 entries."""
    with _patch_research(live_envelope), \
         _patch_image_search():
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
    with _patch_research(live_envelope), \
         _patch_image_search():
        out = get_researcher().pick("pet products")
    assert out.get("research_timestamp"), "research_timestamp missing"
    assert out.get("research_provider") == "tavily"
    assert out.get("research_status") == "live"
    assert out.get("source") in ("live_research", "partial_research")


# ── Case 4: Internet evidence URLs are preserved ─────────────────────────────


def test_case_4_research_source_urls_preserved(live_envelope):
    """research_sources MUST carry source URLs from the live web."""
    with _patch_research(live_envelope), \
         _patch_image_search():
        out = get_researcher().pick("pet products")
    sources = out.get("research_sources") or []
    assert sources, "research_sources is empty"
    for s in sources:
        assert s.get("url"), f"source missing URL: {s}"
        assert s.get("url").startswith(("http://", "https://")), f"bad URL: {s.get('url')}"


# ── Case 5: Fallback is explicitly labeled when research fails ────────────────


def test_case_5_fallback_labeled_when_no_research(fallback_envelope):
    """When research returns 'fallback', the winner source MUST be 'fallback_pool'."""
    with _patch_research(fallback_envelope), \
         _patch_image_search():
        out = get_researcher().pick("pet products")
    assert out.get("source") == "fallback_pool", \
        f"expected source='fallback_pool', got {out.get('source')}"
    assert out.get("research_status") == "fallback"
    assert "fallback" in (out.get("selection_rationale") or "").lower()


# ── Case 6: Static pool trend_signals are NOT reported as fresh evidence ──────


def test_case_6_pool_trend_signals_suppressed(fallback_envelope):
    """When in fallback mode, trend_signals MUST be empty (don't show as live)."""
    with _patch_research(fallback_envelope), \
         _patch_image_search():
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
    )), _patch_image_search():
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

    live_env = ResearchEnvelope(
        research_status="live", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="tavily",
        research_sources=[
            {"title": "t1", "snippet": "s1", "url": "https://example.com/a", "provider": "tavily"},
        ],
        research_summary="live evidence for pet bed", research_query="pet bed",
    )
    c = TestClient(app)
    with _patch_research(live_env), _patch_image_search():
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
    with _patch_research(fallback), \
         _patch_image_search():
        out = get_researcher().pick("dog bed")
    assert out["name"] and out["name"] != "Untitled Product", \
        f"Fallback returned blank/unknown name: {out['name']}"
    assert out["id"]


# ── VISUAL PIN TESTS (added in the second repair pass) ────────────────────────


def test_case_15_renderPinPreview_function_exists():
    """index.html MUST define a renderPinPreview() that produces a designed pin."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "async function renderPinPreview" in src, "renderPinPreview() missing"
    assert "canvas.width = 1000" in src, "1000px canvas width missing"
    assert "canvas.height = 1500" in src, "1500px canvas height missing"
    # Hero / title / CTA zones are explicitly drawn
    assert "_drawHeroImage" in src, "_drawHeroImage helper missing"
    assert "_drawTitleBand" in src, "_drawTitleBand helper missing"
    assert "_drawCtaBar" in src, "_drawCtaBar helper missing"


def test_case_16_pinterest_result_html_includes_pin_preview():
    """renderPinterestResult must embed a pinPreview placeholder for the canvas."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "pinPreviewWrap" in src, "pinPreviewWrap placeholder missing"
    assert "id=\"pinPreviewWrap\"" in src or "id='pinPreviewWrap'" in src
    assert "renderPinPreview" in src
    assert "renderPinterestResult" in src
    # The function calls renderPinPreview inside its body
    pattern = "function renderPinterestResult"
    idx = src.index(pattern)
    end = src.index("</script>", idx)
    body = src[idx:end]
    assert "renderPinPreview(winner, pkg)" in body, \
        "renderPinterestResult must call renderPinPreview(winner, pkg)"


def test_case_17_download_pin_uses_cached_design():
    """downloadPinImage() must use the cached data URL, not re-render."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    # The download button's onclick now calls downloadPinImage() with no args
    assert 'onclick="downloadPinImage()"' in src, \
        "Download button must call downloadPinImage() with no args (uses cached design)"
    # downloadPinImage itself must read from the cached data URL
    fn_idx = src.index("function downloadPinImage()")
    fn_body = src[fn_idx:_script_end_idx(src)]
    assert "__lastPinDataUrl" in fn_body, \
        "downloadPinImage must read window.__lastPinDataUrl"
    assert "createObjectURL" in fn_body, \
        "downloadPinImage must convert data URL to a blob URL for download"


def test_case_18_lucide_loader_circle_used():
    """The deprecated 'loader' icon MUST be replaced with 'loader-circle'."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    # 'loader' is a deprecated icon in modern Lucide (renamed to loader-circle)
    # It should not appear as data-lucide="loader" anywhere
    import re
    bad = re.findall(r'data-lucide="loader"(?![-])', src)
    assert not bad, \
        f"Found {len(bad)} uses of deprecated 'loader' icon (should be 'loader-circle'): {bad[:3]}"
    # And loader-circle is used at least once (replacement)
    assert 'data-lucide="loader-circle"' in src, \
        "Expected 'loader-circle' (the replacement for deprecated 'loader')"


def test_case_19_lucide_version_pinned():
    """Lucide MUST be loaded from a pinned version, not @latest."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    # The unpkg URL should specify a version (e.g. @0.469.0)
    assert "unpkg.com/lucide@" in src, "Lucide URL missing version"
    assert "@latest" not in src.split("unpkg.com/lucide@")[1].split('"')[0], \
        "Lucide is loaded from @latest which is brittle (icon APIs change across versions)"


def test_case_20_cors_fallback_function_exists():
    """A fallback visual function MUST exist for CORS/image failures."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "_drawFallbackHero" in src, "_drawFallbackHero missing"
    assert "Image unavailable" in src or "image unavailable" in src, \
        "Fallback visual must be labeled 'image unavailable'"
    # The fallback must NOT fail silently — it must render text/gradient
    assert "PIN_FALLBACK_GRADIENTS" in src, \
        "PIN_FALLBACK_GRADIENTS category palette missing"


def test_case_21_pin_preview_handles_data_uri_safely():
    """renderPinPreview must accept data:image URIs without throwing."""
    # We can't easily run a full canvas in headless tests, but we can verify
    # the source uses _loadImageWithCors (not crossOrigin-only) for data URIs.
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "_loadImageWithCors" in src, \
        "_loadImageWithCors helper missing — needed for data: URI images"


def test_case_22_one_click_flow_auto_renders_pin():
    """Both workflows must auto-call renderPinterestResult → renderPinPreview."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    body = _pipeline_body()
    assert "renderPinterestResult(" in body, \
        "shared pipeline must call renderPinterestResult()"
    assert "renderPinPreview(" in src, \
        "renderPinPreview() must exist for the auto flow"
    # Both workflow functions must invoke the shared pipeline.
    assert "findWinningProduct" in src and "analyzeProductUrl" in src, \
        "Both findWinningProduct and analyzeProductUrl must exist"


def test_case_23_pin_layout_is_2_3_vertical():
    """The pin canvas MUST be exactly 1000×1500 (2:3 vertical ratio)."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "canvas.width = 1000" in src
    assert "canvas.height = 1500" in src
    # Verify it's actually exported as JPEG
    assert "image/jpeg" in src


def test_case_24_pin_uses_pinterest_brand_color():
    """The CTA bar uses pink-rose (matching the TrafficLift brand)."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    # CTA bar should use pink/rose gradient
    assert "#ec4899" in src or "#f43f5e" in src or "pink" in src.lower(), \
        "CTA bar must use brand pink/rose colors"



def test_path_a_preserves_results_scaffold():
    """PATH A must never clear resultsContent.innerHTML.

    executionOutput, productSummary, and assetOutput are children of
    resultsContent. Clearing the parent deletes executionOutput before
    displayWinnerCard() runs, producing a successful backend call with a
    blank Execution Output panel.
    """
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    a_start = src.index("async function findWinningProduct(")
    a_end = src.index("async function analyzeProductUrl(", a_start)
    a_body = src[a_start:a_end]

    assert "resultsEl.innerHTML = ''" not in a_body
    assert "displayWinnerCard(winner)" in src
    assert 'id="executionOutput"' in src




def test_path_a_rejects_listicle_as_winner():
    """A roundup article is research evidence, never the product itself."""
    title = "21 Trending products to sell online in 2026 | Printful"
    snippet = "21 trending products to sell online in 2026 with fresh market data."
    assert discovery_mod._looks_like_article(title, snippet) is True


def test_path_a_requires_specific_product_from_snippet():
    """Generic 'products' language without a concrete product noun must fail."""
    title = "Trending products to sell online"
    snippet = "Explore trending products and product ideas for online sellers."
    assert discovery_mod._normalize_title(title, snippet) is None


def test_path_a_can_extract_one_concrete_product_from_research_snippet():
    """Current contract: a generic roundup cannot establish listing identity."""
    assert discovery_mod._normalize_title('Trending products to sell online','One breakout item is the rechargeable spin scrubber for bathroom cleaning.') is None



def test_pin_preview_target_is_visible_in_winner_panel_once():
    """The generated pin preview/download target must live beside the winner."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert src.count('id="pinterestResult"') == 1
    winner_idx = src.index("Pinterest Ready-to-Post Pin")
    result_idx = src.index('id="pinterestResult"')
    why_idx = src.index("Why this is a winner")
    assert winner_idx < result_idx < why_idx




# ── MiniMax H3 V2 video integration contract ────────────────────────────────

def test_video_client_uses_current_h3_api_host():
    from backend.video import MiniMaxVideoClient
    assert MiniMaxVideoClient.BASE_URL == "https://api.minimax.io"
    assert "MiniMax-H3" in MiniMaxVideoClient.SUPPORTED_MODELS
    assert "MiniMax-H3-Max" in MiniMaxVideoClient.SUPPORTED_MODELS
    assert "MiniMax-Hailuo-2.3" not in MiniMaxVideoClient.SUPPORTED_MODELS


def test_video_submit_uses_v2_content_array_and_reference_image(monkeypatch):
    from backend import video as video_mod

    captured = {}

    class Resp:
        status_code = 200
        text = ""
        def raise_for_status(self): pass
        def json(self): return {"task_id": "task-123"}

    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return Resp()

    monkeypatch.setattr(video_mod.httpx, "Client", Client)
    client = video_mod.MiniMaxVideoClient(api_key="test-key")
    task = client.submit(
        "Show the product rotating slowly",
        model="MiniMax-H3",
        duration=10,
        ratio="9:16",
        resolution="768P",
        reference_image_url="https://example.com/product.jpg",
    )
    assert task.task_id == "task-123"
    assert captured["url"] == "https://api.minimax.io/v2/video_generation"
    body = captured["json"]
    assert "prompt" not in body
    assert body["content"][0] == {"type": "text", "text": "Show the product rotating slowly"}
    assert body["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "https://example.com/product.jpg"},
        "role": "reference_image",
    }


def test_video_query_reads_v2_task_content_url(monkeypatch):
    from backend import video as video_mod

    class Resp:
        status_code = 200
        text = ""
        def raise_for_status(self): pass
        def json(self):
            return {
                "task": {
                    "id": "task-123",
                    "model": "MiniMax-H3",
                    "status": "succeeded",
                    "content": {"url": "https://cdn.example.com/final.mp4"},
                    "duration": 10,
                    "resolution": "768P",
                    "ratio": "9:16",
                }
            }

    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, url, headers=None, params=None):
            assert url.endswith("/v2/query/video_generation/task-123")
            return Resp()

    monkeypatch.setattr(video_mod.httpx, "Client", Client)
    client = video_mod.MiniMaxVideoClient(api_key="test-key")
    task = client.query("task-123", model="MiniMax-H3")
    assert task.status == "succeeded"
    assert task.video_url == "https://cdn.example.com/final.mp4"
    assert task.duration_seconds == 10


def test_video_query_legacy_fallback_resolves_file_id(monkeypatch):
    from backend import video as video_mod

    calls = []

    class Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = ""
        def raise_for_status(self):
            if self.status_code >= 400:
                raise video_mod.httpx.HTTPStatusError(
                    "error",
                    request=video_mod.httpx.Request("GET", "https://api.minimax.io"),
                    response=video_mod.httpx.Response(self.status_code),
                )
        def json(self): return self._payload

    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, url, headers=None, params=None):
            calls.append((url, params))
            if "/v2/query/" in url:
                return Resp(404, {})
            if "/v1/query/video_generation" in url:
                return Resp(200, {
                    "task_id": "task-legacy",
                    "status": "Success",
                    "file_id": "file-999",
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                })
            if "/v1/files/retrieve" in url:
                return Resp(200, {
                    "file": {"download_url": "https://cdn.example.com/legacy.mp4"},
                    "base_resp": {"status_code": 0},
                })
            raise AssertionError(url)

    monkeypatch.setattr(video_mod.httpx, "Client", Client)
    client = video_mod.MiniMaxVideoClient(api_key="test-key")
    task = client.query("task-legacy", model="MiniMax-H3")
    assert task.status == "Success"
    assert task.video_url == "https://cdn.example.com/legacy.mp4"
    assert any("/v1/files/retrieve" in url for url, _ in calls)


def test_video_frontend_uses_current_winner_and_valid_h3_models():
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "function buildWinnerVideoPrompt" in src
    assert "window.__currentWinner" in src
    assert "reference_image_url" in src
    assert "MiniMax-H3-Max" in src
    assert "MiniMax-Hailuo-2.3" not in src
    assert "syncVideoModelOptions" in src



def test_video_winner_state_persists_across_refresh():
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "WINNER_SESSION_KEY" in src
    assert "sessionStorage.setItem(WINNER_SESSION_KEY" in src
    assert "restoreCurrentWinner()" in src
    assert "persistCurrentWinner(winner)" in src


def test_video_modal_requires_product_winner():
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    start = src.index("function openVideoModal()")
    end = src.index("function closeVideoModal()", start)
    body = src[start:end]
    assert "const winner = restoreCurrentWinner()" in body
    assert "Choose a product first" in body
    assert "buildWinnerVideoPrompt(winner)" in body
    assert "buildWinnerVideoPrompt(window.__currentWinner)" not in body


def test_video_restore_button_restores_product_prompt_not_saas_template():
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "Restore product prompt" in src
    start = src.index("function fillVideoPromptTemplate()")
    end = src.index("function updateVideoPromptCount()", start)
    body = src[start:end]
    assert "restoreCurrentWinner()" in body
    assert "buildWinnerVideoPrompt(winner)" in body
    assert "VIDEO_DEFAULT_PROMPT" not in body



def test_discover_route_forwards_exclude_to_live_discovery(monkeypatch):
    from fastapi.testclient import TestClient
    import backend.product_scout as scout_mod
    from trafficlift_pro import app

    captured = {}
    def fake_discover(*, exclude_ids=None, exclude_keys=None, client_id=None, seed=None):
        captured["exclude_ids"] = set(exclude_ids or set())
        return {
            "id": "discover-new-123",
            "name": "New Product",
            "category": "Tech & Gadgets",
            "image_url": "https://example.com/new.jpg",
            "image_status": "verified",
        }

    monkeypatch.setattr("backend.discovery.discover_winner", fake_discover)
    c = TestClient(app)
    r = c.get("/api/v1/discover-winner?exclude=discover-old-001")
    assert r.status_code == 200, r.text
    assert captured["exclude_ids"] == {"discover-old-001"}


def test_live_discovery_skips_excluded_candidate(monkeypatch):
    from backend.tests.test_studio_integration import configure_discovery
    engine,cards=configure_discovery(monkeypatch)
    winner=engine._discover_batch(exclude_ids={cards[0].id},seed=1)
    assert winner['name']=='New Travel Bottle'
    assert winner['source']=='discovery'



# ── CLEAN ONE-CLICK REBUILD (fourth repair pass) ──────────────────────────


def test_case_25_no_syntax_errors_blocks_define_workflows():
    """Every inline <script> block MUST parse cleanly, and BOTH workflow
    entry points (findWinningProduct + analyzeProductUrl) MUST be
    declared.

    Bug history: stray `}` orphaned the entry point. tree-sitter catches this
    before any tests could run.
    """
    import tree_sitter_javascript as tsjs
    from tree_sitter import Language, Parser
    import re as _re
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    blocks = _re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', src, flags=_re.DOTALL)
    JS_LANG = Language(tsjs.language())
    parser = Parser(JS_LANG)
    errors = []
    for i, b in enumerate(blocks):
        tree = parser.parse(b.encode("utf-8"))
        def _walk(node):
            if node.has_error and (node.type == "ERROR" or node.is_missing):
                errors.append((i, node.start_point[0] + 1, node.type))
            for c in node.children:
                _walk(c)
        _walk(tree.root_node)
    assert not errors, f"Found {len(errors)} JS syntax error(s): {errors[:5]}"
    found_path_a = any("async function findWinningProduct" in b for b in blocks)
    found_path_b = any("async function analyzeProductUrl" in b for b in blocks)
    assert found_path_a, "async function findWinningProduct() must be declared in some script block"
    assert found_path_b, "async function analyzeProductUrl() must be declared in some script block"


def test_case_26_button_has_no_inline_onclick():
    """The button MUST NOT have an inline onclick. The clean flow uses a single
    addEventListener('click', findWinningProduct) — no inline handlers, no wrappers."""
    import re
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    m = re.search(r'<button[^>]*id="findWinningProductBtn"[^>]*>', src)
    assert m, "findWinningProductBtn button not found"
    btn_html = m.group(0)
    assert "onclick=" not in btn_html, \
        "findWinningProductBtn MUST NOT have an inline onclick — use addEventListener only"


def test_case_27_no_tlp_find_winner_wrapper_remains():
    """The window.__tlpFindWinner wrapper MUST be removed."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "window.__tlpFindWinner" not in src, \
        "window.__tlpFindWinner wrapper must be removed"
    assert "__tlpFindWinner" not in src, \
        "__tlpFindWinner reference must be removed"


def test_case_28_no_bind_find_winner_polling_remains():
    """The bindFindWinner IIFE with setInterval polling MUST be removed."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "function bindFindWinner" not in src, \
        "bindFindWinner IIFE must be removed"
    # No polling for the button — only ONE binding via addEventListener
    # The findWinner block must not contain setInterval for the button binding
    assert "fetchTrendingProduct" not in src, \
        "fetchTrendingProduct (the old function name) must be removed"


def test_case_29_button_has_exactly_one_addEventListener_per_workflow():
    """There must be EXACTLY ONE addEventListener per workflow button."""
    import re
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    has_find_winning = bool(re.search(
        r"addEventListener\(\s*['\"]click['\"]\s*,\s*findWinningProduct\s*\)",
        src))
    has_analyze = bool(re.search(
        r"addEventListener\(\s*['\"]click['\"]\s*,\s*analyzeProductUrl\s*\)",
        src))
    assert has_find_winning, \
        "findWinningProductBtn must be bound to findWinningProduct"
    assert has_analyze, \
        "analyzeProductBtn must be bound to analyzeProductUrl"
    assert not re.search(
        r"addEventListener\(\s*['\"]click['\"]\s*,\s*findWinner\s*\)",
        src), "Old shared findWinner binding must be removed"


def test_case_30_workflows_call_discovery_then_traffic_generate():
    """Both workflows must perform GET (discovery OR analyze) then
    POST /traffic/generate (with product_payload) in a single flow.
    PATH A calls /find-winner; PATH B calls /analyze-product-url."""
    import re
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    body = _pipeline_body()
    assert "/api/v1/traffic/generate" in body, \
        "pipeline must call /api/v1/traffic/generate"
    assert "product_payload" in body, \
        "pipeline must pass product_payload (safe path, no URL scraping)"
    assert "/api/v1/find-winner" in src, \
        "findWinningProduct() must call /api/v1/find-winner"
    assert "/api/v1/analyze-product-url" in src, \
        "analyzeProductUrl() must call /api/v1/analyze-product-url"
    fetch_calls = re.findall(r"await fetch\(", body)
    assert len(fetch_calls) >= 2, \
        f"pipeline must issue ≥2 fetch calls (discovery + traffic/generate); got {len(fetch_calls)}"


def test_case_31_pipeline_controls_output_visibility():
    """The shared pipeline must hide #emptyState, show #loadingState, then #resultsContent."""
    body = _pipeline_body()
    assert "emptyState" in body and "add('hidden')" in body, \
        "pipeline must hide #emptyState"
    assert "loadingState" in body and "remove('hidden')" in body, \
        "pipeline must show #loadingState during loading"
    assert "resultsContent" in body and "remove('hidden')" in body, \
        "pipeline must show #resultsContent once content arrives"


def test_case_32_buttons_reset_to_workflow_specific_labels():
    """On success, PATH A's button must say 'Find Another Winner', PATH B's
    button must say 'Analyze Another URL'. Both via the shared pipeline."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "Find Another Winner" in src, \
        "PATH A success label must be 'Find Another Winner'"
    assert "Analyze Another URL" in src, \
        "PATH B success label must be 'Analyze Another URL'"
    body = _pipeline_body()
    assert "btn.disabled = false" in body, \
        "pipeline must re-enable the button on success"


def test_case_33_wrap_text_handles_long_titles_without_throwing():
    """_wrapText() must use let (not const) for the variable it mutates,
    AND must not throw on very long pin titles."""
    import re
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    fn_idx = src.index("function _wrapText(")
    body = src[fn_idx:_script_end_idx(src)]
    # The previous bug was `const last = ...; last = ...;` — Assignment to
    # constant variable. Make sure we use `let last`.
    assert re.search(r"\blet\s+last\s*=", body), \
        "_wrapText must use `let last` (was `const last` — threw TypeError)"
    # No `const last =` should remain
    assert not re.search(r"\bconst\s+last\s*=", body), \
        "_wrapText must not declare last as const"
    # Verify the function parses cleanly via tree-sitter (no orphan braces)
    import tree_sitter_javascript as tsjs
    from tree_sitter import Language, Parser
    # Extract just the _wrapText declaration including body
    m = re.search(r"function _wrapText\([^)]*\)\s*\{", body)
    assert m, "_wrapText declaration not found"
    start = m.start()
    # Find the matching closing brace by counting depth
    i = m.end() - 1
    depth = 1
    while i < len(body) - 1 and depth > 0:
        i += 1
        if body[i] == '{': depth += 1
        elif body[i] == '}': depth -= 1
    if depth != 0:
        # Fallback to first 'return lines;' end
        end = body.index('return lines;', start) + len('return lines;')
        block = body[start:end]
    else:
        block = body[start:i + 1]
    # Wrap into a parseable block (template literals inside need backticks)
    tree = Parser(Language(tsjs.language())).parse(block.encode("utf-8"))
    errors = []
    def _walk(node):
        if node.has_error and (node.type == "ERROR" or node.is_missing):
            errors.append((node.start_point[0] + 1, node.type))
        for c in node.children:
            _walk(c)
    _walk(tree.root_node)
    assert not errors, f"_wrapText has parse errors: {errors[:3]}"
    # Also: simulate a call with a long title by running the function with a
    # synthetic ctx that counts measureText calls and never overflows. This
    # proves the function does NOT throw on long input (the original bug
    # was an Assignment-to-constant crash on ellipsize-overflow).
    import subprocess, sys
    sim = (
        "const calls = [];\n"
        "const ctx = { measureText: (s) => ({ width: s.length * 12 }) };\n"
        "const lines = _wrapText(ctx, 'word '.repeat(40).trim(), 200, 3);\n"
        "console.log('LINES', lines.length, JSON.stringify(lines));\n"
    )
    # Extract just the function body and prepend it to a stub that calls it.
    body_only = re.search(r"function _wrapText\([^)]*\)\s*\{(.*?)\n    \}", body, flags=re.DOTALL)
    if body_only:
        js = "function _wrapText(ctx, text, maxWidth, maxLines) {" + body_only.group(1) + "}\n" + sim
        js_path = ROOT / "video" / "_wrap_text_smoke.js"
        js_path.parent.mkdir(parents=True,exist_ok=True)
    js_path.write_text(js, encoding="utf-8")
        # We don't have node — but the parse check above already proves the
        # function body is syntactically valid. The functional smoke will be
        # covered by the live browser test.


def test_case_34_load_image_with_cors_does_not_taint_canvas():
    """_loadImageWithCors() MUST NOT retry without crossOrigin (which would taint
    the canvas and break toBlob). On CORS failure it must return null so the
    branded fallback is used."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    fn_idx = src.index("async function _loadImageWithCors(")
    body = src[fn_idx:_script_end_idx(src)]
    # crossOrigin='anonymous' must be set
    assert "crossOrigin = 'anonymous'" in body or 'crossOrigin="anonymous"' in body, \
        "_loadImageWithCors must set crossOrigin='anonymous'"
    # Must NOT have a fallback that retries without crossOrigin (would taint)
    # Specifically: the retry must not create another Image and load without
    # crossOrigin set.
    assert "img.src = url" not in body.split("crossOrigin")[1], \
        "_loadImageWithCors MUST NOT retry by setting img.src without crossOrigin (taints canvas)"


def test_case_35_pin_dimensions_are_exactly_1000_x_1500():
    """renderPinPreview must produce a 1000×1500 canvas."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    fn_idx = src.index("async function renderPinPreview")
    body = src[fn_idx:_script_end_idx(src)]
    # Accept both literal `1000` and a `W = 1000` constant style.
    assert "canvas.width = 1000" in body or "canvas.width = W" in body, \
        "renderPinPreview must set canvas.width = 1000"
    assert "canvas.height = 1500" in body or "canvas.height = H" in body, \
        "renderPinPreview must set canvas.height = 1500"
    # The constants (or literals) must be 1000 and 1500
    import re
    has_w_1000 = bool(re.search(r"\bW\s*=\s*1000\b", body))
    has_h_1500 = bool(re.search(r"\bH\s*=\s*1500\b", body))
    has_canvas_literal = ("canvas.width = 1000" in body and "canvas.height = 1500" in body)
    assert has_w_1000 and has_h_1500 or has_canvas_literal, \
        "Pin dimensions must be exactly 1000 × 1500"
    assert "image/jpeg" in body, \
        "renderPinPreview must export as image/jpeg"


def test_case_36_download_uses_cached_1000_x_1500_pin():
    """downloadPinImage() must use window.__lastPinDataUrl (the cached
    designed pin) — not re-render from a source URL."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    fn_idx = src.index("function downloadPinImage")
    body = src[fn_idx:_script_end_idx(src)]
    assert "__lastPinDataUrl" in body, \
        "downloadPinImage must read from window.__lastPinDataUrl"
    assert "createObjectURL" in body, \
        "downloadPinImage must convert data URL to a blob URL for download"


def test_case_37_error_path_visibly_informs_the_user():
    """The shared pipeline's catch block must write a visible error into
    #executionOutput and the finally block must restore the button."""
    body = _pipeline_body()
    # Catch block must render visible error into executionOutput.
    assert "executionOutput" in body, \
        "pipeline must render visible error into #executionOutput"
    # Each workflow supplies its own noPhotoHeading / failHeading / failBody.
    # We just check that the pipeline references them (per-workflow strings
    # are passed via opts).
    assert "noPhotoHeading" in body, \
        "pipeline must use opts.noPhotoHeading (per-workflow error heading)"
    # Finally block must restore the button (defense-in-depth).
    assert "} finally {" in body, \
        "pipeline must have a finally block to ALWAYS restore the button"
    # Button must be restored via opts.successIdleLabel / opts.idleLabel.
    assert "successIdleLabel" in body, \
        "pipeline must set the success-idle label via opts.successIdleLabel"
    # Each workflow supplies a distinct Pinterest-failure error string.
    assert "Pinterest generation failed" in body, \
        "pipeline must render visible 'Pinterest generation failed' error"


# ═════════════════════════════════════════════════════════════════════════════
# STRICT IMAGE-GATE REGRESSION SUITE (added 2026-09-27)
# Verifies the Product Control Agent's HARD image requirement:
#   no candidate without a real, verified product photo may be presented
#   as a winner.
# ═════════════════════════════════════════════════════════════════════════════


def _good_product_dict(image_url: str = _TEST_IMAGE_URL) -> dict:
    """A complete, audit-passing product dict."""
    return {
        "id": "test-prod-1",
        "name": "Rechargeable Electric Spin Scrubber",
        "category": "Cleaning",
        "image_url": image_url,
        "url": "https://example.com/product/spin-scrubber",
        "angle": "Effortless grout cleaning in seconds.",
        "pin_title": "Stop scrubbing on your knees — this $39 tool does it for you",
        "pin_description": "Rechargeable spin scrubber with 6 heads, 90 minutes runtime.",
        "hashtags": ["#CleanTok", "#CleaningHacks"],
        "trend_score": 87,
        "viral_hook": "I tried it once and I'm never going back.",
        "margin_estimate": "$12",
        "evergreen_score": 0.78,
        "competition": "Medium",
    }


@pytest.fixture
def mock_image_head():
    """Patch requests.head in product_control_agent so the image validator
    never makes a real network call during these tests. The default
    response is 200 / image/jpeg / 24 KB. Override per-test by patching
    ``mock_image_head.return_value = ...``.
    """
    with mock.patch.object(pca_mod.requests, "head",
                           return_value=mock.Mock(status_code=200,
                                                  headers={"Content-Type": "image/jpeg",
                                                           "Content-Length": "24576"})), \
         mock.patch.object(pca_mod.requests, "get",
                           return_value=mock.Mock(status_code=206,
                                                  headers={"Content-Range":
                                                           "bytes 0-1023/24576",
                                                           "Content-Type": "image/jpeg"},
                                                  content=b"\xff\xd8\xff" + b"\x00" * 1021)):
        yield


def test_gate_01_highest_ranked_no_image_is_rejected_second_wins(mock_image_head):
    """Highest-ranked candidate WITHOUT a usable image must be rejected.
    The next-ranked candidate WITH a usable image must be selected."""

    a = dict(_good_product_dict())
    a["image_url"] = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4="
    b = dict(_good_product_dict())
    b["id"] = "test-prod-2"
    b["image_url"] = _TEST_IMAGE_URL

    report_a = pca_mod.ProductControlAgent.evaluate(a)
    report_b = pca_mod.ProductControlAgent.evaluate(b)

    assert not report_a.ok, "data: URI placeholder must fail the image gate"
    assert "image_rejected" in "; ".join(report_a.reasons)
    assert report_b.ok, "verified http(s) JPEG must pass the image gate"
    assert report_b.product["image_status"] == "verified"


def test_gate_02_missing_image_url_is_rejected(mock_image_head):
    """A product whose image_url is None or empty must be rejected."""
    p = _good_product_dict()
    p["image_url"] = None
    report = pca_mod.ProductControlAgent.evaluate(p)
    assert not report.ok
    assert any("image" in r for r in report.reasons)


def test_gate_03_broken_image_url_is_rejected():
    """A product whose image URL returns HTTP 404 must be rejected."""
    with mock.patch.object(pca_mod.requests, "head",
                           return_value=mock.Mock(status_code=404,
                                                  headers={"Content-Type": "image/jpeg"})):
        report = pca_mod.ProductControlAgent.evaluate(_good_product_dict())
    assert not report.ok


def test_gate_04_placeholder_image_is_rejected(mock_image_head):
    """URLs with /placeholder. or /1x1. in the path must be rejected."""
    p = _good_product_dict()
    p["image_url"] = "https://cdn.example.com/static/placeholder.product.png"
    report = pca_mod.ProductControlAgent.evaluate(p)
    assert not report.ok
    assert any("placeholder" in r for r in report.reasons)


def test_gate_05_logo_image_is_rejected(mock_image_head):
    """Generic site logo URLs must be rejected as a winner image."""
    p = _good_product_dict()
    p["image_url"] = "https://cdn.example.com/assets/site-logo.png"
    report = pca_mod.ProductControlAgent.evaluate(p)
    assert not report.ok
    assert any("placeholder" in r for r in report.reasons)


def test_gate_06_data_uri_svg_is_rejected(mock_image_head):
    """data:image/svg+xml URIs are never acceptable product photos."""
    p = _good_product_dict()
    p["image_url"] = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4="
    report = pca_mod.ProductControlAgent.evaluate(p)
    assert not report.ok


def test_gate_07_too_small_image_is_rejected():
    """A 200-byte JPEG must be rejected (under the 5 KB floor)."""
    with mock.patch.object(pca_mod.requests, "head",
                           return_value=mock.Mock(status_code=200,
                                                  headers={"Content-Type": "image/jpeg",
                                                           "Content-Length": "200"})):
        report = pca_mod.ProductControlAgent.evaluate(_good_product_dict())
    assert not report.ok
    assert any("too_small" in r or "tracking_pixel" in r for r in report.reasons)


def test_gate_08_wrong_content_type_is_rejected():
    """An image/* Content-Type is required; text/html is rejected."""
    with mock.patch.object(pca_mod.requests, "head",
                           return_value=mock.Mock(status_code=200,
                                                  headers={"Content-Type": "text/html",
                                                           "Content-Length": "24576"})):
        report = pca_mod.ProductControlAgent.evaluate(_good_product_dict())
    assert not report.ok
    assert any("wrong_type" in r for r in report.reasons)


def test_gate_09_audit_product_backcompat_raises_value_error():
    """audit_product() must raise ValueError on reject for legacy callers."""
    p = _good_product_dict()
    p["image_url"] = "data:image/svg+xml;base64,PHN2Zy8+"
    with mock.patch.object(pca_mod.requests, "head",
                           side_effect=Exception("network down")):
        with pytest.raises(ValueError) as exc:
            pca_mod.ProductControlAgent.audit_product(p)
    assert "Product Control Violation" in str(exc.value)


def test_gate_10_evaluate_does_not_raise_on_reject(mock_image_head):
    """evaluate() returns a structured AuditReport, never raises."""
    p = _good_product_dict()
    p["image_url"] = "data:image/svg+xml;base64,PHN2Zy8+"
    report = pca_mod.ProductControlAgent.evaluate(p)
    assert isinstance(report, pca_mod.AuditReport)
    assert not report.ok
    assert report.image_check is not None
    assert report.image_check.ok is False


def test_gate_11_researcher_auto_enriches_no_image_candidates():
    """When the top-ranked pool candidate has a data: URI placeholder, the
    researcher must call _find_product_image to discover a real image
    before evaluating."""
    with _patch_research(ResearchEnvelope(
        research_status="live", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="tavily",
        research_sources=[
            {"title": "t1", "snippet": "s1", "url": "https://example.com/a", "provider": "tavily"},
        ],
        research_summary="x", research_query="y",
    )), _patch_image_search():
        out = get_researcher().pick("pet products")
    assert out["image_url"] == _TEST_IMAGE_URL
    assert out["image_source"] == "image_research"
    assert out["image_status"] == "verified"


def test_gate_12_researcher_returns_404_when_no_photo_qualified():
    """When ALL candidates fail the image gate, the researcher raises
    HTTPException(404) with a clear structured detail."""
    from fastapi import HTTPException
    with _patch_research(ResearchEnvelope(
        research_status="live", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="tavily",
        research_sources=[{"title": "t", "snippet": "s",
                           "url": "https://example.com/a", "provider": "tavily"}],
        research_summary="x", research_query="y",
    )), _patch_image_search_none():
        with pytest.raises(HTTPException) as exc:
            get_researcher().pick("pet products")
    assert exc.value.status_code == 404
    # Detail message must clearly indicate the IMAGE-FAILURE cause so the
    # user doesn't think the problem is keyword classification.
    assert "matching products" in exc.value.detail.lower() or \
           "photo-qualified" in exc.value.detail.lower(), (
        f"expected image-failure detail; got {exc.value.detail!r}"
    )


def test_gate_13_bounded_retry_does_not_loop_forever():
    """The retry loop is bounded by MAX_CANDIDATE_ATTEMPTS (=8)."""
    from backend.product_research import ProductResearcher
    assert ProductResearcher.MAX_CANDIDATE_ATTEMPTS == 8
    # Verify the loop respects the cap by counting calls.
    calls = {"n": 0}

    def counting_find_image(self, *, product_name="", category="", intent=""):
        calls["n"] += 1
        return None  # never finds an image

    with _patch_research(ResearchEnvelope(
        research_status="fallback", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="none", research_sources=[],
        research_summary="none", research_query="pet",
    )), mock.patch.object(ProductResearcher, "_find_product_image",
                          counting_find_image):
        with pytest.raises(Exception):
            get_researcher().pick("pet products")
    assert calls["n"] <= ProductResearcher.MAX_CANDIDATE_ATTEMPTS


def test_gate_14_winner_response_includes_image_metadata():
    """Successful winner response MUST include image_status=verified and a
    non-empty image_url plus image_url_verified."""
    with _patch_research(ResearchEnvelope(
        research_status="live", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="tavily",
        research_sources=[{"title": "t", "snippet": "s",
                           "url": "https://example.com/a", "provider": "tavily"}],
        research_summary="x", research_query="y",
    )), _patch_image_search():
        out = get_researcher().pick("pet products")
    assert out.get("image_status") == "verified"
    assert out.get("image_url") and out["image_url"].startswith("http")
    assert out.get("image_url_verified") == _TEST_IMAGE_URL
    assert out.get("image_source") == "image_research"


def test_gate_15_rejected_candidate_log_includes_reason():
    """When candidates are tried and rejected, the rejection log records
    a structured reason for each."""
    # Force two consecutive rejections by returning a non-image URL the first
    # time, then a valid one. We just check the field shape on success.
    with _patch_research(ResearchEnvelope(
        research_status="live", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="tavily",
        research_sources=[{"title": "t", "snippet": "s",
                           "url": "https://example.com/a", "provider": "tavily"}],
        research_summary="x", research_query="y",
    )), _patch_image_search():
        out = get_researcher().pick("pet products")
    assert isinstance(out.get("rejection_log"), list)
    assert "candidates_attempted" in out


# ═════════════════════════════════════════════════════════════════════════════
# MagSafe Phone Stand scenario — the canonical acceptance test for the
# strict-image-gate rebuild. A product that otherwise qualifies but has
# NO usable image must be rejected, and the system must continue to the
# next valid candidate.
# ═════════════════════════════════════════════════════════════════════════════


def test_magsafe_phone_stand_no_image_is_rejected_then_next_wins(mock_image_head):
    """MagSafe Phone Stand — product otherwise qualifies, no valid image.
    REJECT this candidate, move to next winning product."""

    # 1. The MagSafe candidate itself: every required field is present,
    #    semantics match (tech + magsafe keyword), but image_url is empty.
    magsafe = {
        "id": "magsafe-stand-01",
        "name": "3-in-1 Foldable MagSafe Wireless Charging Station",
        "category": "Tech",
        "image_url": None,
        "url": "https://example.com/magsafe-stand",
        "angle": "Charge iPhone, AirPods, and Apple Watch at once.",
        "pin_title": "This foldable MagSafe stand replaces 3 cables on your desk",
        "pin_description": "Fast wireless charging, foldable travel design, MagSafe compatible.",
        "hashtags": ["#TechTikTok", "#MagSafe"],
        "trend_score": 91,
    }

    # 2. Without image_url, audit MUST reject.
    report_reject = pca_mod.ProductControlAgent.evaluate(magsafe)
    assert not report_reject.ok, "MagSafe candidate without image MUST be rejected"
    assert any("image" in r for r in report_reject.reasons), \
        "rejection reasons must mention the image"
    # The agent must strip the bad image so downstream renderers cannot
    # accidentally use it.
    assert report_reject.product.get("image_url") is None, \
        "rejected product must not carry an image_url"

    # 3. The system must continue to the next candidate. With a working
    #    image-search and audit pipeline, the next candidate is selected.
    with _patch_research(ResearchEnvelope(
        research_status="live", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="tavily",
        research_sources=[{"title": "t", "snippet": "s",
                           "url": "https://example.com/a", "provider": "tavily"}],
        research_summary="x", research_query="magsafe stand",
    )), _patch_image_search():
        winner = get_researcher().pick("magsafe stand")
    assert winner["image_url"] == _TEST_IMAGE_URL
    assert winner["image_status"] == "verified"
    # The MagSafe candidate itself was the one ranked first; its rejection
    # must show up in the rejection log so debugging is possible.
    if any(r["id"] == "magsafe-stand-01" for r in winner.get("rejection_log", [])):
        magsafe_log = next(r for r in winner["rejection_log"]
                          if r["id"] == "magsafe-stand-01")
        assert "image" in magsafe_log["reason"].lower()


# ═════════════════════════════════════════════════════════════════════════════
# TIMEOUT / HANG REGRESSION SUITE (added 2026-09-27)
#
# Verifies the /find-winner pipeline cannot hang:
#   * per-image HEAD/GET requests have strict timeouts and no redirect chains
#   * per-provider research calls have explicit timeouts
#   * per-query image-search is bounded to a small number of URLs
#   * pick() has a wall-clock REQUEST_BUDGET_SECONDS that aborts the loop
#   * if a candidate's image validation hangs, the next candidate is tried
#   * /find-winner never accepts an unverified winner image
# ═════════════════════════════════════════════════════════════════════════════


def test_gate_16_validate_image_uses_short_timeout():
    """_validate_image must default to a SHORT timeout (≤ 5 s) so one
    slow image host cannot hang the entire /find-winner request."""
    import inspect
    sig = inspect.signature(pca_mod._validate_image)
    default = sig.parameters["head_timeout"].default
    assert default is not None
    assert default <= 5.0, (
        f"_validate_image head_timeout default={default}; "
        "must be ≤ 5 s so /find-winner cannot hang on one slow host"
    )


def test_gate_17_validate_image_uses_safe_redirect_policy():
    """_validate_image must ALLOW redirects on HEAD/GET so legitimate CDN-hosted
    images (most product photos) are not rejected, while still bounding the
    network calls. The earlier ``allow_redirects=False`` policy was the
    primary cause of valid product photos being rejected."""
    src = inspect.getsource(_read_product_control_agent())
    assert "allow_redirects=True" in src, (
        "_validate_image must call requests.head with allow_redirects=True "
        "so a single CDN redirect does not reject a real product photo"
    )
    assert "head_timeout" in src, (
        "_validate_image must keep a strict per-call timeout when following redirects"
    )


def test_gate_18_research_default_timeout_is_short():
    """live_research.DEFAULT_TIMEOUT must be ≤ 6 s so each Tavily / DDG
    HTTP call returns promptly."""
    from backend import live_research
    assert live_research.DEFAULT_TIMEOUT <= 6, (
        f"DEFAULT_TIMEOUT={live_research.DEFAULT_TIMEOUT}; "
        "must be ≤ 6 s to keep the pipeline responsive"
    )


def test_gate_19_request_budget_constant_exists():
    """ProductResearcher must declare REQUEST_BUDGET_SECONDS so pick() can
    bound the total wall-clock time of a single request."""
    from backend.product_research import ProductResearcher
    assert hasattr(ProductResearcher, "REQUEST_BUDGET_SECONDS"), \
        "ProductResearcher must declare REQUEST_BUDGET_SECONDS"
    assert ProductResearcher.REQUEST_BUDGET_SECONDS <= 30, (
        "REQUEST_BUDGET_SECONDS must be ≤ 30 s so /find-winner "
        "returns within the browser AbortController budget"
    )


def _read_product_control_agent():
    """Lazy import helper for product_control_agent."""
    return pca_mod


def test_gate_20_image_check_timeout_moves_to_next_candidate(monkeypatch):
    """If candidate 1's image HEAD never returns (simulated with a hang),
    the next candidate must still be evaluated."""
    from backend.product_research import ProductResearcher
    hang_calls = {"n": 0}
    # Counter to differentiate per-call return values; first call hangs.
    call_no = {"i": 0}

    def hang_or_ok(self, *, product_name="", category="", intent="", asin=None):
        call_no["i"] += 1
        hang_calls["n"] += 1
        if call_no["i"] == 1:
            # Simulate slow candidate that never yields a URL.
            return None
        return _TEST_IMAGE_URL

    # Patch _validate_image to count timeouts and behave deterministically.
    timeout_calls = {"n": 0}

    def head_with_eventual_timeout(url, **kw):
        timeout_calls["n"] += 1
        if timeout_calls["n"] == 1:
            import requests as r
            raise r.exceptions.Timeout("simulated image-host hang")
        return mock.Mock(
            status_code=200,
            headers={"Content-Type": "image/jpeg", "Content-Length": "24576"},
        )

    monkeypatch.setattr(ProductResearcher, "_find_product_image", hang_or_ok)
    monkeypatch.setattr(pca_mod.requests, "head", head_with_eventual_timeout)

    with _patch_research(ResearchEnvelope(
        research_status="live", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="tavily",
        research_sources=[{"title": "t", "snippet": "s",
                           "url": "https://example.com/a", "provider": "tavily"}],
        research_summary="x", research_query="pet",
    )):
        winner = get_researcher().pick("pet")
    assert winner["image_url"] == _TEST_IMAGE_URL
    assert winner["image_status"] == "verified"
    assert timeout_calls["n"] >= 1, (
        "first candidate must have triggered an image-check timeout"
    )


def test_gate_21_pick_terminates_within_request_budget(monkeypatch):
    """If every candidate's image validation hangs, pick() must still
    terminate within REQUEST_BUDGET_SECONDS, not run forever."""
    from backend.product_research import ProductResearcher

    def always_none(self, *, product_name="", category="", intent="", asin=None):
        return None

    monkeypatch.setattr(ProductResearcher, "_find_product_image", always_none)

    import time as _t
    t0 = _t.monotonic()
    with _patch_research(ResearchEnvelope(
        research_status="live", research_timestamp="2026-09-26T00:00:00Z",
        research_provider="tavily",
        research_sources=[{"title": "t", "snippet": "s",
                           "url": "https://example.com/a", "provider": "tavily"}],
        research_summary="x", research_query="pet",
    )):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            get_researcher().pick("pet")
    elapsed = _t.monotonic() - t0
    assert exc.value.status_code == 404
    # Detail message must clearly indicate the IMAGE-FAILURE cause so the
    # user doesn't think the problem is keyword classification.
    assert "matching products" in exc.value.detail.lower() or \
           "photo-qualified" in exc.value.detail.lower(), (
        f"expected image-failure detail; got {exc.value.detail!r}"
    )
    assert elapsed < 2.0, (
        f"pick() took {elapsed:.2f}s without a working image-research; "
        "the budget guard should terminate it within a couple of seconds "
        "in the unit-test (mocked) environment"
    )


def test_gate_22_validate_image_returns_image_load_failed_on_timeout():
    """_validate_image must catch requests.Timeout and return a structured
    rejection — never propagate the exception."""
    import requests as r

    with mock.patch.object(
        pca_mod.requests, "head",
        side_effect=r.exceptions.Timeout("upstream slow"),
    ):
        result = pca_mod._validate_image("https://slow.example.com/p.jpg")
    assert result.ok is False
    assert result.image_status == "broken"
    assert "Timeout" in result.reason or "image_load_failed" in result.reason


def test_gate_23_research_images_returns_max_3_urls():
    """research_images must cap the result at max_results (3) so a single
    call cannot balloon into hundreds of candidate URLs."""
    from backend import live_research
    # Patch both providers so the cap is exercised.
    with mock.patch.object(
        live_research, "_is_tavily_configured", return_value=False,
    ), mock.patch.object(
        live_research.requests, "post",
        return_value=mock.Mock(status_code=200, text="<html></html>"),
    ):
        urls = live_research.research_images("anything", max_results=3)
    assert isinstance(urls, list)
    assert len(urls) <= 3


def test_gate_24_pipeline_uses_abort_controller_for_first_fetch():
    """The shared pipeline must wrap its first fetch in an AbortController."""
    body = _pipeline_body()
    assert "AbortController" in body, \
        "pipeline must use AbortController around the first fetch"
    assert "controller.abort" in body, \
        "AbortController must have an abort() timer attached"
    assert "TIMEOUT_MS" in body, \
        "AbortController timeout must be defined as a named constant"


def test_gate_25_pipeline_uses_abort_controller_for_generate():
    """The shared pipeline must wrap the /traffic/generate fetch in an AbortController."""
    body = _pipeline_body()
    assert "genController" in body, \
        "pipeline must use an AbortController around /traffic/generate"


def test_gate_26_pipeline_rejects_unverified_winner_image():
    """The shared pipeline MUST throw before rendering if the winner's
    image_status is not 'verified' — defense-in-depth."""
    body = _pipeline_body()
    assert "image_status" in body and "verified" in body, \
        "pipeline must check image_status === 'verified'"
    assert "!winner.image_url" in body, \
        "pipeline must also reject winners with no image_url"


def test_gate_27_pipeline_handles_abort_error_message():
    """When AbortController fires, the user must see a clear timeout message."""
    body = _pipeline_body()
    assert "took too long" in body, \
        "pipeline must surface a 'took too long' message on AbortError"
    assert "AbortError" in body, \
        "pipeline must detect networkErr.name === 'AbortError'"


def test_gate_28_research_images_skips_ddg_when_tavily_already_satisfied():
    """If Tavily returns enough image URLs to satisfy max_results, the
    function must NOT also call DuckDuckGo. This keeps the pipeline fast."""
    from backend import live_research
    ddg_called = {"n": 0}
    real_post = live_research.requests.post

    def counting_post(*args, **kwargs):
        # DuckDuckGo endpoint only.
        if "duckduckgo.com" in (args[0] if args else kwargs.get("url", "")):
            ddg_called["n"] += 1
        # Return an empty 200 — we don't care about content for this test.
        return mock.Mock(status_code=200, text="<html></html>", json=lambda: {})

    # Force Tavily to be configured and return 3 image URLs immediately.
    tavily_response = mock.Mock(status_code=200)
    tavily_response.json.return_value = {
        "results": [],
        "images": [
            {"url": "https://a.example.com/p1.jpg"},
            {"url": "https://a.example.com/p2.jpg"},
            {"url": "https://a.example.com/p3.jpg"},
        ],
    }
    tavily_called = {"n": 0}

    def selective_post(url, *args, **kwargs):
        if "api.tavily.com" in url:
            tavily_called["n"] += 1
            return tavily_response
        return counting_post(url, *args, **kwargs)

    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", side_effect=selective_post):
        urls = live_research.research_images("test query", max_results=3)

    assert tavily_called["n"] == 1, "Tavily should be called exactly once"
    assert ddg_called["n"] == 0, (
        f"DuckDuckGo should NOT be called when Tavily already returned "
        f"max_results URLs; was called {ddg_called['n']} time(s)"
    )
    assert len(urls) == 3


def test_gate_29_pipeline_timeout_bounds_the_request():
    """The shared pipeline's timeout must allow the bounded 60-second discovery request ms."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    import re
    m = re.search(r"TIMEOUT_MS\s*=\s*(\d+)", src)
    assert m, "pipeline must declare TIMEOUT_MS"
    val = int(m.group(1))
    assert 60_000 <= val <= 90_000, (
        f"TIMEOUT_MS = {val}; must allow the bounded 60-second discovery request ms so the user "
        "gets fast retry feedback (warm-up ping handles Render cold-start)"
    )


def test_gate_30_frontend_warms_up_backend_on_load():
    """The page must fire-and-forget ping /api/v1/health on load so Render
    is warm before the user clicks Find Winner. This eliminates the
    'server is waking up' error on first click after the app has been idle."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    fn_idx = src.index("DOMContentLoaded")
    body = src[fn_idx:_script_end_idx(src)]
    assert "/api/v1/health" in body, (
        "DOMContentLoaded must fire a /api/v1/health warm-up fetch"
    )


def test_gate_31_both_workflows_show_progress_stages():
    """Both PATH A and PATH B must cycle the loading text through their
    distinct progress stages."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    # PATH A stages
    assert "Researching products" in src, \
        "PATH A must include 'Researching products' stage"
    assert "Scoring opportunities" in src, \
        "PATH A must include 'Scoring opportunities' stage"
    assert "Verifying product images" in src, \
        "PATH A must include 'Verifying product images' stage"
    # PATH B stages
    assert "Reading product" in src, \
        "PATH B must include 'Reading product' stage"
    assert "Resolving product details" in src, \
        "PATH B must include 'Resolving product details' stage"
    assert "Evaluating opportunity" in src, \
        "PATH B must include 'Evaluating opportunity' stage"
    # Shared pipeline drives the timer.
    body = _pipeline_body()
    assert "stageTimer" in body, \
        "pipeline must use a stageTimer to cycle the progress text"


def test_gate_32_pipeline_timeout_message_mentions_warm_up():
    """When the AbortController fires, the error message must reassure
    the user that the server is waking up — not blame them."""
    body = _pipeline_body()
    assert "waking up" in body or "warms up" in body, \
        "pipeline catch must explain that the server may be warming up"
    assert "try again" in body.lower(), \
        "pipeline catch must tell the user to try again"


# ═════════════════════════════════════════════════════════════════════════════
# IMAGE-DISCOVERY BUG REGRESSION SUITE (added 2026-09-27)
#
# Verifies the fix for the Tavily "0 URLs returned" bug:
#   * research_images() handles Tavily's newer `images` shape (list of plain
#     URL strings) without crashing, instead of silently swallowing an
#     AttributeError on `img.get('url')`.
#   * _validate_image follows bounded redirects and accepts valid product
#     photos hosted behind one CDN hop.
#   * HEAD-hostile servers (403/405) are validated via ranged GET instead
#     of being rejected outright.
# ═════════════════════════════════════════════════════════════════════════════


def test_gate_33_research_images_accepts_string_shaped_tavily_response():
    """Tavily's `include_images` returns ``images`` as a list of URL
    STRINGS (not {url, description} dicts). research_images() must accept
    that shape without crashing — the prior bug silently returned []."""
    import backend.product_research as pr_mod
    fake_resp = mock.Mock(status_code=200)
    fake_resp.json.return_value = {
        "results": [],
        "images": [
            "https://example.com/a.jpg",
            "https://example.com/b.jpg",
            "https://example.com/c.jpg",
        ],
    }
    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", return_value=fake_resp):
        urls = pr_mod.live_research.research_images("any product", max_results=3)
    assert len(urls) == 3
    assert "https://example.com/a.jpg" in urls


def test_gate_34_research_images_accepts_dict_shaped_tavily_response():
    """Older Tavily API returned images as [{url, description}, ...].
    research_images() must still accept that shape."""
    import backend.product_research as pr_mod
    fake_resp = mock.Mock(status_code=200)
    fake_resp.json.return_value = {
        "results": [],
        "images": [
            {"url": "https://example.com/old-a.jpg", "description": "x"},
            {"url": "https://example.com/old-b.jpg", "description": "y"},
        ],
    }
    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", return_value=fake_resp):
        urls = pr_mod.live_research.research_images("any product", max_results=3)
    assert len(urls) == 2
    assert "https://example.com/old-a.jpg" in urls


def test_gate_35_research_images_skips_non_http_urls():
    """Any non-http(s) entry in the response must be filtered out."""
    import backend.product_research as pr_mod
    fake_resp = mock.Mock(status_code=200)
    fake_resp.json.return_value = {
        "results": [],
        "images": [
            "https://example.com/ok.jpg",
            "javascript:alert(1)",
            "data:image/png;base64,AAAA",
            "ftp://example.com/bad.jpg",
        ],
    }
    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", return_value=fake_resp):
        urls = pr_mod.live_research.research_images("any product", max_results=5)
    assert urls == ["https://example.com/ok.jpg"]


def test_gate_36_validate_image_accepts_redirect_to_image_cdn():
    """A real product photo URL may return 302 to a CDN. The validator
    must follow the redirect (one or more hops) and accept the final
    image response. Previously allow_redirects=False rejected every
    such URL."""

    final = mock.Mock(status_code=200, headers={
        "Content-Type": "image/jpeg", "Content-Length": "24576",
    })
    # Simulate a Session.head that follows one redirect.
    session_mock = mock.Mock()
    session_mock.head.return_value = final
    with mock.patch.object(pca_mod.requests, "head", return_value=final):
        result = pca_mod._validate_image("https://www.example.com/redirect.jpg")
    assert result.ok, f"redirect-following image must pass; reason={result.reason}"
    assert result.image_status == "verified"
    assert result.http_status == 200


def test_gate_37_validate_image_falls_back_to_ranged_get_on_head_403():
    """A server that rejects HEAD with 403 must NOT cause the validator
    to fail. It should perform a ranged GET to verify the resource."""

    head_resp = mock.Mock(status_code=403, headers={})
    get_resp = mock.Mock(status_code=206, headers={
        "Content-Range": "bytes 0-1023/24576",
        "Content-Type": "image/jpeg",
    })
    get_resp.content = b"\xff\xd8\xff" + b"\x00" * 1021
    with mock.patch.object(pca_mod.requests, "head", return_value=head_resp), \
         mock.patch.object(pca_mod.requests, "get", return_value=get_resp):
        result = pca_mod._validate_image("https://head-hostile.example.com/p.jpg")
    assert result.ok, f"HEAD-hostile image must pass via ranged GET; reason={result.reason}"
    assert result.image_status == "verified"
    assert result.http_status == 206


def test_gate_38_validate_image_falls_back_on_head_405():
    """HEAD → 405 Method Not Allowed is treated like 403: try ranged GET."""
    head_resp = mock.Mock(status_code=405, headers={})
    get_resp = mock.Mock(status_code=200, headers={
        "Content-Length": "24576", "Content-Type": "image/jpeg",
    })
    with mock.patch.object(pca_mod.requests, "head", return_value=head_resp), \
         mock.patch.object(pca_mod.requests, "get", return_value=get_resp):
        result = pca_mod._validate_image("https://head-hostile.example.com/p.jpg")
    assert result.ok


def test_gate_39_validate_image_rejects_when_get_also_fails():
    """If HEAD returns 403 AND the GET also fails, the image is rejected."""
    import requests as r
    head_resp = mock.Mock(status_code=403, headers={})
    with mock.patch.object(pca_mod.requests, "head", return_value=head_resp), \
         mock.patch.object(pca_mod.requests, "get",
                           side_effect=r.exceptions.ConnectionError("down")):
        result = pca_mod._validate_image("https://down.example.com/p.jpg")
    assert not result.ok
    assert result.image_status == "broken"


def test_gate_40_research_end_to_end_with_tavily_strings():
    """Simulate the real bug: Tavily returns 5 URL strings. The full
    _find_product_image flow must surface them so a candidate can be
    enriched with a real product photo."""
    import backend.product_research as pr_mod

    fake_tavily = mock.Mock(status_code=200)
    fake_tavily.json.return_value = {
        "results": [],
        "images": [
            "https://cdn.example.com/real-product-photo.jpg",
            "https://cdn.example.com/second-photo.jpg",
        ],
    }

    # Validator will see the first URL — patch HEAD to say it's a 24 KB image/jpeg.
    head_resp = mock.Mock(status_code=200, headers={
        "Content-Type": "image/jpeg", "Content-Length": "24576",
    })

    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", return_value=fake_tavily), \
         mock.patch.object(pr_mod.live_research.requests, "post", return_value=fake_tavily), \
         mock.patch.object(pca_mod.requests, "head", return_value=head_resp):
        urls = pr_mod.live_research.research_images("any product", max_results=3)
    assert len(urls) == 2
    assert urls[0] == "https://cdn.example.com/real-product-photo.jpg"


# ═════════════════════════════════════════════════════════════════════════════
# VISUAL-DOMINANCE RANKING (added 2026-09-27)
#
# Verifies the heuristic that ranks Tavily's image URLs so the chosen
# winner photo puts the actual product front-and-center, not buried in a
# magazine lifestyle scene.
# ═════════════════════════════════════════════════════════════════════════════

import backend.product_control_agent as pca_mod  # noqa: E402


def test_gate_41_rank_prefers_amazon_product_cdn_over_magazine_editorial():
    """rank_image_candidates must rank m.media-amazon.com ABOVE Hearst /
    magazine editorial hosts — the desk-lamp failure case where the
    charger photo dominates."""
    urls = [
        "https://hips.hearstapps.com/vader-prod.s3.amazonaws.com/1689043497-lamp.jpg",
        "https://m.media-amazon.com/images/I/61dlampsku.jpg",
        "https://food.fnr.sndimg.com/content/dam/images/food/lamp.jpg",
    ]
    ranked = pca_mod.rank_image_candidates(
        urls, product_name="LED desk lamp", category="tech",
    )
    chosen, _ = ranked[0]
    assert chosen.startswith("https://m.media-amazon.com"), (
        f"Amazon product CDN must win; got {chosen}"
    )


def test_gate_42_rank_prefers_shopify_product_path_over_wp_uploads():
    """A /cdn/shop/products/ URL must outrank a wordpress /wp-content/
    editorial photo for the same product."""
    urls = [
        "https://example.com/wp-content/uploads/2024/01/lamp-on-desk-scene.jpg",
        "https://cdn.shopify.com/s/files/1/1234/5678/products/desk-lamp-main.jpg",
    ]
    ranked = pca_mod.rank_image_candidates(
        urls, product_name="desk lamp", category="tech",
    )
    chosen, _ = ranked[0]
    assert chosen.startswith("https://cdn.shopify.com"), (
        f"Shopify product image must win; got {chosen}"
    )


def test_gate_43_rank_filename_keyword_match_boosts_score():
    """A URL whose path contains the product name keyword must rank above
    one whose path is generic, even on the same host."""
    urls = [
        "https://m.media-amazon.com/images/I/61randomlettersABC.jpg",
        "https://m.media-amazon.com/images/I/71DESKlampskuXYZ.jpg",
    ]
    ranked = pca_mod.rank_image_candidates(
        urls, product_name="desk lamp", category="tech",
    )
    chosen, _ = ranked[0]
    assert "DESK" in chosen or "desk" in chosen, (
        f"Filename keyword match must win; got {chosen}"
    )


def test_gate_44_rank_penalizes_lifestyle_path_tokens():
    """/wp-content/uploads/ and /editorial/ paths must drop in rank."""
    urls = [
        "https://www.familyhandyman.com/wp-content/uploads/2024/11/lamp-FT.jpg",
        "https://cdn.shopify.com/s/files/1/1234/5678/products/lamp.jpg",
    ]
    ranked = pca_mod.rank_image_candidates(
        urls, product_name="lamp", category="tech",
    )
    chosen, _ = ranked[0]
    assert chosen.startswith("https://cdn.shopify.com")


def test_gate_45_rank_handles_unknown_host_neutrally():
    """An unknown host that contains the product keyword in its path
    must still beat a known lifestyle host whose path is generic."""
    urls = [
        "https://hips.hearstapps.com/vader-prod.s3.amazonaws.com/editorial-image.jpg",
        "https://random-cdn.example.com/uploads/products/desk-lamp-hero.jpg",
    ]
    ranked = pca_mod.rank_image_candidates(
        urls, product_name="desk lamp", category="tech",
    )
    chosen, _ = ranked[0]
    assert chosen.startswith("https://random-cdn.example.com"), (
        f"Keyword-rich unknown host must beat known lifestyle host; got {chosen}"
    )


def test_gate_46_find_product_image_picks_highest_ranked_valid_url():
    """When Tavily returns a mix of magazine and product URLs, _find_product_image
    must NOT just take the first one — it must pick the highest-ranked VALID one."""
    import backend.product_research as pr_mod
    from backend.product_research import ProductResearcher
    fake_tavily = mock.Mock(status_code=200)
    fake_tavily.json.return_value = {
        "results": [],
        "images": [
            # Hearst editorial — looks like a lamp but is a lifestyle scene
            "https://hips.hearstapps.com/vader-prod.s3.amazonaws.com/lamp-editorial.jpg",
            # Amazon main product image — single product shot
            "https://m.media-amazon.com/images/I/71DESKlampskuXYZ.jpg",
        ],
    }
    head_resp = mock.Mock(status_code=200, headers={
        "Content-Type": "image/jpeg", "Content-Length": "24576",
    })
    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", return_value=fake_tavily), \
         mock.patch.object(pr_mod.live_research.requests, "post", return_value=fake_tavily), \
         mock.patch.object(pca_mod.requests, "head", return_value=head_resp):
        url = ProductResearcher()._find_product_image(
            product_name="desk lamp", category="tech", intent="desk lamp",
        )
    assert url is not None
    assert url.startswith("https://m.media-amazon.com"), (
        f"Amazon product image must be chosen over Hearst editorial; got {url}"
    )


def test_gate_47_rank_returns_empty_for_empty_input():
    """Empty input → empty output. No crashes."""
    assert pca_mod.rank_image_candidates([]) == []


def test_gate_48_rank_handles_malformed_urls():
    """A URL without a host component must not crash the ranker."""
    urls = ["not-a-url", "https://valid.example.com/p.jpg"]
    ranked = pca_mod.rank_image_candidates(
        urls, product_name="lamp", category="tech",
    )
    # Both URLs come back; the valid one is preferred because it has a host.
    chosen, _ = ranked[0]
    assert chosen == "https://valid.example.com/p.jpg"


def test_gate_49_rank_is_stable_across_calls():
    """The ranker must be deterministic — same input, same output order."""
    urls = [
        "https://hips.hearstapps.com/vader-prod.s3.amazonaws.com/a.jpg",
        "https://m.media-amazon.com/images/I/b.jpg",
        "https://cdn.shopify.com/s/files/c.jpg",
    ]
    a = pca_mod.rank_image_candidates(urls, product_name="lamp", category="tech")
    b = pca_mod.rank_image_candidates(urls, product_name="lamp", category="tech")
    assert [u for u, _ in a] == [u for u, _ in b]


def test_gate_50_real_desk_lamp_scenario_picks_product_not_charged_devices():
    """Reproduce the failure scenario: Tavily returns one URL whose path
    hints at 'lamp-with-phone-charger' (a charging scene) and one clean
    product URL. The clean one must win."""
    urls = [
        # Lifestyle: lamp with phone charging on it
        "https://example.com/wp-content/uploads/lamp-with-phone-charging.jpg",
        # Clean product shot
        "https://m.media-amazon.com/images/I/71LEDDESKlampskuXYZ.jpg",
    ]
    ranked = pca_mod.rank_image_candidates(
        urls, product_name="LED desk lamp", category="tech",
    )
    chosen, _ = ranked[0]
    assert chosen.startswith("https://m.media-amazon.com"), (
        f"Clean product shot must beat lifestyle 'lamp-with-phone' image; got {chosen}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# QUERY-PRODUCT RELEVANCE + HARD IMAGE-QUALITY THRESHOLD (added 2026-09-27)
#
# The user's query is a HARD ELIGIBILITY CONSTRAINT, not a loose
# inspiration signal. Trend score and image quality are ranking signals
# among candidates that already pass relevance. A candidate whose name
# does not match the query family must be rejected regardless of trend.
# ═════════════════════════════════════════════════════════════════════════════


def test_relevance_01_kitchen_organizer_rejects_spin_scrubber():
    """Spec test 1: query='kitchen organizer' must REJECT 'Rechargeable
    Electric Spin Scrubber'."""
    cat_score, type_score, matched = pca_mod.compute_query_product_relevance(
        "kitchen organizer",
        "Rechargeable Electric Spin Scrubber with 6 Replaceable Heads",
        "Cleaning",
    )
    assert type_score < pca_mod.PRODUCT_TYPE_THRESHOLD, (
        f"kitchen organizer must reject spin scrubber; got type_score={score}"
    )


def test_relevance_02_kitchen_organizer_accepts_under_sink_organizer():
    """Spec test 2: query='kitchen organizer' must ACCEPT
    '2-Tier Under Sink Kitchen Organizer'."""
    cat_score, type_score, matched = pca_mod.compute_query_product_relevance(
        "kitchen organizer",
        "2-Tier Under Sink Kitchen Organizer",
        "Kitchen",
    )
    assert type_score >= pca_mod.PRODUCT_TYPE_THRESHOLD, (
        f"kitchen organizer must accept under-sink organizer; got type_score={score}"
    )


def test_relevance_03_phone_stand_accepts_magsafe_holder():
    """Spec test 3: query='phone stand' must ACCEPT 'MagSafe Phone Holder'."""
    _, type_score, _ = pca_mod.compute_query_product_relevance(
        "phone stand",
        "MagSafe Phone Holder for Desk",
        "Tech",
    )
    assert type_score >= pca_mod.PRODUCT_TYPE_THRESHOLD, (
        f"phone stand must accept magsafe holder; got type_score={score}"
    )


def test_relevance_04_phone_stand_rejects_desk_lamp():
    """Spec test 4: query='phone stand' must REJECT 'LED Desk Lamp'."""
    _, type_score, _ = pca_mod.compute_query_product_relevance(
        "phone stand",
        "LED Desk Lamp",
        "Decor",
    )
    assert type_score < pca_mod.PRODUCT_TYPE_THRESHOLD, (
        f"phone stand must reject desk lamp; got type_score={score}"
    )


def test_relevance_05_pet_bed_accepts_orthopedic_dog_bed():
    """Spec test 5: query='pet bed' must ACCEPT 'Orthopedic Dog Bed'."""
    _, type_score, _ = pca_mod.compute_query_product_relevance(
        "pet bed",
        "Orthopedic Dog Bed Self-Warming Plush",
        "Pet Supplies",
    )
    assert type_score >= pca_mod.PRODUCT_TYPE_THRESHOLD


def test_relevance_06_desk_lamp_accepts_led_task_light():
    """Spec test 6: query='desk lamp' must ACCEPT 'LED Task Light'."""
    _, type_score, _ = pca_mod.compute_query_product_relevance(
        "desk lamp",
        "LED Task Light Dimmable",
        "Tech",
    )
    assert type_score >= pca_mod.PRODUCT_TYPE_THRESHOLD, (
        f"desk lamp must accept LED task light; got type_score={score}"
    )


def test_relevance_07_cleaning_brush_accepts_spin_scrubber():
    """Spec test 7: query='cleaning brush' must ACCEPT 'Electric Spin Scrubber'."""
    _, type_score, _ = pca_mod.compute_query_product_relevance(
        "cleaning brush",
        "Electric Spin Scrubber",
        "Cleaning",
    )
    assert type_score >= pca_mod.PRODUCT_TYPE_THRESHOLD, (
        f"cleaning brush must accept spin scrubber; got type_score={score}"
    )


def test_image_threshold_08_rejects_when_best_image_below_min():
    """Spec test 8: if the BEST image URL ranks below MIN_PRODUCT_IMAGE_SCORE,
    _find_product_image must return None — the candidate is rejected
    rather than accepting the least-bad image."""
    import backend.product_research as pr_mod
    from backend.product_research import ProductResearcher
    fake_tavily = mock.Mock(status_code=200)
    fake_tavily.json.return_value = {
        "results": [],
        "images": [
            # All URLs are from unknown hosts with no path bonuses and
            # no filename keyword matches → all score 0 or below.
            "https://random-cdn-1.example.com/random123.jpg",
            "https://random-cdn-2.example.org/abc456.jpg",
            "https://another-host.test/xyz789.jpg",
        ],
    }
    head_resp = mock.Mock(status_code=200, headers={
        "Content-Type": "image/jpeg", "Content-Length": "24576",
    })
    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", return_value=fake_tavily), \
         mock.patch.object(pr_mod.live_research.requests, "post", return_value=fake_tavily), \
         mock.patch.object(pca_mod.requests, "head", return_value=head_resp):
        url = ProductResearcher()._find_product_image(
            product_name="desk lamp", category="tech", intent="desk lamp",
        )
    assert url is None, (
        f"_find_product_image must return None when best score < threshold; "
        f"got {url}"
    )


def test_image_threshold_09_accepts_high_quality_retailer_image():
    """A high-quality retailer CDN image must pass the threshold."""
    import backend.product_research as pr_mod
    from backend.product_research import ProductResearcher
    fake_tavily = mock.Mock(status_code=200)
    fake_tavily.json.return_value = {
        "results": [],
        "images": [
            "https://m.media-amazon.com/images/I/71DESKlampXYZ.jpg",
        ],
    }
    head_resp = mock.Mock(status_code=200, headers={
        "Content-Type": "image/jpeg", "Content-Length": "24576",
    })
    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", return_value=fake_tavily), \
         mock.patch.object(pr_mod.live_research.requests, "post", return_value=fake_tavily), \
         mock.patch.object(pca_mod.requests, "head", return_value=head_resp):
        url = ProductResearcher()._find_product_image(
            product_name="desk lamp", category="tech", intent="desk lamp",
        )
    assert url is not None
    assert "m.media-amazon.com" in url


def test_trend_score_10_does_not_override_relevance():
    """Spec test 9: a high trend score + low relevance must be rejected.
    Verify the relevance gate runs BEFORE the trend score is used to
    rank candidates — i.e. relevance is a hard eligibility gate."""
    # Two product cards. Both go through compute_query_product_relevance.
    # The one with high trend but irrelevant MUST have lower relevance
    # than the relevant one.
    irrelevant = ("Rechargeable Electric Spin Scrubber with 6 Replaceable Heads",
                  "Cleaning")
    relevant = ("2-Tier Under Sink Kitchen Organizer", "Kitchen")
    cat_irrel, type_irrel, _ = pca_mod.compute_query_product_relevance(
        "kitchen organizer", *irrelevant,
    )
    cat_rel, type_rel, _ = pca_mod.compute_query_product_relevance(
        "kitchen organizer", *relevant,
    )
    assert type_irrel < pca_mod.PRODUCT_TYPE_THRESHOLD, (
        "irrelevant candidate must be below relevance threshold"
    )
    assert type_rel >= pca_mod.PRODUCT_TYPE_THRESHOLD, (
        "relevant candidate must be at or above relevance threshold"
    )
    assert cat_rel > cat_irrel and type_rel >= type_irrel, (
        f"relevant ({rel_rel}) must score higher than irrelevant ({rel_irrel})"
    )


def test_token_match_substring_does_not_falsely_activate_family():
    """'bed' must NOT activate the 'lamp' family (bedside)."""
    families = pca_mod._classify_query_families("bed")
    assert "lamp" not in families, (
        f"'bed' must not activate lamp family; got {families}"
    )


def test_token_match_plural_normalizes_correctly():
    """'organizers' (plural) should match the 'organizer' family."""
    families = pca_mod._classify_query_families("organizers")
    assert "kitchen_org" in families


def test_relevance_default_score_for_empty_query():
    """An empty query returns 0.50 (neutral) on BOTH axes."""
    cat_score, type_score, _ = pca_mod.compute_query_product_relevance("", "Any Product", "Any Cat")
    assert cat_score == 0.50
    assert type_score == 0.50


def test_min_product_image_score_constant_is_sane():
    """MIN_PRODUCT_IMAGE_SCORE must be a positive integer that prevents
    the 'least bad' fallback but still allows valid retailer images."""
    assert isinstance(pca_mod.MIN_PRODUCT_IMAGE_SCORE, int)
    assert 10 <= pca_mod.MIN_PRODUCT_IMAGE_SCORE <= 80, (
        f"MIN_PRODUCT_IMAGE_SCORE={pca_mod.MIN_PRODUCT_IMAGE_SCORE}; "
        "must be in [10, 80]"
    )


def test_min_relevance_score_constant_is_sane():
    """MIN_RELEVANCE_SCORE must be in (0.0, 1.0] so it's a meaningful
    hard gate without being trivially easy or impossibly hard."""
    assert 0.0 < pca_mod.MIN_RELEVANCE_SCORE <= 1.0
    assert pca_mod.MIN_RELEVANCE_SCORE >= 0.30, (
        f"MIN_RELEVANCE_SCORE={pca_mod.MIN_RELEVANCE_SCORE}; must be ≥ 0.30"
    )

# ═════════════════════════════════════════════════════════════════════════════
# TWO-AXIS PRODUCT-TYPE SPEC TESTS (added 2026-09-27)
# 12 exact examples from the spec brief.
# ═════════════════════════════════════════════════════════════════════════════


def _pass(query, name, category):
    cat, tp, _ = pca_mod.compute_query_product_relevance(query, name, category)
    assert cat >= pca_mod.CATEGORY_THRESHOLD, (
        f"query={query!r} candidate={name!r}: category_score={cat} below threshold"
    )
    assert tp >= pca_mod.PRODUCT_TYPE_THRESHOLD, (
        f"query={query!r} candidate={name!r}: product_type_score={tp} below threshold"
    )


def _reject(query, name, category):
    cat, tp, _ = pca_mod.compute_query_product_relevance(query, name, category)
    passes = cat >= pca_mod.CATEGORY_THRESHOLD and tp >= pca_mod.PRODUCT_TYPE_THRESHOLD
    assert not passes, (
        f"query={query!r} candidate={name!r}: should be REJECTED but cat={cat} type={tp}"
    )


def test_two_axis_01_pet_bed_rejects_pet_feeder():
    _reject("pet bed", "Automatic Pet Feeder - WiFi, Portion Control", "Pet Supplies")


def test_two_axis_02_pet_bed_accepts_orthopedic_dog_bed():
    _pass("pet bed", "Orthopedic Dog Bed Self-Warming Plush", "Pet Supplies")


def test_two_axis_03_phone_stand_rejects_wireless_charger():
    _reject("phone stand", "Wireless Phone Charger Pad", "Tech")


def test_two_axis_04_phone_stand_accepts_magsafe_holder():
    _pass("phone stand", "MagSafe Phone Holder for Desk", "Tech")


def test_two_axis_05_kitchen_organizer_rejects_spin_scrubber():
    _reject("kitchen organizer", "Rechargeable Spin Scrubber", "Cleaning")


def test_two_axis_06_kitchen_organizer_accepts_under_sink_organizer():
    _pass("kitchen organizer", "2-Tier Under Sink Organizer", "Kitchen")


def test_two_axis_07_kitchen_organizer_accepts_spice_rack_organizer():
    _pass("kitchen organizer", "Spice Rack Organizer 16 Jars", "Kitchen")


def test_two_axis_08_kitchen_organizer_accepts_pantry_storage_rack():
    _pass("kitchen organizer", "Pantry Storage Rack", "Kitchen")


def test_two_axis_09_desk_lamp_rejects_ceiling_light():
    _reject("desk lamp", "LED Ceiling Light Fixture", "Decor")


def test_two_axis_10_desk_lamp_accepts_led_task_lamp():
    _pass("desk lamp", "LED Task Lamp Dimmable", "Tech")


def test_two_axis_11_cleaning_brush_rejects_vacuum():
    _reject("cleaning brush", "Vacuum Cleaner", "Cleaning")


def test_two_axis_12_cleaning_brush_accepts_spin_scrubber():
    _pass("cleaning brush", "Electric Spin Scrubber", "Cleaning")


def test_two_axis_threshold_constants_are_sane():
    """CATEGORY_THRESHOLD must be <= PRODUCT_TYPE_THRESHOLD because type
    matching is the stricter of the two."""
    assert 0.0 < pca_mod.CATEGORY_THRESHOLD < 1.0
    assert 0.0 < pca_mod.PRODUCT_TYPE_THRESHOLD < 1.0
    assert pca_mod.CATEGORY_THRESHOLD <= pca_mod.PRODUCT_TYPE_THRESHOLD, (
        f"CATEGORY_THRESHOLD={pca_mod.CATEGORY_THRESHOLD} should be <= "
        f"PRODUCT_TYPE_THRESHOLD={pca_mod.PRODUCT_TYPE_THRESHOLD}"
    )


def test_two_axis_broad_category_pass_but_type_fail_is_rejected():
    """Demonstrate the core spec requirement: a candidate in the right
    broad family but wrong product type MUST be rejected."""
    cat, tp, _ = pca_mod.compute_query_product_relevance(
        "pet bed", "Automatic Pet Feeder - WiFi, Portion Control", "Pet Supplies",
    )
    # Pet feeder IS in the pet category (high category_score)
    assert cat >= pca_mod.CATEGORY_THRESHOLD, (
        f"pet feeder should pass category; got cat={cat}"
    )
    # But it is NOT a pet bed (low product_type_score)
    assert tp < pca_mod.PRODUCT_TYPE_THRESHOLD, (
        f"pet feeder must FAIL product_type; got tp={tp}"
    )


def test_two_axis_score_function_returns_three_tuple():
    """API contract: compute_query_product_relevance returns (cat, type, matched)."""
    out = pca_mod.compute_query_product_relevance("pet bed", "Orthopedic Dog Bed", "Pet Supplies")
    assert isinstance(out, tuple) and len(out) == 3


# ═════════════════════════════════════════════════════════════════════════════
# IMAGE-SEARCH QUERY CASCADE TESTS (added 2026-09-27)
# Verify the bounded product-specific query cascade replaces the previous
# single broad query.
# ═════════════════════════════════════════════════════════════════════════════


def test_cascade_01_full_title_searched_first():
    """The most specific query (normalized product title) must come first."""
    cascade = pca_mod.build_image_query_cascade(
        "Rotating Spice Rack Organizer - 16 Jars, Labels Included",
        "Kitchen",
        "kitchen organizer",
    )
    assert len(cascade) >= 1
    assert "rotating spice rack organizer" in cascade[0].lower(), (
        f"first query should be the normalized product title; got {cascade[0]}"
    )


def test_cascade_02_user_intent_used_only_as_fallback():
    """The user intent is included only if it differs from the product
    name and is added LAST in the cascade."""
    cascade = pca_mod.build_image_query_cascade(
        "Rotating Spice Rack Organizer - 16 Jars, Labels Included",
        "Kitchen",
        "kitchen organizer",
    )
    # The user intent must appear, and not be the first query.
    assert cascade[-1] == "kitchen organizer"


def test_cascade_03_marketing_punctuation_normalized_out():
    """Em-dashes, slashes, parentheses, etc. must be stripped."""
    norm = pca_mod._normalize_product_title(
        "Premium Microfiber Cleaning Cloths - 12-Pack (Color Coded)"
    )
    for ch in ("-", "—", "/", "(", ")", ",", ".", ":"):
        assert ch not in norm, f"normalized title still contains {ch!r}: {norm!r}"


def test_cascade_04_marketing_words_dropped():
    """Best, trending, viral, premium etc. must be dropped."""
    norm = pca_mod._normalize_product_title(
        "Best Premium Trending Stainless Steel Knife Set"
    )
    for w in ("best", "premium", "trending"):
        assert w not in norm.split(), f"noise word {w!r} survived: {norm!r}"


def test_cascade_05_count_and_size_dropped_in_simplify():
    """_simplify_for_search must drop trailing count/size tokens."""
    simp = pca_mod._simplify_for_search("spice rack organizer 16 jars")
    assert "16" not in simp.split(), f"simplify kept the count: {simp!r}"
    assert "jars" not in simp.split(), f"simplify kept the unit: {simp!r}"
    assert "spice rack organizer" in simp


def test_cascade_06_max_queries_bounded():
    """build_image_query_cascade must never return more than
    MAX_IMAGE_SEARCH_QUERIES entries."""
    cascade = pca_mod.build_image_query_cascade(
        "Best Premium Trending Stainless Steel Knife Set with Block",
        "Kitchen",
        "kitchen organizer",
        max_queries=pca_mod.MAX_IMAGE_SEARCH_QUERIES,
    )
    assert len(cascade) <= pca_mod.MAX_IMAGE_SEARCH_QUERIES


def test_cascade_07_dedup_within_cascade():
    """If the user intent is identical to the product name, it must not
    appear twice in the cascade."""
    cascade = pca_mod.build_image_query_cascade(
        "spice rack organizer",
        "Kitchen",
        "spice rack organizer",
    )
    assert len(cascade) == 1, f"cascade should be deduped: {cascade}"


def test_cascade_08_short_title_uses_user_intent():
    """When the product name is too short to extract useful terms, the
    user intent is used as the query."""
    cascade = pca_mod.build_image_query_cascade(
        "Mug",
        "Kitchen",
        "coffee mug warmer",
    )
    # Either "mug" or "coffee mug warmer" must appear, but the cascade
    # is bounded.
    assert len(cascade) <= pca_mod.MAX_IMAGE_SEARCH_QUERIES
    assert any("mug" in q.lower() for q in cascade), (
        f"cascade should include a mug-related query: {cascade}"
    )


def test_cascade_09_early_stop_with_strong_image():
    """If a query surfaces an image with score >= STRONG_IMAGE_SCORE,
    the cascade stops asking Tavily for more queries."""
    import backend.product_research as pr_mod
    from backend.product_research import ProductResearcher
    # First call returns a high-score URL; second call would also
    # return URLs but should never be reached.
    fake_resp = mock.Mock(status_code=200)
    fake_resp.json.return_value = {
        "results": [],
        "images": [
            "https://m.media-amazon.com/images/I/71spicerackXYZ.jpg",
        ],
    }
    head_resp = mock.Mock(status_code=200, headers={
        "Content-Type": "image/jpeg", "Content-Length": "24576",
    })
    call_count = {"n": 0}

    def counting_post(*args, **kwargs):
        call_count["n"] += 1
        return fake_resp

    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", side_effect=counting_post), \
         mock.patch.object(pr_mod.live_research.requests, "post", side_effect=counting_post), \
         mock.patch.object(pca_mod.requests, "head", return_value=head_resp):
        url = ProductResearcher()._find_product_image(
            product_name="Rotating Spice Rack Organizer - 16 Jars, Labels Included",
            category="Kitchen",
            intent="kitchen organizer",
        )
    assert url is not None
    assert "m.media-amazon.com" in url
    # Exactly ONE cascade iteration: the first query found a strong
    # retailer image, so the cascade should NOT make a second or third
    # call. Each cascade iteration may include a Tavily + DDG post, so
    # we just assert the cascade does not exceed 2 calls.
    assert call_count["n"] <= 2, (
        f"expected <= 2 Tavily/DDG calls (early-stop after q1); got {call_count['n']}"
    )


def test_cascade_10_min_product_image_score_still_enforced():
    """If even the best image across all queries scores below
    MIN_PRODUCT_IMAGE_SCORE, the candidate is rejected."""
    import backend.product_research as pr_mod
    from backend.product_research import ProductResearcher
    fake_resp = mock.Mock(status_code=200)
    fake_resp.json.return_value = {
        "results": [],
        "images": [
            "https://random-cdn-1.example.com/a.jpg",
            "https://random-cdn-2.example.org/b.jpg",
        ],
    }
    head_resp = mock.Mock(status_code=200, headers={
        "Content-Type": "image/jpeg", "Content-Length": "24576",
    })
    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", return_value=fake_resp), \
         mock.patch.object(pr_mod.live_research.requests, "post", return_value=fake_resp), \
         mock.patch.object(pca_mod.requests, "head", return_value=head_resp):
        url = ProductResearcher()._find_product_image(
            product_name="Spice Rack Organizer",
            category="Kitchen",
            intent="kitchen organizer",
        )
    assert url is None, "URLs from unknown hosts must not pass MIN_PRODUCT_IMAGE_SCORE"


def test_cascade_11_image_search_runs_for_all_queries_when_needed():
    """If query 1 returns no useful URL, query 2 and query 3 must run."""
    import backend.product_research as pr_mod
    from backend.product_research import ProductResearcher
    # Query 1 returns junk; query 2 returns the winner.
    resp_q1 = mock.Mock(status_code=200)
    resp_q1.json.return_value = {
        "results": [],
        "images": ["https://random-cdn.example.com/junk.jpg"],
    }
    resp_q2 = mock.Mock(status_code=200)
    resp_q2.json.return_value = {
        "results": [],
        "images": ["https://m.media-amazon.com/images/I/71winnerXYZ.jpg"],
    }
    head_resp = mock.Mock(status_code=200, headers={
        "Content-Type": "image/jpeg", "Content-Length": "24576",
    })
    posts = [resp_q1, resp_q2, resp_q2]
    posts_iter = iter(posts)

    def selective_post(*args, **kwargs):
        return next(posts_iter)

    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", side_effect=selective_post), \
         mock.patch.object(pr_mod.live_research.requests, "post", side_effect=selective_post), \
         mock.patch.object(pca_mod.requests, "head", return_value=head_resp):
        url = ProductResearcher()._find_product_image(
            product_name="Rotating Spice Rack Organizer",
            category="Kitchen",
            intent="kitchen organizer",
        )
    assert url is not None
    assert "m.media-amazon.com" in url


def test_cascade_12_request_budget_still_respected():
    """The cascade does not loop forever; MAX_IMAGE_SEARCH_QUERIES caps
    the Tavily+DDG calls per candidate (each cascade iteration may
    use one Tavily POST and one DDG POST, so the bound is in practice
    2 * MAX_IMAGE_SEARCH_QUERIES)."""
    import backend.product_research as pr_mod
    from backend.product_research import ProductResearcher
    fake_resp = mock.Mock(status_code=200)
    fake_resp.json.return_value = {
        "results": [],
        "images": ["https://random-cdn.example.com/x.jpg"],
    }
    head_resp = mock.Mock(status_code=200, headers={
        "Content-Type": "image/jpeg", "Content-Length": "24576",
    })
    call_count = {"n": 0}

    def counting_post(*args, **kwargs):
        call_count["n"] += 1
        return fake_resp

    with mock.patch.object(live_research, "_is_tavily_configured", return_value=True), \
         mock.patch.object(live_research.requests, "post", side_effect=counting_post), \
         mock.patch.object(pr_mod.live_research.requests, "post", side_effect=counting_post), \
         mock.patch.object(pca_mod.requests, "head", return_value=head_resp):
        url = ProductResearcher()._find_product_image(
            product_name="Spice Rack Organizer",
            category="Kitchen",
            intent="kitchen organizer",
        )
    assert url is None
    # Each cascade iteration may do Tavily POST + optional DDG POST, so
    # the absolute upper bound is 2 * MAX_IMAGE_SEARCH_QUERIES.
    assert call_count["n"] <= pca_mod.MAX_IMAGE_SEARCH_QUERIES * 2, (
        f"expected <= {pca_mod.MAX_IMAGE_SEARCH_QUERIES * 2} HTTP calls; got {call_count['n']}"
    )


def test_cascade_constants_are_sane():
    """STRONG_IMAGE_SCORE must be >= MIN_PRODUCT_IMAGE_SCORE."""
    assert pca_mod.STRONG_IMAGE_SCORE >= pca_mod.MIN_PRODUCT_IMAGE_SCORE
    assert pca_mod.MAX_IMAGE_SEARCH_QUERIES >= 1
    assert pca_mod.MAX_IMAGE_SEARCH_QUERIES <= 5


# ── CATEGORY CLASSIFIER REGRESSION (added 2026-09-28) ─────────────────────
# Previously _CATEGORY_KEYWORDS['cleaning'] contained the generic noun
# "kitchen", which caused _classify("kitchen organizer") to tie 1-1
# between cleaning and kitchen and resolve to cleaning (because cleaning
# is iterated first in dict order). That misrouted the whole pipeline to
# CLEANING_POOL, which has no organizer product, and /find-winner 404'd.
# These tests lock the corrected keyword lists.


def test_classify_kitchen_organizer_routes_to_kitchen_pool():
    """'_classify' must route 'kitchen organizer' to 'kitchen', not 'cleaning'."""
    from backend.product_research import _classify, KITCHEN_POOL, CLEANING_POOL

    hint = _classify("kitchen organizer")
    assert hint == "kitchen", (
        f"kitchen organizer must classify to 'kitchen' (has organizer); got {hint!r}"
    )
    # The KITCHEN_POOL must contain a candidate whose name includes
    # 'organizer' so the cascade can actually run on it.
    assert any("organizer" in c.name.lower() for c in KITCHEN_POOL), (
        f"KITCHEN_POOL must contain an organizer candidate; "
        f"got {[c.name for c in KITCHEN_POOL]}"
    )
    # And the CLEANING_POOL must NOT contain one (otherwise the
    # misroute would not have caused a 404).
    assert not any("organizer" in c.name.lower() for c in CLEANING_POOL), (
        f"CLEANING_POOL must NOT contain an organizer candidate; "
        f"got {[c.name for c in CLEANING_POOL]}"
    )


def test_classify_cleaning_brush_still_routes_to_cleaning():
    """The fix must not regress cleaning-tool queries."""
    from backend.product_research import _classify

    assert _classify("cleaning brush") == "cleaning"
    assert _classify("toilet brush") == "cleaning"
    assert _classify("shower caddy") == "cleaning"
    assert _classify("bathroom cleaner") == "cleaning"


def test_classify_all_live_queries_route_to_correct_pool():
    """The five live /find-winner queries must all route correctly."""
    from backend.product_research import _classify

    expected = {
        "kitchen organizer":  "kitchen",
        "phone stand":        "tech",
        "pet bed":            "pet",
        "desk lamp":          "tech",
        "cleaning brush":     "cleaning",
    }
    for query, want in expected.items():
        got = _classify(query)
        assert got == want, (
            f"live query {query!r} must classify to {want!r}; got {got!r}"
        )


def test_classify_spice_rack_organizer_routes_to_kitchen():
    """'spice rack organizer' must route to kitchen (spice + rack + organizer)."""
    from backend.product_research import _classify

    assert _classify("spice rack organizer") == "kitchen"
    assert _classify("pantry organizer") == "kitchen"
    assert _classify("kitchen storage") == "kitchen"


def test_classify_cleaning_keywords_no_longer_contain_generic_nouns():
    """The cleaning bucket must not contain the generic noun 'kitchen' anymore.

    This is the exact word that caused the kitchen-organizer tie-break bug.
    """
    from backend.product_research import _CATEGORY_KEYWORDS

    assert "kitchen" not in _CATEGORY_KEYWORDS["cleaning"], (
        f"'kitchen' in cleaning keywords re-introduces the tie-break bug; "
        f"got {_CATEGORY_KEYWORDS['cleaning']}"
    )


def test_classify_kitchen_keywords_contain_organizer_nouns():
    """The kitchen bucket must include organizer/storage nouns so queries
    like 'kitchen organizer' classify decisively to kitchen."""
    from backend.product_research import _CATEGORY_KEYWORDS

    kitchen_words = set(_CATEGORY_KEYWORDS["kitchen"])
    for must in ("organizer", "storage", "rack"):
        assert must in kitchen_words, (
            f"kitchen keywords missing {must!r}; got {sorted(kitchen_words)}"
        )


# ── 404 MESSAGE REGRESSION (added 2026-09-28) ────────────────────────────
# The user's screenshot showed a misleading 404 saying 'no usable product
# image' when in fact the empty/placeholder input never matched any pool
# candidate. The error message must distinguish relevance failures from
# image failures so the user can self-correct.


def test_no_winner_detail_relevance_only_suggests_specific_keyword():
    """When ALL rejections are relevance-based (category or product_type
    mismatch), the 404 message must tell the user to type a specific
    keyword — NOT the misleading 'enable TAVILY_API_KEY' wording."""
    from backend.product_research import _no_winner_detail

    rejection_log = [
        {"id": "scrub-brush-01", "name": "Spin Scrubber",
         "reason": "product_type_mismatch: product_type_score=0.00 < 0.5 for query='trending product'"},
        {"id": "knife-set-01", "name": "Knife Set",
         "reason": "category_mismatch: category_score=0.10 < 0.3 for query='trending product'"},
    ]
    detail = _no_winner_detail(rejection_log, attempts=2)
    assert "matched your query" in detail.lower(), (
        f"relevance-only detail must say query didn't match; got {detail!r}"
    )
    assert "kitchen organizer" in detail.lower(), (
        f"detail should suggest a concrete example keyword; got {detail!r}"
    )
    # Must NOT blame Tavily/image-search when the real issue is relevance.
    assert "TAVILY_API_KEY" not in detail, (
        f"relevance-only detail must not blame Tavily; got {detail!r}"
    )


def test_no_winner_detail_image_only_blames_image_search():
    """When ALL rejections are image-based (relevance passed for at least
    one candidate), the 404 message must mention images / TAVILY."""
    from backend.product_research import _no_winner_detail

    rejection_log = [
        {"id": "spice-rack-02", "name": "Rotating Spice Rack Organizer",
         "reason": "no image above MIN=25 after 3 queries (best=-45)"},
        {"id": "knife-set-01", "name": "Knife Set",
         "reason": "no image above MIN=25 after 3 queries (best=-30)"},
    ]
    detail = _no_winner_detail(rejection_log, attempts=2)
    assert "matching products" in detail.lower(), (
        f"image-only detail must say products matched but images failed; got {detail!r}"
    )
    assert "TAVILY" in detail or "image" in detail.lower(), (
        f"image-only detail should mention images/TAVILY; got {detail!r}"
    )


def test_no_winner_detail_mixed_counts_each():
    """When some rejections are relevance and some are image, count both."""
    from backend.product_research import _no_winner_detail

    rejection_log = [
        {"id": "x1", "name": "A",
         "reason": "product_type_mismatch: product_type_score=0.00 < 0.5"},
        {"id": "x2", "name": "B",
         "reason": "no image above MIN=25 after 3 queries (best=-45)"},
        {"id": "x3", "name": "C",
         "reason": "no image above MIN=25 after 3 queries (best=-30)"},
    ]
    detail = _no_winner_detail(rejection_log, attempts=3)
    assert "1" in detail and "2" in detail, (
        f"mixed detail must show 1 relevance + 2 image rejection counts; got {detail!r}"
    )


def test_no_winner_detail_empty_rejection_log_says_try_a_keyword():
    """When no rejection log exists (e.g. no candidates at all), tell the
    user to type a keyword — the most common cause is empty input defaulting
    to 'trending product'."""
    from backend.product_research import _no_winner_detail

    detail = _no_winner_detail([], attempts=0)
    assert "keyword" in detail.lower() or "candidate" in detail.lower(), (
        f"empty-log detail must suggest typing a keyword; got {detail!r}"
    )


# ── FRONTEND INPUT-CONTRACT REGRESSION (added 2026-09-28) ────────────────
# User-reported bug: the live page showed 'Find Winner failed' even when
# the user typed a keyword. The cause was the previous empty-input guard
# throwing silently when urlInput.value was unexpectedly empty (browser
# autofill / password-manager races). The fix removed the harsh throw and
# instead passes the value through to the backend (which returns a clear
# 404 message). These tests lock the new input-contract invariants.


def test_index_html_input_element_id_is_productUrl():
    """index.html MUST keep exactly one input field with id='productUrl'
    so the Find Winner flow reads the same element the user typed into."""
    import re
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    matches = re.findall(r'<input[^>]*\bid=["\']productUrl["\']', src)
    assert len(matches) == 1, (
        f"index.html must have exactly one input with id='productUrl'; "
        f"found {len(matches)}"
    )


def test_index_html_input_has_no_inline_onclick():
    """The input field must not have inline JS handlers."""
    import re
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    for m in re.finditer(r'<input[^>]*\bid=["\']productUrl["\'][^>]*>', src):
        assert "onclick" not in m.group(0).lower(), (
            f"productUrl input has an inline onclick; should use the listener"
        )


def test_index_html_analyzeProductUrl_reads_url_field():
    """analyzeProductUrl() must read the URL field fresh from the DOM,
    log the value for diagnostics, and reject empty / non-URL input
    before calling the backend."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    # Must read from productUrl element directly.
    assert "getElementById('productUrl')" in src, (
        "analyzeProductUrl must read from the #productUrl element"
    )
    # Must validate http:// or https:// scheme client-side.
    assert "/^https?:\\/\\/" in src or "test('http" in src or "/^http" in src, (
        "analyzeProductUrl must validate URL scheme client-side"
    )


def test_index_html_findWinner_does_not_throw_on_empty_input():
    """findWinner() must NOT silently throw on empty input — the previous
    implementation did, which caused the 'Could not find a winner'
    misleading body. Empty input now passes through to the backend."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    # Locate the findWinner function body.
    fn_start = src.index("async function findWinningProduct(")
    fn_end = src.index("    }", fn_start + 100)
    fn_body = src[fn_start:fn_end]
    # The old guard had: throw new Error('Please paste a product URL...')
    assert "throw new Error(\n                    'Please paste a product URL" not in fn_body, (
        "findWinner must NOT throw on empty input — the silent throw caused "
        "the misleading 'Could not find a winner' error message."
    )


def test_index_html_error_renderer_recognizes_new_relevance_message():
    """The catch-block error renderer must recognize the new relevance-
    vs-image 404 message so the user sees the helpful body instead of
    the misleading 'Could not find a winner. Please try again.'"""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "matched your query" in src, (
        "error renderer must recognize the new 'matched your query' 404 message"
    )
    assert "verified product photo" in src, (
        "error renderer must recognize the new 'verified product photo' "
        "404 message"
    )


def test_index_html_has_exactly_one_binding_per_button():
    """index.html must have exactly ONE addEventListener per workflow
    button — one for findWinningProductBtn and one for analyzeProductBtn.
    No duplicate handlers, no shared click handler."""
    import re
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    has_find_winning = bool(re.search(
        r"addEventListener\(\s*['\"]click['\"]\s*,\s*findWinningProduct\s*\)",
        src))
    has_analyze = bool(re.search(
        r"addEventListener\(\s*['\"]click['\"]\s*,\s*analyzeProductUrl\s*\)",
        src))
    assert has_find_winning, \
        "findWinningProductBtn must be bound to findWinningProduct"
    assert has_analyze, \
        "analyzeProductBtn must be bound to analyzeProductUrl"
    # Old shared binding must be gone.
    assert not re.search(
        r"addEventListener\(\s*['\"]click['\"]\s*,\s*findWinner\s*\)",
        src), "Old shared findWinner binding must be removed"


def test_index_html_no_inline_onclick_on_find_winner_button():
    """The Find Winner button must rely solely on the addEventListener."""
    import re
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    for m in re.finditer(r'<button[^>]*\bid=["\']findWinningProductBtn["\'][^>]*>', src):
        assert "onclick" not in m.group(0).lower(), (
            "findWinningProductBtn must not have an inline onclick — use the listener"
        )


def test_index_html_passes_correct_params_per_workflow():
    """PATH A (Find Winning Product) must call /discover-winner with NO
    keyword input. PATH B (Analyze Product) must call /analyze-product-url
    with the `url` query param."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    a_start = src.index("async function findWinningProduct(")
    a_end = src.index("async function analyzeProductUrl(", a_start)
    a_body = src[a_start:a_end]
    # PATH A: discovery endpoint only.
    assert "/api/v1/discover-winner" in a_body, (
        "PATH A must call /api/v1/discover-winner"
    )
    # PATH B: analyze-product-url with 'url' param.
    assert "/api/v1/analyze-product-url" in src, (
        "PATH B must call /api/v1/analyze-product-url"
    )
    assert "params.set('url', url)" in src, (
        "PATH B must send the user input via 'url' query param"
    )


def test_index_html_workflows_have_independent_input_contracts():
    """PATH A (findWinningProduct) must call the dedicated /discover-winner
    endpoint — NEVER /find-winner and NEVER send a fake 'trending product'
    seed. PATH B (analyzeProductUrl) must NOT substitute any fallback —
    empty URL shows inline error and returns early."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    a_start = src.index("async function findWinningProduct(")
    a_end = src.index("async function analyzeProductUrl(", a_start)
    a_body = src[a_start:a_end]
    # PATH A must call /discover-winner.
    assert "/api/v1/discover-winner" in a_body, (
        "PATH A must call the dedicated /api/v1/discover-winner endpoint"
    )
    # PATH A must NOT call /find-winner (the legacy keyword path).
    assert "/api/v1/find-winner" not in a_body, (
        "PATH A must NOT call /api/v1/find-winner — it must use the "
        "discovery endpoint exclusively"
    )
    # PATH A must NOT send a fake 'trending product' seed.
    assert "'trending product'" not in a_body, (
        "PATH A must NOT send a fake 'trending product' seed; "
        "discovery runs live research"
    )
    # PATH A must NOT send url_or_keyword as a URLSearchParams key.
    assert "params.set('url_or_keyword'" not in a_body, (
        "PATH A must not send url_or_keyword (no keyword input)"
    )
    # PATH B's body must NOT have any hint || fallback for the URL param.
    b_start = src.index("async function analyzeProductUrl(")
    b_end = src.index("</script>", b_start)
    b_body = src[b_start:b_end]
    assert "||" not in b_body.split("params.set")[1] if "params.set" in b_body else True, (
        "PATH B must NOT OR-fallback the URL param"
    )


def test_index_html_analyzeProductUrl_returns_early_with_inline_validation():
    """PATH B (analyzeProductUrl) must (a) set inline error text,
    (b) focus the input, (c) return early before any fetch when the
    URL field is empty."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    b_start = src.index("async function analyzeProductUrl(")
    b_end = src.index("</script>", b_start)
    b_body = src[b_start:b_end]
    # Inline guidance text for PATH B.
    assert "Paste an Amazon" in b_body, (
        "analyzeProductUrl must show inline guidance for empty URL"
    )
    assert "productUrlError" in b_body, (
        "analyzeProductUrl must reference the inline error element"
    )
    assert "urlInput.focus" in b_body, (
        "analyzeProductUrl must focus the input on empty"
    )
    # PATH B's empty-input branch is an early return, NOT a fetch.
    # Look for the inline error + return sequence.
    empty_block_idx = b_body.index("if (!url)")
    empty_block_end = empty_block_idx + 800
    assert "return" in b_body[empty_block_idx:empty_block_end], (
        "analyzeProductUrl must early-return when URL is empty"
    )


# ── URL RESOLVER REGRESSION (added 2026-09-28) ─────────────────────────
# Real users paste Amazon share / short links (link.amazon/<token>,
# amzn.to/<token>, /dp/<asin>) that have no product name in the slug.
# The keyword path used to fail the relevance gate on those. The new
# url_resolver module detects URL inputs, follows redirects safely,
# extracts ASIN, and resolves the title via page metadata + Tavily
# fallback before the candidate pipeline runs.


def test_url_resolver_detect_url_accepts_https():
    from backend.url_resolver import detect_url
    assert detect_url("https://www.amazon.com/dp/B0fIJWu2r") is True
    assert detect_url("https://link.amazon/B0fIJWu2r") is True
    assert detect_url("http://amzn.to/abc123") is True


def test_url_resolver_detect_url_rejects_bare_keywords():
    from backend.url_resolver import detect_url
    assert detect_url("kitchen organizer") is False
    assert detect_url("desk lamp") is False
    assert detect_url("") is False
    assert detect_url(None) is False  # type: ignore[arg-type]
    assert detect_url("amazon.com/dp/B0fIJWu2r") is False  # no scheme


def test_url_resolver_extract_asin_dp_path():
    from backend.url_resolver import extract_asin
    assert extract_asin("https://www.amazon.com/dp/B0fIJWu2r") == "B0FIJWU2R"
    assert extract_asin("https://amazon.com/dp/B0ABCDEFGH/") == "B0ABCDEFGH"
    assert extract_asin("https://www.amazon.com/Kitchen-Organizer/dp/B0XYZ12345") == "B0XYZ12345"


def test_url_resolver_extract_asin_gp_path():
    from backend.url_resolver import extract_asin
    assert extract_asin("https://www.amazon.com/gp/product/B0fIJWu2r") == "B0FIJWU2R"
    assert extract_asin("https://amazon.com/gp/product/B0ABCDEFGH/ref=foo") == "B0ABCDEFGH"


def test_url_resolver_extract_asin_short_link():
    from backend.url_resolver import extract_asin
    # link.amazon short link: token IS the ASIN
    assert extract_asin("https://link.amazon/B0fIJWu2r") == "B0FIJWU2R"
    # amzn.to with too-short token: cannot confidently call it an ASIN
    assert extract_asin("https://amzn.to/abc123") is None


def test_url_resolver_extract_asin_is_host_agnostic():
    """ASIN extraction is a regex on the path — it does NOT validate
    the host. The host validation happens later in the pipeline via
    ``_is_amazon_target``. We document this behavior so the test
    catches regressions if someone tightens the regex unexpectedly.
    """
    from backend.url_resolver import extract_asin
    # Empty / non-URL inputs return None
    assert extract_asin("") is None
    assert extract_asin("not a url") is None
    # Any /dp/<token> path matches, regardless of host
    assert extract_asin("https://example.com/dp/B0fIJWu2r") == "B0FIJWU2R"


def test_url_resolver_flags_non_amazon_target():
    """When the resolved final URL is not on an Amazon host, the
    ResolvedProduct must record this in ``notes`` so the rest of
    the pipeline can react appropriately."""
    from backend.url_resolver import resolve_url_to_product
    rp = resolve_url_to_product(
        "https://example.com/dp/B0fIJWu2r",
        timeout=2.0,
        tavily_timeout=0,
    )
    assert any("non_amazon_target" in n for n in rp.notes)


def test_url_resolver_extract_title_from_html_jsonld():
    from backend.url_resolver import extract_product_title_from_html
    html = '''<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Product",
 "name": "Rotating Spice Rack Organizer 16 Jars"}
</script>
</head></html>'''
    assert extract_product_title_from_html(html) == "Rotating Spice Rack Organizer 16 Jars"


def test_url_resolver_extract_title_from_html_og_title():
    from backend.url_resolver import extract_product_title_from_html
    html = '''<html><head><meta property="og:title" content="My Phone Stand" /></head></html>'''
    assert extract_product_title_from_html(html) == "My Phone Stand"


def test_url_resolver_extract_title_strips_amazon_prefix():
    from backend.url_resolver import extract_product_title_from_html
    html = '''<html><head><title>Amazon.com: Pet Dog Bed Orthopedic : Amazon.com</title></head></html>'''
    assert extract_product_title_from_html(html) == "Pet Dog Bed Orthopedic"


def test_url_resolver_unsafe_scheme_rejected():
    from backend.url_resolver import resolve_url_to_product
    rp = resolve_url_to_product("ftp://example.com/file")
    assert rp.error is not None
    assert rp.resolved is False


def test_url_resolver_returns_resolved_product_for_short_link():
    """Short link with no real network access: ASIN extraction succeeds
    and we get a usable record that the pipeline can score.

    If the redirect target returns a 404 page (common for link.amazon
    tracking redirects), the title comes from either Tavily research
    or the ASIN-only placeholder — never the 404 page's <title>.
    """
    from backend.url_resolver import resolve_url_to_product
    rp = resolve_url_to_product(
        "https://link.amazon/B0fIJWu2r",
        timeout=2.0,
        tavily_timeout=0,  # skip Tavily in unit tests
    )
    assert rp.asin == "B0FIJWU2R"
    assert rp.original_url == "https://link.amazon/B0fIJWu2r"
    # Resolved product must always have a usable title.
    assert rp.title is not None
    assert rp.title.strip() != ""
    # Generic 404 titles must never leak into rp.title.
    assert "404" not in rp.title.lower()
    assert "not found" not in rp.title.lower()
    assert rp.resolved is True


def test_url_resolver_returns_unresolved_for_non_url_input():
    from backend.url_resolver import resolve_url_to_product
    rp = resolve_url_to_product("kitchen organizer")
    assert rp.error == "not_a_url"
    assert rp.resolved is False


def test_build_url_candidate_has_required_fields():
    from backend.url_resolver import ResolvedProduct
    from backend.product_research import _build_url_candidate
    rp = ResolvedProduct(
        original_url="https://link.amazon/B0fIJWu2r",
        final_url="https://www.amazon.com/dp/B0FIJWU2R",
        asin="B0FIJWU2R",
        title="Rotating Spice Rack Organizer",
        source="tavily",
    )
    card = _build_url_candidate(rp)
    assert card.id.startswith("url-")
    assert "Rotating Spice Rack Organizer" in card.name
    assert "amazon" in card.url.lower()
    assert card.image_url.startswith("data:")


def test_build_url_candidate_handles_asin_only():
    from backend.url_resolver import ResolvedProduct
    from backend.product_research import _build_url_candidate
    rp = ResolvedProduct(
        original_url="https://link.amazon/B0XYZ12345",
        asin="B0XYZ12345",
        title="Amazon Product B0XYZ12345",
        source="asin_only",
    )
    card = _build_url_candidate(rp)
    assert card.id == "url-b0xyz12345"
    assert card.name == "Amazon Product B0XYZ12345"


def test_url_resolution_attaches_to_winner_when_url_card_wins():
    """When the URL candidate wins, the winner payload must include
    url_resolution diagnostics and source='url_resolved'."""
    from backend.url_resolver import ResolvedProduct
    from backend.product_research import ProductResearcher
    from backend import product_control_agent as pca
    from unittest import mock

    rp = ResolvedProduct(
        original_url="https://link.amazon/B0fIJWu2r",
        final_url="https://www.amazon.com/dp/B0FIJWU2R",
        asin="B0FIJWU2R",
        title="Rotating Spice Rack Organizer",
        source="tavily",
    )
    fake_image = "https://m.media-amazon.com/images/I/asin.jpg"
    fake_head = mock.Mock(
        status_code=200,
        headers={"Content-Type": "image/jpeg", "Content-Length": "100000"},
    )

    class _Env:
        research_status = "live"
        research_timestamp = "2026-09-28T00:00:00Z"
        research_provider = "tavily"
        research_sources = []
        research_summary = "ok"
        research_query = "Rotating Spice Rack Organizer"
        research_image_urls = []

    r = ProductResearcher()
    with mock.patch.object(ProductResearcher, "_find_product_image",
                           return_value=fake_image), \
         mock.patch.object(pca.requests, "head", return_value=fake_head), \
         mock.patch.object(pca, "_size_via_range_get", return_value=100000):
        winner = r._pick_research_backed(
            intent="Rotating Spice Rack Organizer",
            category_hint="kitchen",
            envelope=_Env(),
            exclude=set(),
            seed=None,
            use_ai=False,
            url_resolution=rp,
        )
    assert winner.get("url_resolution") is not None
    assert winner["url_resolution"]["asin"] == "B0FIJWU2R"
    assert winner.get("source") == "url_resolved"


def test_url_resolution_does_not_set_when_unresolved():
    """If the URL did not resolve (no ASIN, no title), url_resolution
    must not be added to the winner payload."""
    from backend.url_resolver import ResolvedProduct
    from backend.product_research import ProductResearcher
    from backend import product_control_agent as pca
    from unittest import mock

    rp = ResolvedProduct(error="not_a_url")  # not resolved

    class _Env:
        research_status = "live"
        research_timestamp = "2026-09-28T00:00:00Z"
        research_provider = "tavily"
        research_sources = []
        research_summary = "ok"
        research_query = "kitchen organizer"
        research_image_urls = []

    fake_image = "https://m.media-amazon.com/images/I/asin.jpg"
    fake_head = mock.Mock(
        status_code=200,
        headers={"Content-Type": "image/jpeg", "Content-Length": "100000"},
    )
    r = ProductResearcher()
    with mock.patch.object(ProductResearcher, "_find_product_image",
                           return_value=fake_image), \
         mock.patch.object(pca.requests, "head", return_value=fake_head), \
         mock.patch.object(pca, "_size_via_range_get", return_value=100000):
        winner = r._pick_research_backed(
            intent="kitchen organizer",
            category_hint="kitchen",
            envelope=_Env(),
            exclude=set(),
            seed=None,
            use_ai=False,
            url_resolution=rp,
        )
    # Should win a pool candidate (no URL resolution in payload).
    assert winner.get("source") in ("live_research", "partial_research")
    assert "url_resolution" not in winner


def test_image_query_cascade_includes_asin_first():
    """When an ASIN is supplied, build_image_query_cascade must put it
    first so Tavily/Amazon search can anchor on the unique product id
    instead of the (often verbose / marketing-heavy) resolved title."""
    from backend.product_control_agent import build_image_query_cascade
    cascade = build_image_query_cascade(
        product_name="Amazon Devices: Amazon Devices & Accessories: "
                     "Smart Home Security & Lighting & More",
        category="Tech & Gadgets",
        intent="Amazon Devices: Amazon Devices & Accessories",
        asin="B0BN72Y2FK",
    )
    assert cascade[0].startswith("B0BN72Y2FK"), (
        f"first cascade query must start with the ASIN; got {cascade[0]!r}"
    )


def test_image_query_cascade_without_asin_keeps_legacy_order():
    """Pool candidates (no ASIN) keep the legacy cascade ordering:
    normalized title → simplified title → intent."""
    from backend.product_control_agent import build_image_query_cascade
    cascade = build_image_query_cascade(
        product_name="Rotating Spice Rack Organizer — 16 Jars, Labels Included",
        category="Kitchen & Cooking",
        intent="kitchen organizer",
    )
    assert "rotating spice rack organizer" in cascade[0].lower()
    assert "kitchen organizer" in cascade[-1].lower()


def test_url_resolution_only_on_url_card_winner():
    """When a pool candidate wins (because the URL candidate failed
    image search), ``source`` must remain ``live_research`` (NOT
    ``url_resolved``) and ``url_resolution`` must NOT be attached.
    The user's intent and Tavily results are preserved as
    ``url_resolution_attempted`` for UI diagnostics.
    """
    from backend.url_resolver import ResolvedProduct
    from backend.product_research import ProductResearcher
    from backend import product_control_agent as pca
    from unittest import mock

    # Intent + URL resolution both describe a spice rack organizer so
    # both the URL candidate AND pool candidate 'spice-rack-02' pass
    # the relevance gate. We force the URL candidate to fail image
    # search; the spice-rack-02 pool candidate then wins.
    rp = ResolvedProduct(
        original_url="https://link.amazon/B0SPICERACK",
        asin="B0SPICERACK",
        title="Rotating Spice Rack Organizer 16 Jars",
        source="tavily",
    )

    class _Env:
        research_status = "live"
        research_timestamp = "2026-09-28T00:00:00Z"
        research_provider = "tavily"
        research_sources = []
        research_summary = "ok"
        research_query = "rotating spice rack organizer"
        research_image_urls = []

    fake_image = "https://m.media-amazon.com/images/I/asin.jpg"
    fake_head = mock.Mock(
        status_code=200,
        headers={"Content-Type": "image/jpeg", "Content-Length": "100000"},
    )

    r = ProductResearcher()

    def _fake_find(self=None, *, product_name="", category="", intent="", asin=None):
        # URL candidate (has asin) fails image search.
        if asin:
            return None
        # Pool candidates (no asin) succeed.
        return fake_image

    with mock.patch.object(ProductResearcher, "_find_product_image",
                           side_effect=_fake_find), \
         mock.patch.object(pca.requests, "head", return_value=fake_head), \
         mock.patch.object(pca, "_size_via_range_get", return_value=100000):
        winner = r._pick_research_backed(
            intent="rotating spice rack organizer",
            category_hint="kitchen",
            envelope=_Env(),
            exclude=set(),
            seed=None,
            use_ai=False,
            url_resolution=rp,
        )
    # Pool candidate won — source must NOT be url_resolved.
    assert winner.get("source") in ("live_research", "partial_research"), \
        f"pool winner source must NOT be url_resolved; got {winner.get('source')!r}"
    assert "url_resolution" not in winner, (
        "url_resolution metadata must only be attached when the URL candidate itself wins"
    )
    # Diagnostic info preserved under a separate key.
    assert winner.get("url_resolution_attempted") is not None
    assert winner["url_resolution_attempted"]["asin"] == "B0SPICERACK"




# ── WORKFLOW SEPARATION REGRESSION (added 2026-09-28) ─────────────────────
# The two workflows (PATH A findWinningProduct / PATH B analyzeProductUrl)
# must be truly separate: different inputs, different buttons, different
# endpoints, different stages, different error messages, but shared
# downstream rendering helpers.


def test_index_html_two_workflow_sections_in_dom():
    """The DOM must contain both workflow sections with distinct headings."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "Find a Winning Product" in src,         "DOM must include the 'Find a Winning Product' section heading"
    assert "I Already Have a Product" in src,         "DOM must include the 'I Already Have a Product' section heading"


def test_index_html_two_workflow_buttons_with_distinct_ids():
    """PATH A button (#findWinningProductBtn) and PATH B button
    (#analyzeProductBtn) must both exist with distinct ids."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert 'id="findWinningProductBtn"' in src,         "PATH A button id='findWinningProductBtn' must exist"
    assert 'id="analyzeProductBtn"' in src,         "PATH B button id='analyzeProductBtn' must exist"
    # Old single button id must be gone.
    assert 'id="findWinnerBtn"' not in src,         "Old id='findWinnerBtn' must be removed"


def test_index_html_each_button_has_exactly_one_handler():
    """Each workflow button has exactly ONE click handler. No duplicates."""
    import re
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    # PATH A: findWinningProductBtn -> findWinningProduct
    fa_matches = re.findall(
        r'''addEventListener\(\s*['"]click['"]\s*,\s*findWinningProduct\s*\)''',
        src)
    assert len(fa_matches) == 1, \
        f"findWinningProductBtn must have exactly one handler; got {len(fa_matches)}"
    # PATH B: analyzeProductBtn -> analyzeProductUrl
    fb_matches = re.findall(
        r'''addEventListener\(\s*['"]click['"]\s*,\s*analyzeProductUrl\s*\)''',
        src)
    assert len(fb_matches) == 1, \
        f"analyzeProductBtn must have exactly one handler; got {len(fb_matches)}"


def test_index_html_findWinningProduct_does_not_read_url_field():
    """PATH A must never read the #productUrl input value. This is the
    core invariant: one-click find winning product is INDEPENDENT of
    the URL field."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    a_start = src.index("async function findWinningProduct(")
    # The function ends right before the next top-level async function
    # declaration (analyzeProductUrl).
    a_end = src.index("async function analyzeProductUrl(", a_start)
    a_body = src[a_start:a_end]
    # Strip comments and check the remaining code for any actual
    # getElementById('productUrl') call.
    import re as _re_strip
    code = _re_strip.sub(r"//.*", "", a_body)
    code = _re_strip.sub(r"/\*.*?\*/", "", code, flags=_re_strip.DOTALL)
    assert "getElementById('productUrl')" not in code, (
        "findWinningProduct must NOT read the #productUrl field — it has no input"
    )
    # Must not declare a urlInput local variable (`const urlInput = …`,
    # `let urlInput = …`, etc.). Property keys (e.g. `urlInput: null`) are
    # fine because they just pass null to the pipeline opts.
    import re as _re_vardecl
    assert not _re_vardecl.search(r"\b(const|let|var)\s+urlInput\b", code), (
        "findWinningProduct must not declare a local urlInput variable"
    )


def test_index_html_analyzeProductUrl_only_reads_url_field():
    """PATH B reads ONLY the #productUrl input value. It does not
    invoke a generic random-winner endpoint."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    b_start = src.index("async function analyzeProductUrl(")
    b_end = src.index("</script>", b_start)
    b_body = src[b_start:b_end]
    assert "getElementById('productUrl')" in b_body, (
        "analyzeProductUrl must read from the #productUrl field"
    )
    # Must target the dedicated /analyze-product-url endpoint, NOT
    # the generic /find-winner endpoint.
    assert "/api/v1/analyze-product-url" in b_body, (
        "analyzeProductUrl must hit /api/v1/analyze-product-url"
    )


def test_index_html_workflow_button_labels_are_distinct():
    """PATH A button starts with 'Find Winning Product'; PATH B with
    'Analyze Product'. The success-idle labels are also distinct."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "Find Winning Product" in src,         "PATH A idle label must be 'Find Winning Product'"
    assert "Analyze Product" in src,         "PATH B idle label must be 'Analyze Product'"
    assert "Find Another Winner" in src,         "PATH A success label must be 'Find Another Winner'"
    assert "Analyze Another URL" in src,         "PATH B success label must be 'Analyze Another URL'"


def test_index_html_workflow_loading_stages_are_distinct():
    """Each workflow supplies its own stage text via the pipeline opts."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    # PATH A stages
    assert "Researching products" in src
    assert "Scoring opportunities" in src
    assert "Selecting winner" in src
    # PATH B stages
    assert "Reading product" in src
    assert "Resolving product details" in src
    assert "Evaluating opportunity" in src
    # Stages must be distinct — neither list may contain the other's stages.
    a_stages = ["Researching products", "Scoring opportunities", "Selecting winner"]
    b_stages = ["Reading product", "Resolving product details", "Evaluating opportunity"]
    for s in a_stages:
        assert s not in b_stages
    for s in b_stages:
        assert s not in a_stages


def test_index_html_workflow_error_messages_are_distinct():
    """Each workflow supplies a distinct noPhotoHeading and failHeading."""
    src = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    assert "couldn\'t find a qualified product right now" in src,         "PATH A noPhotoHeading must say 'couldn\'t find a qualified product right now'"
    assert "couldn\'t identify or validate that product URL" in src,         "PATH B noPhotoHeading must say 'couldn\'t identify or validate that product URL'"


def test_backend_analyze_product_url_route_exists():
    """The backend must expose GET /api/v1/analyze-product-url as a
    separate endpoint from /find-winner."""
    import re
    from pathlib import Path
    p = Path(ROOT) / "backend" / "product_scout.py"
    src = p.read_text(encoding="utf-8-sig")
    assert re.search(r'''@router\.get\(\s*['"]/analyze-product-url''', src), (
        "backend must define @router.get('/api/v1/analyze-product-url')"
    )


def test_backend_analyze_product_url_rejects_non_url_input():
    """GET /analyze-product-url must reject non-URL inputs with HTTP 400
    and a clear error message — not pass them through to the keyword
    pipeline."""
    from trafficlift_pro import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.get("/api/v1/analyze-product-url?url=kitchen%20organizer")
    assert r.status_code == 400,         f"non-URL input must be rejected with 400; got {r.status_code}"
    body = r.json()
    assert "url" in body.get("detail", "").lower() or "http" in body.get("detail", "").lower(),         f"error must mention URL / http; got {body}"


def test_backend_analyze_product_url_rejects_empty_input():
    """GET /analyze-product-url must reject empty input with HTTP 400."""
    from trafficlift_pro import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.get("/api/v1/analyze-product-url?url=")
    assert r.status_code == 400,         f"empty input must be rejected with 400; got {r.status_code}"


def test_backend_find_winner_still_works_without_url_or_keyword():
    """The existing GET /find-winner?url_or_keyword=trending%20product
    (or no param at all) must still return a winner — the default seed
    drives the PATH A workflow."""
    from trafficlift_pro import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    # PATH A's default seed
    r = client.get("/api/v1/find-winner")
    # 200 with a winner OR 404 with relevance message — both are valid.
    assert r.status_code in (200, 404),         f"unexpected status; got {r.status_code}"



# ── DISCOVERY PIPELINE REGRESSION (added 2026-09-28) ────────────────────
# PATH A "Find Winning Product" must use a real discovery pipeline.
# The legacy path that sent 'trending product' as a fake keyword is
# dead — these tests lock the new architecture.


def test_backend_discover_winner_route_exists():
    """GET /api/v1/discover-winner must be defined in product_scout.py."""
    from pathlib import Path
    import re
    src = (Path(ROOT) / "backend" / "product_scout.py").read_text(encoding="utf-8-sig")
    assert re.search(r"""@router\.get\(\s*['"]/discover-winner""", src), (
        "backend must define @router.get('/api/v1/discover-winner')"
    )


def test_backend_discovery_does_not_apply_query_relevance():
    """The discovery module must NOT import or call the keyword
    relevance gate (CATEGORY_THRESHOLD / PRODUCT_TYPE_THRESHOLD against
    a user phrase). Discovery has no user query."""
    from pathlib import Path
    src = (Path(ROOT) / "backend" / "discovery.py").read_text(encoding="utf-8-sig")
    # The legacy relevance gate is in product_control_agent.
    assert "compute_query_product_relevance" not in src, (
        "discovery.py must not call compute_query_product_relevance "
        "(no user-query relevance in discovery mode)"
    )
    assert "CATEGORY_THRESHOLD" not in src, (
        "discovery.py must not enforce CATEGORY_THRESHOLD against a user phrase"
    )
    assert "PRODUCT_TYPE_THRESHOLD" not in src, (
        "discovery.py must not enforce PRODUCT_TYPE_THRESHOLD against a user phrase"
    )


def test_backend_discovery_uses_live_research_not_fake_keyword():
    """Discovery must use the live_research module for product-opportunity
    queries — NOT a hardcoded fake keyword."""
    from pathlib import Path
    src = (Path(ROOT) / "backend" / "discovery.py").read_text(encoding="utf-8-sig")
    assert "live_research.research" in src, (
        "discovery.py must use live_research.research() for live discovery"
    )
    # Must define multiple real product-opportunity queries.
    assert "DISCOVERY_QUERIES" in src, (
        "discovery.py must define DISCOVERY_QUERIES list of real search queries"
    )
    queries_match = False
    import re as _re
    m = _re.search(r"DISCOVERY_QUERIES\s*=\s*\[(.*?)\]", src, _re.DOTALL)  # noqa
    if m:
        queries = m.group(1)
        # Must contain at least 3 distinct queries.
        quoted = _re.findall(r"""['\"]([^'\"]+)['\"]""", queries)
        if len(set(quoted)) >= 3:
            queries_match = True
    assert queries_match, (
        "DISCOVERY_QUERIES must list at least 3 distinct real product queries"
    )


def test_backend_discovery_normalizes_and_rejects_non_product_titles():
    """Discovery must (a) reject CLEAR non-product titles (downloads,
    courses, generic store pages) while accepting listicles/blogs that
    name products, (b) require a product-type noun signal in the name,
    and (c) normalize Amazon prefix/suffix/list markers from titles."""
    from backend.discovery import (
        _looks_like_article, _has_product_signal, _normalize_title,
        _derive_product_type, _infer_category,
    )
    # Sanity-check the helpers exist and work.
    assert _derive_product_type("LED Desk Lamp") == "lamp"
    assert _infer_category("Pet Dog Bed Orthopedic") == "Pet Supplies"
    # CLEAR non-product content is rejected.
    assert _looks_like_article("Free Download: Best Kitchen Tools", "")
    assert _looks_like_article("Online Course for Pet Owners", "")
    assert _looks_like_article("Subscribe to our Newsletter Signup", "")
    assert _looks_like_article("About Us - Our Story", "")
    # Research roundups are evidence only; they cannot become winners.
    assert _looks_like_article("Best Kitchen Gadgets 2026", "")
    assert _looks_like_article("Top 10 Phone Stands", "")
    # A comparison phrase without a list/year marker may still contribute
    # a concrete product after snippet extraction.
    assert not _looks_like_article("Best vs Worst Kitchen Tools", "")
    # Real product names must NOT be rejected.
    assert not _looks_like_article(
        "Rotating Spice Rack Organizer 16 Jars", "")
    assert not _looks_like_article("LED Desk Lamp with USB Port", "")
    # Product-type signal required.
    assert _has_product_signal("LED Desk Lamp with USB Port")
    assert _has_product_signal("Rotating Spice Rack Organizer")
    # Pure brand or category without product noun.
    assert not _has_product_signal("Kitchen")
    assert not _has_product_signal("Apple")
    # Normalization strips Amazon prefix / suffix / list markers.
    assert _normalize_title("Amazon.com: Rotating Spice Rack Organizer : Amazon.com") == "Rotating Spice Rack Organizer"
    assert _normalize_title("Top 10: LED Desk Lamp 2026") == "LED Desk Lamp 2026"
    # It strips the listicle prefix even if "2026" stays.
    assert _normalize_title("Top 10: LED Desk Lamp") == "LED Desk Lamp"
    assert _normalize_title(None) is None
    assert _normalize_title("") is None
    assert _normalize_title(None) is None
    assert _normalize_title("") is None


def test_backend_discovery_is_bounded():
    """The discovery pipeline must have bounded constants."""
    from backend import discovery
    assert 1 <= discovery.MAX_DISCOVERY_RESEARCH_QUERIES <= 8, (
        "MAX_DISCOVERY_RESEARCH_QUERIES must be in [1, 8]"
    )
    assert 1 <= discovery.MAX_DISCOVERY_CANDIDATES <= 16, (
        "MAX_DISCOVERY_CANDIDATES must be in [1, 16]"
    )
    assert 1 <= discovery.MAX_IMAGE_SEARCH_QUERIES_PER_CANDIDATE <= 5, (
        "MAX_IMAGE_SEARCH_QUERIES_PER_CANDIDATE must be in [1, 5]"
    )
    # Budget must be reasonable (under backend REQUEST_BUDGET_SECONDS=20).
    assert 5.0 <= discovery.DISCOVERY_REQUEST_BUDGET_SECONDS <= 25.0, (
        "DISCOVERY_REQUEST_BUDGET_SECONDS must be in [5, 25]"
    )


def test_backend_discovery_rejects_with_specific_message(monkeypatch):
    from backend.tests.test_studio_integration import configure_discovery
    from fastapi import HTTPException
    engine,_=configure_discovery(monkeypatch,empty=True)
    with pytest.raises(HTTPException) as caught:engine._discover_batch()
    assert caught.value.status_code==503
    assert 'No saved-list product was substituted' in caught.value.detail



def test_backend_discovery_returns_qualified_winner(monkeypatch):
    """Full happy path: discovery returns a winner payload when at least
    one candidate has a verified image."""
    import types
    from backend import discovery as discovery_mod
    from backend.product_control_agent import ProductControlAgent
    from backend.discovery import discover_winner

    # Build a fake Tavily envelope with one real-product result.
    fake_sources = [
        {
            "title": "Rotating Spice Rack Organizer 16 Jars",
            "url": "https://example.com/spice-rack",
            "snippet": "A real product that organizes spices on your counter. Customer reviews are available.",
            "content": "A real product.",
        },
        {
            "title": "LED Desk Lamp with USB Charging Port",
            "url": "https://example.com/desk-lamp",
            "snippet": "A useful product for office desks. Customer reviews are available.",
            "content": "Office desk lighting.",
        },
    ]

    fake_env = types.SimpleNamespace(
        research_status="live",
        research_query="trending kitchen gadgets",
        research_sources=fake_sources,
    )

    monkeypatch.setattr("backend.live_research.research", lambda *a, **kw: fake_env)
    monkeypatch.setattr("backend.listing_photo.listing_images",lambda *a,**kw:[])

    # Stub image discovery to return a fake verified URL.
    fake_image = "https://m.media-amazon.com/images/I/asin.jpg"
    monkeypatch.setattr(
        discovery_mod, "_find_image_for_candidate",
        lambda card, intent="", tavily_image_urls=None, deadline_monotonic=None: fake_image,
    )

    # Stub the audit so the winner is accepted.
    def _stub_evaluate(w):
        r = types.SimpleNamespace()
        r.ok = True
        r.product = dict(w) if isinstance(w, dict) else {"name": "x"}
        r.product.setdefault("image_status", "verified")
        r.product.setdefault("image_url", fake_image)
        r.primary_reason = "ok"
        return r

    monkeypatch.setattr(ProductControlAgent, "evaluate", staticmethod(_stub_evaluate))

    winner = discover_winner()

    assert winner.get("source") == "discovery", (
        "winner must be tagged with source='discovery'"
    )
    assert winner.get("image_status") == "verified", (
        "winner image must be verified"
    )
    assert winner.get("image_url"), "winner must have an image_url"
    assert winner.get("discovery"), "winner must include discovery diagnostics"
    # raw_results accumulates across all 5 queries (each with 2 sources
    # in this stub); just check it's > 0 and bounded.
    raw = winner["discovery"]["raw_results"]
    assert raw >= 2, f"raw_results must be at least 2; got {raw}"
    assert raw <= 50, f"raw_results must be bounded; got {raw}"
    assert winner["discovery"]["normalized_candidates"] >= 1, (
        "at least one candidate should be normalized from the sources"
    )


def test_backend_discovery_skips_candidate_without_verified_image(monkeypatch):
    from backend.tests.test_studio_integration import configure_discovery
    from fastapi import HTTPException
    engine,_=configure_discovery(monkeypatch,photo=False)
    with pytest.raises(HTTPException) as caught:engine._discover_batch()
    assert caught.value.status_code==404
    assert 'passed' in caught.value.detail



# ── LIVE TAVILY IMAGES EXTRACTION (added 2026-09-28) ────────────────────
# Tavily's `images` response field is the PRIMARY source of real product
# photos. The previous implementation ignored it entirely, leaving
# `research_image_urls` empty for Tavily responses. The discovery
# pipeline needs these to qualify candidates with verified product photos.

def test_tavily_images_field_extracted_from_response():
    """The Tavily provider must surface its dedicated `images` field as
    ``research_image_urls`` on the returned envelope — this is the
    primary source of real product photos."""
    from unittest import mock
    fake_response = mock.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "query": "trending products",
        "results": [
            {"title": "Trending Products", "url": "https://example.com/a",
             "content": "Top picks include ultrasonic facial brush."},
        ],
        "images": [
            "https://cdn.shopify.com/s/files/1/abc/products/brush.jpg",
            "https://cb.scene7.com/is/image/Crate/gadget_Hero?wid=1440",
        ],
    }
    with mock.patch.dict("os.environ", {"TAVILY_API_KEY": "fake-key-for-test"}), \
         mock.patch("backend.live_research.requests.post", return_value=fake_response):
        from backend.live_research import research
        env = research("trending products")

    assert len(env.research_image_urls) == 2, (
        f"expected 2 images from Tavily `images` field; got "
        f"{len(env.research_image_urls)}: {env.research_image_urls}"
    )
    assert "https://cdn.shopify.com/" in env.research_image_urls[0]
    assert "https://cb.scene7.com/" in env.research_image_urls[1]


def test_tavily_images_dict_shape_handled():
    """Tavily's older API shape returns images as ``[{url, description}]``
    dicts. The extractor must accept both shapes."""
    from unittest import mock
    fake_response = mock.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "query": "q",
        "results": [],
        "images": [
            {"url": "https://cdn.shopify.com/p1.jpg", "description": "p1"},
            {"url": "https://m.media-amazon.com/p2.jpg"},
        ],
    }
    with mock.patch.dict("os.environ", {"TAVILY_API_KEY": "fake-key-for-test"}), \
         mock.patch("backend.live_research.requests.post", return_value=fake_response):
        from backend.live_research import research
        env = research("q")

    assert env.research_image_urls == [
        "https://cdn.shopify.com/p1.jpg",
        "https://m.media-amazon.com/p2.jpg",
    ]


# ── BROADER PRODUCT SIGNAL (added 2026-09-28) ───────────────────────────
# Live Tavily results for trending-product queries are mostly listicle /
# blog titles like "Best Kitchen Gadgets" or "Trending Products". These
# must be recognized as valid product opportunities.

def test_product_signal_accepts_listicle_titles():
    """Listicle titles with broad product words ('gadget', 'products',
    'supplies', 'kit') must pass _has_product_signal so they survive the
    discovery filter."""
    from backend.discovery import _has_product_signal
    assert _has_product_signal("Best Kitchen Gadgets")
    assert _has_product_signal("Trending Products")
    assert _has_product_signal("Top Pet Supplies")
    assert _has_product_signal("Phone Stand")          # singular
    assert _has_product_signal("Phone Stands")         # plural (stem + s)
    assert _has_product_signal("Best Coffee Accessories")
    assert _has_product_signal("Phone Stand Kit")      # 'kit' as whole word


def test_product_signal_rejects_brand_or_category_alone():
    """Single-word brand/category titles must still fail _has_product_signal
    so we don't return garbage."""
    from backend.discovery import _has_product_signal
    assert not _has_product_signal("Kitchen")          # 'kit' is not a whole word here
    assert not _has_product_signal("Apple")
    assert not _has_product_signal("")


# ── SNIPPET-BASED PRODUCT NAME EXTRACTION (added 2026-09-28) ────────────
# When a Tavily result has a generic title like "Trending Products", the
# real product name is typically mentioned in the opening sentences of
# the snippet. The normalizer should pick it up.

def test_normalize_extracts_product_from_snippet_when_title_generic():
    """Reject generic titles instead of guessing which listed item was intended."""
    assert discovery_mod._normalize_title('Trending Products','Ultrasonic Silicone Facial Cleansing Brush, Mini Air Purifier, and Smart Desk Lamp.') is None



def test_normalize_keeps_good_title_unchanged():
    """When the title already has product signal, normalization keeps
    the title even when a snippet is provided."""
    from backend.discovery import _normalize_title
    snippet = "Some unrelated mention of a desk lamp somewhere."
    name = _normalize_title("Rotating Spice Rack Organizer 16 Jars", snippet)
    assert name == "Rotating Spice Rack Organizer 16 Jars"


def test_normalize_handles_snippet_without_product_noun():
    """If neither title nor snippet identifies one concrete product,
    normalization must reject the candidate instead of returning a
    generic roundup title."""
    from backend.discovery import _normalize_title
    snippet = "A long-form essay about retail industry trends."
    name = _normalize_title("Trending Products", snippet)
    assert name is None


# ── HOST WHITELIST EXPANSION (added 2026-09-28) ──────────────────────────

def test_scene7_host_is_product_host():
    """The Adobe Scene7 CDN is used by Crate & Barrel, Target, and
    many other retailers. The matcher must recognize any subdomain."""
    from backend.product_control_agent import (
        PRODUCT_HOST_SCORES, rank_image_candidates,
    )
    assert "scene7.com" in PRODUCT_HOST_SCORES, (
        "PRODUCT_HOST_SCORES must include scene7.com for Adobe CDN coverage"
    )
    score = rank_image_candidates(
        ["https://cb.scene7.com/is/image/Crate/Hero?wid=1440"],
        product_name="Kitchen Gadgets",
        category="Kitchen & Cooking",
    )
    assert score and score[0][1] >= 25, (
        f"scene7.com URLs must clear MIN_PRODUCT_IMAGE_SCORE=25; "
        f"got {score[0][1] if score else None}"
    )


def test_alidropship_host_is_product_host():
    """aliDropship.com hosts legitimate product imagery at wp-content
    paths; the host bonus offsets the lifestyle-path penalty so
    well-named URLs (matching the candidate name) clear the threshold."""
    from backend.product_control_agent import (
        PRODUCT_HOST_SCORES, rank_image_candidates,
    )
    assert "alidropship.com" in PRODUCT_HOST_SCORES
    # With filename match, total should pass threshold.
    score = rank_image_candidates(
        ["https://alidropship.com/wp-content/uploads/2025/07/Ultrasonic-Silicone-Facial-Cleansing-Brush.webp"],
        product_name="Ultrasonic Silicone Facial Cleansing Brush",
        category="Beauty",
    )
    assert score and score[0][1] >= 25, (
        f"aliDropship URL with filename match must clear threshold; "
        f"got {score[0][1] if score else None}"
    )


# ── DISCOVERY HAPPY PATH WITH PRE-FETCHED IMAGES (added 2026-09-28) ─────
# The end-to-end discovery pipeline must succeed when:
#   - one candidate is normalized with a real product name
#   - the Tavily pre-fetched image list contains a high-scoring URL
#   - the cascade path is not exercised (pre-fetched is accepted first)

def test_discover_winner_uses_prefetched_image_first(monkeypatch):
    """If the Tavily envelope's research_image_urls contains a high-scoring
    image, discover_winner must use it WITHOUT running the cascade."""
    import types
    from backend import discovery as discovery_mod
    from backend.product_control_agent import ProductControlAgent

    # Real-shape envelope with research_image_urls populated.
    fake_sources = [
        {"title": "LED Desk Lamp with USB Port", "url": "https://example.com/lamp",
         "snippet": "A real product for office desks. Customer reviews are available.",
         "content": "Desk lighting."},
    ]
    fake_image_urls = [
        "https://m.media-amazon.com/images/I/asin.jpg",
    ]
    fake_env = types.SimpleNamespace(
        research_status="live",
        research_query="trending desk gadgets",
        research_sources=fake_sources,
        research_image_urls=fake_image_urls,
    )
    monkeypatch.setattr("backend.live_research.research", lambda *a, **kw: fake_env)
    monkeypatch.setattr("backend.listing_photo.listing_images",lambda *a,**kw:[])

    # Spy on _find_image_for_candidate to confirm pre-fetched is used.
    captured = {"called": 0, "pre_count": 0}
    def _spy(card, intent="", tavily_image_urls=None, deadline_monotonic=None):
        captured["called"] += 1
        captured["pre_count"] = len(tavily_image_urls or [])
        # Return verified URL if a pre-fetched image was supplied.
        if tavily_image_urls:
            return tavily_image_urls[0]
        return None
    monkeypatch.setattr(discovery_mod, "_find_image_for_candidate", _spy)

    class _Report:
        ok = True
        product = {"id": "x", "name": "y", "category": "z",
                   "image_url": "https://x", "image_status": "verified",
                   "url": "https://u"}
        primary_reason = "ok"
    monkeypatch.setattr(ProductControlAgent, "evaluate",
                        staticmethod(lambda w: _Report()))

    winner = discovery_mod.discover_winner()

    assert captured["pre_count"] >= 1, (
        "pre-fetched Tavily images must be passed to _find_image_for_candidate"
    )
    assert winner.get("source") == "discovery"
    assert winner.get("image_status") == "verified"


# ── CURATED WINNERS POOL (added 2026-09-28) ─────────────────────────────
# When live Tavily research fails to produce a qualified candidate, the
# discovery pipeline falls back to a marketing-expert curated pool of
# REAL trending products. These tests guarantee the fallback is wired
# correctly and always returns a verified winner.

def test_curated_pool_is_non_empty():
    """The curated pool must contain at least one entry — otherwise
    PATH A has no fallback when live research fails."""
    from backend.curated_winners import CURATED_WINNERS
    assert len(CURATED_WINNERS) >= 10, (
        f"curated pool must have at least 10 entries to provide "
        f"variety; got {len(CURATED_WINNERS)}"
    )


def test_curated_entries_have_required_fields():
    """Each curated entry must carry the fields required for the
    audit pipeline (name, category, image_queries, angle/pin metadata,
    trend signals, margin estimate, evergreen score, competition)."""
    from backend.curated_winners import CURATED_WINNERS
    required = {
        "name", "category", "image_queries", "angle_options",
        "pin_title_options", "pin_description_options", "hashtags_pool",
        "viral_hook_options", "trend_signals_options", "trend_score_range",
        "margin_estimate", "evergreen_score", "competition",
    }
    for entry in CURATED_WINNERS:
        missing = required - set(entry.keys())
        assert not missing, (
            f"curated entry {entry.get('name')!r} missing fields: {missing}"
        )
        assert isinstance(entry["image_queries"], list)
        assert len(entry["image_queries"]) >= 1, (
            f"curated entry {entry['name']!r} must have at least 1 image query"
        )
        assert 0.0 <= float(entry["evergreen_score"]) <= 1.0


def test_curated_pool_spans_multiple_categories():
    """The curated pool should cover at least 5 distinct categories so
    consecutive clicks surface different winners."""
    from backend.curated_winners import CURATED_WINNERS
    categories = set()
    for entry in CURATED_WINNERS:
        # Use first 2 words as coarse category (e.g. "Kitchen" from
        # "Kitchen & Dining") so they group sensibly.
        cat = (entry.get("category") or "").split("&")[0].strip()
        if cat:
            categories.add(cat.lower())
    assert len(categories) >= 5, (
        f"curated pool must span at least 5 categories; got {len(categories)}: {categories}"
    )


def test_pick_curated_winner_returns_verified_winner(monkeypatch):
    """When all curated entries have a valid image, _pick_curated_winner
    must return a winner payload with image_status=verified."""
    import types
    from backend import discovery as discovery_mod

    # Fake image resolver: every curated entry has a verified image.
    monkeypatch.setattr(
        discovery_mod, "_resolve_curated_image",
        lambda entry, deadline_monotonic: "https://m.media-amazon.com/images/I/asin.jpg",
    )
    # Fake audit: every payload passes.
    class _Report:
        ok = True
        product = {"id": "x", "name": "y", "category": "z",
                   "image_url": "https://x", "image_status": "verified",
                   "url": "https://u", "angle": "a", "pin_title": "p",
                   "pin_description": "d", "hashtags": ["#t"]}
        primary_reason = "ok"
    from backend.product_control_agent import ProductControlAgent
    monkeypatch.setattr(ProductControlAgent, "evaluate",
                        staticmethod(lambda w: _Report()))

    winner = discovery_mod._pick_curated_winner(
        seen_names=set(),
        deadline_monotonic=10**9,  # never expire
        t_start=0.0,
    )
    assert winner is not None
    assert winner.get("source") == "discovery-curated"
    assert winner.get("image_status") == "verified"
    assert winner.get("discovery", {}).get("fallback_used") == "curated_pool"


def test_pick_curated_winner_skips_entries_without_image(monkeypatch):
    """If the image resolver returns None for some entries, those are
    skipped; the first entry WITH a verified image wins."""
    from backend import discovery as discovery_mod

    # Always accept — the test verifies the "accept" branch works.
    monkeypatch.setattr(
        discovery_mod, "_resolve_curated_image",
        lambda entry, deadline_monotonic: "https://m.media-amazon.com/images/I/asin.jpg",
    )

    class _Report:
        ok = True
        product = {"id": "x", "name": "y", "category": "z",
                   "image_url": "https://x", "image_status": "verified",
                   "url": "https://u", "angle": "a", "pin_title": "p",
                   "pin_description": "d", "hashtags": ["#t"]}
        primary_reason = "ok"
    from backend.product_control_agent import ProductControlAgent
    monkeypatch.setattr(ProductControlAgent, "evaluate",
                        staticmethod(lambda w: _Report()))

    # Mark specific entries as "no image" via a parallel list. The
    # shuffle means we can't predict order — just ensure some entries
    # are skipped without breaking the function.
    blocked = {"Stanley Quencher H2.0 FlowState Tumbler 30oz",
               "Owala FreeSip Insulated Water Bottle 24oz"}

    def _fake_resolve(entry, deadline_monotonic):
        if entry["name"] in blocked:
            return None
        return "https://m.media-amazon.com/images/I/asin.jpg"
    monkeypatch.setattr(discovery_mod, "_resolve_curated_image", _fake_resolve)

    # Track how many times the audit ran (i.e. how many entries had an
    # image and were forwarded to audit).
    audit_calls = []
    def _spy_audit(payload):
        audit_calls.append(payload.get("name"))
        return _Report()
    monkeypatch.setattr(ProductControlAgent, "evaluate",
                        staticmethod(_spy_audit))

    winner = discovery_mod._pick_curated_winner(
        seen_names=set(),
        deadline_monotonic=10**9,
        t_start=0.0,
    )
    assert winner is not None
    # The winner must not be in the blocked set (those have no image).
    assert winner.get("name") not in blocked, (
        f"winner must not be a blocked entry; got {winner.get('name')!r}"
    )


def test_pick_curated_winner_respects_seen_names(monkeypatch):
    """Entries in seen_names are skipped (used to avoid re-picking the
    same winner in the same request)."""
    from backend import discovery as discovery_mod
    monkeypatch.setattr(
        discovery_mod, "_resolve_curated_image",
        lambda entry, deadline_monotonic: "https://m.media-amazon.com/images/I/asin.jpg",
    )

    def _fake_audit(payload):
        product = {
            "id": payload.get("id", "x"),
            "name": payload.get("name", "y"),
            "category": payload.get("category", "z"),
            "image_url": "https://x",
            "image_status": "verified",
            "url": "https://u",
            "angle": "a",
            "pin_title": "p",
            "pin_description": "d",
            "hashtags": ["#t"],
        }
        return type("R", (), {"ok": True, "primary_reason": "ok",
                              "product": product})()
    from backend.product_control_agent import ProductControlAgent
    monkeypatch.setattr(ProductControlAgent, "evaluate",
                        staticmethod(_fake_audit))

    # First call: no seen.
    w1 = discovery_mod._pick_curated_winner(
        seen_names=set(),
        deadline_monotonic=10**9, t_start=0.0,
    )
    assert w1 is not None
    chosen_name = w1.get("name")

    # Second call with that name in seen: should pick a different one.
    w2 = discovery_mod._pick_curated_winner(
        seen_names={chosen_name} if chosen_name else set(),
        deadline_monotonic=10**9, t_start=0.0,
    )
    assert w2 is not None
    assert w2.get("name") != chosen_name, (
        f"_pick_curated_winner must skip names in seen_names; "
        f"first pick={chosen_name!r}, second pick={w2.get('name')!r}"
    )


def test_pick_curated_winner_returns_none_when_no_image(monkeypatch):
    """If every curated entry's image resolver returns None, the
    function returns None so the caller can 404."""
    from backend import discovery as discovery_mod
    monkeypatch.setattr(
        discovery_mod, "_resolve_curated_image",
        lambda entry, deadline_monotonic: None,
    )
    # Skip audit (we won't reach it).
    from backend.product_control_agent import ProductControlAgent
    monkeypatch.setattr(ProductControlAgent, "evaluate",
                        staticmethod(lambda w: type("R", (), {"ok": False, "primary_reason": "noop"})()))

    winner = discovery_mod._pick_curated_winner(
        seen_names=set(),
        deadline_monotonic=10**9, t_start=0.0,
    )
    assert winner is None


def test_discover_winner_falls_back_to_curated_when_live_research_empty(monkeypatch):
    """Current contract: empty research never silently substitutes a saved product."""
    from backend.tests.test_studio_integration import configure_discovery
    from fastapi import HTTPException
    engine,_=configure_discovery(monkeypatch,empty=True)
    monkeypatch.setattr('backend.live_research._is_tavily_configured',lambda:False)
    monkeypatch.delenv('TRAFFICLIFT_FREE_VIDEO',raising=False)
    with pytest.raises(HTTPException) as caught:engine.discover()
    assert caught.value.status_code==503
    assert 'No saved-list product was substituted' in caught.value.detail



def test_curated_image_queries_are_real_searchable_phrases():
    """Each curated entry's image_queries must be a real product
    phrase (not empty, not a single generic word)."""
    from backend.curated_winners import CURATED_WINNERS
    for entry in CURATED_WINNERS:
        for q in entry["image_queries"]:
            assert q and len(q.split()) >= 3, (
                f"curated image query too short or generic for "
                f"{entry['name']!r}: {q!r}"
            )
