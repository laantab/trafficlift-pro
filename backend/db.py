"""
trafficlift_pro/backend/db.py
SQLite Persistence Layer — Campaign History

Stores every generated campaign so users can reload and reuse past assets
without re-scraping the original URL.

Schema:
  campaigns
    id            TEXT PRIMARY KEY  (UUID)
    input_url     TEXT NOT NULL
    mode          TEXT NOT NULL      -- 'organic' | 'paid'
    channels      TEXT NOT NULL      -- JSON array of channel slugs
    budget        REAL DEFAULT 25.0
    product_title TEXT
    product_image TEXT
    assets        TEXT NOT NULL      -- JSON blob: full compiled_package
    ai_mode       TEXT NOT NULL      -- 'openai' | 'minimax' | 'template_fallback'
    created_at     TEXT NOT NULL     -- ISO 8601 UTC timestamp

Index: created_at DESC for fast history queries.
"""

from __future__ import annotations

import os
import uuid
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional
from contextlib import contextmanager

from sqlalchemy import (
    create_engine,
    Column,
    String,
    Text,
    Float,
    DateTime,
    Index,
    text,
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session

logger = logging.getLogger(__name__)

Base = declarative_base()

# ─────────────────────────────────────────────────────────────────────────────
# ORM Model
# ─────────────────────────────────────────────────────────────────────────────

class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    input_url = Column(Text, nullable=False)
    mode = Column(String(20), nullable=False)
    channels = Column(Text, nullable=False)          # JSON list
    budget = Column(Float, default=25.0)
    product_title = Column(Text, nullable=True)
    product_image = Column(Text, nullable=True)
    assets = Column(Text, nullable=False)            # JSON blob
    ai_mode = Column(String(30), nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_campaigns_created_at", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "input_url": self.input_url,
            "mode": self.mode,
            "channels": json.loads(self.channels),
            "budget": self.budget,
            "product_title": self.product_title,
            "product_image": self.product_image,
            "assets": json.loads(self.assets),
            "ai_mode": self.ai_mode,
            "created_at": self.created_at.isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Database Manager
# ─────────────────────────────────────────────────────────────────────────────

class CampaignDB:
    """
    Thread-safe SQLite campaign store.

    Database file is stored next to this file (same directory as this module)
    so it lives at:  <project_root>/backend/campaigns.db
    """

    _local = threading.local()

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # 1. Explicit env var wins (use this on Render with a persistent disk).
            env_path = os.getenv("CAMPAIGNS_DB_PATH", "").strip()
            if env_path:
                db_path = env_path
            else:
                # 2. Fallback: store alongside this file (local dev).
                base_dir = os.path.dirname(os.path.abspath(__file__))
                db_path = os.path.join(base_dir, "campaigns.db")

        # Make sure the parent directory exists (Render persistent disk mount point
        # like /var/data is already there, but local dev or first-run may need it).
        db_dir = os.path.dirname(os.path.abspath(db_path))
        if db_dir and not os.path.isdir(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except OSError as exc:
                logger.warning("Could not create DB directory %s: %s", db_dir, exc)

        self._db_url = f"sqlite:///{db_path.replace(os.sep, '/')}"
        self._engine = create_engine(
            self._db_url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
            echo=False,
        )
        # Create tables
        Base.metadata.create_all(self._engine)
        self._SessionFactory = sessionmaker(bind=self._engine, expire_on_commit=False)
        logger.info("CampaignDB initialized at %s", db_path)

    @contextmanager
    def _session(self) -> Session:
        """Thread-local session context manager."""
        sess = self._SessionFactory()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def save(
        self,
        input_url: str,
        mode: str,
        channels: list[str],
        budget: float,
        product_title: Optional[str],
        product_image: Optional[str],
        assets: dict,
        ai_mode: str,
    ) -> Campaign:
        """
        Persist a new campaign and return the Campaign record.
        """
        record = Campaign(
            id=str(uuid.uuid4()),
            input_url=input_url,
            mode=mode,
            channels=json.dumps(channels),
            budget=budget,
            product_title=product_title,
            product_image=product_image,
            assets=json.dumps(assets),
            ai_mode=ai_mode,
            created_at=datetime.now(timezone.utc),
        )
        with self._session() as sess:
            sess.add(record)
        logger.info("Saved campaign id=%s url=%s mode=%s", record.id, input_url, mode)
        return record

    def get_by_id(self, campaign_id: str) -> Optional[dict]:
        """Retrieve a single campaign by its UUID."""
        with self._session() as sess:
            record = sess.query(Campaign).filter(Campaign.id == campaign_id).first()
            if record:
                return record.to_dict()
        return None

    def list_history(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """
        Return the most recent campaigns, newest first.

        Args:
            limit:  max records to return (default 50)
            offset: pagination offset
        """
        with self._session() as sess:
            records = (
                sess.query(Campaign)
                .order_by(Campaign.created_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in records]

    def count(self) -> int:
        """Total number of stored campaigns."""
        with self._session() as sess:
            return sess.query(Campaign).count()

    def delete(self, campaign_id: str) -> bool:
        """Delete a campaign by ID. Returns True if a record was deleted."""
        with self._session() as sess:
            n = (
                sess.query(Campaign)
                .filter(Campaign.id == campaign_id)
                .delete(synchronize_session=False)
            )
        return n > 0

    def clear_all(self) -> int:
        """Delete all campaigns. Returns count of deleted records."""
        with self._session() as sess:
            count = sess.query(Campaign).delete(synchronize_session=False)
        return count
