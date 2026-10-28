"""Regression: transient configured research outages must not disable Find Winner.

All network, images, product evidence and history are controlled fixtures. This
test does NOT assert that any particular retailer image is licensed.
"""
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend import discovery_engine, live_research


PRODUCT = "JAS11242 Motion Sensor Night Light"
LISTING = "https://www.amazon.com/dp/B012345678"
PHOTO = "https://images.example.test/JAS11242-product.jpg"


def _public_hits():
    # Two independently originated, concrete URLs; never synthetic claims.
    return [
        live_research.ResearchSource(PRODUCT, "JAS11242 Motion Sensor Night Light with motion activation", LISTING, "brave_web"),
        live_research.ResearchSource(PRODUCT + " review", "JAS11242 Motion Sensor Night Light review", "https://review.example.test/jas11242", "brave_web"),
    ]


@pytest.mark.parametrize("failure", [500, 502, 503, 504, 520, "timeout", "connection", "empty"])
def test_transient_tavily_uses_independent_evidence(monkeypatch, failure):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-test-key")
    def tavily_post(*args, **kwargs):
        if failure == "timeout":
            raise requests.exceptions.Timeout("simulated")
        if failure == "connection":
            raise requests.exceptions.ConnectionError("simulated")
        if failure == "empty":
            return SimpleNamespace(status_code=200, json=lambda: {"results": [], "images": []})
        return SimpleNamespace(status_code=failure)
    monkeypatch.setattr(live_research.requests, "post", tavily_post)
    monkeypatch.setattr(live_research, "_query_brave", lambda q, n: (_public_hits(), []))
    result = live_research.research("JAS11242 Motion Sensor Night Light")
    assert result.research_status in {"live", "partial"}
    assert result.research_provider == "brave_web"
    assert {s["url"] for s in result.research_sources} == {LISTING, "https://review.example.test/jas11242"}
    assert all(s["provider"] == "brave_web" for s in result.research_sources)
    if failure != "empty":
        assert result.research_errors


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_tavily_client_or_auth_error_is_not_masked(monkeypatch, status):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-test-key")
    monkeypatch.setattr(live_research.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=status))
    called = []
    monkeypatch.setattr(live_research, "_query_brave", lambda *a: (called.append(True), [])[1])
    result = live_research.research("JAS11242 Motion Sensor Night Light")
    assert result.research_status == "fallback"
    assert result.research_provider == "tavily"
    assert result.research_sources == []
    assert f"Tavily HTTP {status}" in result.research_errors
    assert not called


def test_tavily_502_public_provider_qualifies_exact_photo_winner(monkeypatch):
    """Exercise _discover_batch with real research envelope and independent sources."""
    from backend import discovery, listing_photo, product_control_agent, product_research

    monkeypatch.setenv("TAVILY_API_KEY", "fake-test-key")
    monkeypatch.setattr(live_research.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=502))
    monkeypatch.setattr(live_research, "_query_brave", lambda q, n: (_public_hits(), []))
    monkeypatch.setattr(discovery_engine, "query_plan", lambda *a, **k: [PRODUCT])
    monkeypatch.setattr(discovery_engine, "bounded_map",
                        lambda fn, values, seconds: [x for v in values if (x := fn(v)) is not None])
    class History:
        def __init__(self, client_id): pass
        def next_cursor(self): return 0
        def seen(self): return set()
        def claim(self, keys): return True
    monkeypatch.setattr(discovery_engine, "WinnerHistory", History)
    def card_from_envelope(env, titles):
        assert env.research_provider == "brave_web"
        assert len(env.research_sources) == 2
        return [SimpleNamespace(id="test-jas11242", name=PRODUCT, url=LISTING,
                                category="Lighting", image_url="")]
    monkeypatch.setattr(discovery, "_build_candidates_from_envelope", card_from_envelope)
    monkeypatch.setattr(listing_photo, "listing_images",
                        lambda name, url, **kwargs: [PHOTO] if url == LISTING else [])
    monkeypatch.setattr(product_control_agent, "rank_image_candidates",
                        lambda urls, **kwargs: [(u, 100) for u in urls])
    monkeypatch.setattr(product_control_agent, "_validate_image",
                        lambda url, **kwargs: SimpleNamespace(ok=(url == PHOTO)))
    monkeypatch.setattr(product_research.ProductResearcher, "_materialize",
                        lambda self, card, category: {"name": card.name, "url": card.url})
    monkeypatch.setattr(product_control_agent.ProductControlAgent, "evaluate",
                        lambda payload: SimpleNamespace(ok=True, product=dict(payload), reasons=[]))
    winner = discovery_engine._discover_batch(client_id="fixture-browser", seed=1)
    assert winner["url"] == LISTING
    assert winner["image_url"] == PHOTO
    assert winner["image_status"] == "verified"
    assert winner["research_provider"] == "brave_web"
    assert {source["url"] for source in winner["research_sources"]} == {
        LISTING, "https://review.example.test/jas11242"}
    assert winner["discovery"]["winner_evidence_count"] == 2


def test_tavily_401_discovery_fails_closed(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-test-key")
    monkeypatch.setattr(live_research.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=401))
    monkeypatch.setattr(discovery_engine, "query_plan", lambda *a, **k: [PRODUCT])
    monkeypatch.setattr(discovery_engine, "bounded_map",
                        lambda fn, values, seconds: [fn(v) for v in values])
    class History:
        def __init__(self, client_id): pass
        def next_cursor(self): return 0
        def seen(self): return set()
    monkeypatch.setattr(discovery_engine, "WinnerHistory", History)
    with pytest.raises(HTTPException) as exc:
        discovery_engine._discover_batch(client_id="fixture-browser")
    assert exc.value.status_code == 503
    assert "Tavily HTTP 401" in str(exc.value.detail)


def test_all_providers_empty_does_not_invent_product(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-test-key")
    monkeypatch.setattr(live_research.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=502))
    for provider in ("_query_brave", "_query_duckduckgo", "_query_bing_html", "_query_bing_rss"):
        monkeypatch.setattr(live_research, provider, lambda *a, **k: ([], []))
    result = live_research.research(PRODUCT)
    assert result.research_status == "fallback"
    assert not result.research_sources
    assert result.research_provider == "none"
    assert "Tavily HTTP 502" in result.research_errors
