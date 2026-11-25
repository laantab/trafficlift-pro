"""HTTP integration checks for the read-only analytics router."""
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.analytics_readonly import router


def client_for(monkeypatch, tmp_path, rows=0):
    path = tmp_path / "campaigns.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE campaigns (id TEXT, product_title TEXT, input_url TEXT, mode TEXT, channels TEXT, created_at TEXT)")
        for i in range(rows):
            db.execute("INSERT INTO campaigns VALUES (?,?,?,?,?,?)",
                       (str(i), "Product", "https://example.com", "organic", "[]", f"2026-11-{i%20+1:02}"))
    monkeypatch.setenv("CAMPAIGNS_DB_PATH", str(path))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), path


def test_endpoint_and_limit(monkeypatch, tmp_path):
    client, path = client_for(monkeypatch, tmp_path, 3)
    response = client.get("/api/v1/analytics/overview?limit=2")
    assert response.status_code == 200
    assert response.json()["campaign_count"] == 3
    assert len(response.json()["campaigns"]) == 2
    assert response.json()["performance"]["orders"] is None


def test_reject_invalid_limits(monkeypatch, tmp_path):
    client, _ = client_for(monkeypatch, tmp_path)
    assert client.get("/api/v1/analytics/overview?limit=0").status_code == 422
    assert client.get("/api/v1/analytics/overview?limit=101").status_code == 422


def test_endpoint_read_only(monkeypatch, tmp_path):
    client, path = client_for(monkeypatch, tmp_path, 2)
    before = path.read_bytes()
    assert client.get("/api/v1/analytics/overview").status_code == 200
    assert path.read_bytes() == before
