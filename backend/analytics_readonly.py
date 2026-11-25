"""Read-only campaign analytics API prototype.

No paid calls, network access, database writes, or changes to existing campaign
schema. This router is deliberately NOT registered in the production app yet.
Missing performance data is represented as null, never as zero sales.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _database_path() -> str:
    return os.getenv("CAMPAIGNS_DB_PATH") or str(Path(__file__).with_name("campaigns.db"))


def summarize_campaigns(db_path: str, limit: int = 50) -> dict:
    """Read campaign metadata only; existing history is NOT performance data."""
    if not os.path.isfile(db_path):
        return {"campaign_count": 0, "campaigns": [], "performance": _unknown_performance(),
                "status": "no_campaign_database"}
    try:
        # SQLite URI mode=ro guarantees this endpoint cannot modify the database.
        from urllib.parse import quote
        uri = "file:" + quote(os.path.abspath(db_path), safe="/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=3) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaigns'"
            ).fetchone()
            if not table:
                return {"campaign_count": 0, "campaigns": [],
                        "performance": _unknown_performance(), "status": "no_campaign_table"}
            count = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
            rows = conn.execute(
                "SELECT id, product_title, input_url, mode, channels, created_at "
                "FROM campaigns ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            campaigns = [dict(row) for row in rows]
    except sqlite3.Error as exc:
        raise ValueError("Campaign analytics database is unavailable") from exc
    return {"campaign_count": count, "campaigns": campaigns,
            "performance": _unknown_performance(), "status": "campaign_metadata_only"}


def _unknown_performance() -> dict:
    return {"impressions": None, "views": None, "clicks": None, "orders": None,
            "revenue": None, "spend": None, "ctr": None, "roi": None,
            "data_quality": "not_connected",
            "note": "Campaign history does not contain verified platform or sales metrics."}


@router.get("/overview")
def overview(limit: int = Query(50, ge=1, le=100)) -> dict:
    try:
        return summarize_campaigns(_database_path(), limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
