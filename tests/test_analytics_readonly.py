"""Focused regression tests for isolated analytics prototype."""
import sqlite3

from backend.analytics_readonly import summarize_campaigns


def test_missing_database_does_not_invent_metrics(tmp_path):
    data = summarize_campaigns(str(tmp_path / "missing.db"))
    assert data["campaign_count"] == 0
    assert data["performance"]["clicks"] is None
    assert data["performance"]["orders"] is None


def test_existing_campaigns_are_read_only_and_not_sales(tmp_path):
    path = tmp_path / "campaigns.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE campaigns (id TEXT, product_title TEXT, input_url TEXT, mode TEXT, channels TEXT, created_at TEXT)")
        db.execute("INSERT INTO campaigns VALUES (?,?,?,?,?,?)",
                   ("abc", "A Season for Everyone", "https://www.amazon.com/dp/B0HQ84DS7S",
                    "organic", '["youtube"]', "2026-11-20T00:00:00"))
    before = path.read_bytes()
    result = summarize_campaigns(str(path))
    assert result["campaign_count"] == 1
    assert result["campaigns"][0]["product_title"] == "A Season for Everyone"
    assert result["performance"]["revenue"] is None
    assert result["performance"]["ctr"] is None
    assert path.read_bytes() == before


def test_empty_table_is_not_a_verified_zero(tmp_path):
    path = tmp_path / "campaigns.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE campaigns (id TEXT, product_title TEXT, input_url TEXT, mode TEXT, channels TEXT, created_at TEXT)")
    result = summarize_campaigns(str(path))
    assert result["campaign_count"] == 0
    assert result["performance"]["views"] is None
