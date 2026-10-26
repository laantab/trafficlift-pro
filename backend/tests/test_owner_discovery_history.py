"""Owner beta sessions must share winner exclusions across browsers, not just tabs.

This is deliberately scoped to the existing authenticated operator beta; it
does not assert customer accounts exist or change anonymous browser behavior.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import discovery
from backend import winner_history as history_module
from backend.winner_history import WinnerHistory, identity_keys
from trafficlift_pro import app

OWNER_SCOPE = "owner:beta:v1"
PRODUCT = identity_keys(
    "Compact Rechargeable Desk Lamp Model A",
    "https://shop.example/products/desk-lamp?sku=MODEL-A",
)


@pytest.fixture
def owner_beta(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO", "1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN", "owner-test-secret-" + "x" * 40)
    monkeypatch.setattr(
        history_module, "default_history_path", lambda: tmp_path / "discovery.sqlite3"
    )

    def fake_discover(*, exclude_ids=None, exclude_keys=None, client_id=None, seed=None):
        assert client_id
        if not WinnerHistory(client_id).claim(PRODUCT):
            raise HTTPException(404, "No fresh product; previously selected winners remain excluded.")
        return {"name": "Compact Rechargeable Desk Lamp Model A",
                "discovery": {"winner_history_policy": "permanent_per_browser"}}

    monkeypatch.setattr(discovery, "discover_winner", fake_discover)
    with TestClient(app, base_url="https://testserver") as client:
        login = client.post(
            "/api/v1/video-owner-login",
            json={"access_code": "owner-test-secret-" + "x" * 40},
            headers={"Origin": "https://testserver"},
        )
        assert login.status_code == 200, login.text
        yield client


def test_signed_in_owner_cannot_repeat_winner_from_second_browser(owner_beta):
    first = owner_beta.get("/api/v1/discover-winner", params={"client_id": "returning-browser-1111"})
    assert first.status_code == 200, first.text
    assert first.json()["discovery"]["winner_history_policy"] == "permanent_per_authenticated_owner_beta"
    second = owner_beta.get("/api/v1/discover-winner", params={"client_id": "returning-browser-2222"})
    assert second.status_code == 404, second.text
    assert "No fresh product" in second.json()["detail"]


def test_existing_browser_winner_is_imported_into_signed_in_owner_history(owner_beta):
    assert WinnerHistory("returning-browser-3333").claim(PRODUCT)
    response = owner_beta.get("/api/v1/discover-winner", params={"client_id": "returning-browser-3333"})
    assert response.status_code == 404, response.text
    assert not WinnerHistory(OWNER_SCOPE).claim(PRODUCT)


def test_owner_history_is_not_publicly_addressable_as_browser_id(monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO", "1")
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/v1/discover-winner", params={"client_id": OWNER_SCOPE})
    assert response.status_code == 422


def test_exhausted_catalog_is_not_mislabeled_as_missing_photo():
    """A permanent-history 404 needs a truthful exhausted-catalog message."""
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "index.html").read_text(encoding="utf-8-sig")
    # Do not call every HTTP 404 a photo failure: discovery returns 404 when
    # all qualified candidates were previously selected for this owner.
    error_handler = page.split("const isNoPhotoError", 1)[1].split("const backendDetail", 1)[0]
    assert "/HTTP 404" not in error_handler.split("const heading", 1)[0]
    assert "const isNoFreshWinner" in page
    assert "No fresh product available" in error_handler
    assert "Previously selected winners remain excluded" in page
