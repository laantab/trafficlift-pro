"""Encrypted, transactional campaign pipeline. Publication attempts survive restarts."""
import json
import sqlite3
from contextlib import contextmanager
from backend.photo_video_api import storage
from backend.publishing_store import seal, unseal


@contextmanager
def database():
    root = storage() / 'pipeline'
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / 'pipeline.sqlite3', timeout=10) as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS entities (kind TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(kind,id))')
        db.execute('CREATE TABLE IF NOT EXISTS posts (id TEXT PRIMARY KEY, dedupe TEXT UNIQUE, state TEXT, payload TEXT NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        yield db


def get(kind, identifier):
    with database() as db:
        row = db.execute('SELECT payload FROM entities WHERE kind=? AND id=?', (kind, identifier)).fetchone()
    return unseal(row['payload']) if row else None


def put(kind, identifier, value):
    with database() as db:
        write(db, kind, identifier, value)


def write(db, kind, identifier, value):
    db.execute('INSERT INTO entities VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload',
               (kind, identifier, seal(value)))


def records(kind):
    with database() as db:
        rows = db.execute('SELECT payload FROM entities WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
    return [unseal(row['payload']) for row in rows]


def posts():
    with database() as db:
        rows = db.execute('SELECT payload FROM posts ORDER BY rowid DESC').fetchall()
    return [unseal(row['payload']) for row in rows]


def post(identifier):
    return next((p for p in posts() if p['id'] == identifier), None)


def save_post(value):
    with database() as db:
        db.execute('UPDATE posts SET state=?,payload=? WHERE id=?', (value['state'], seal(value), value['id']))


def snapshot(post_id, value):
    # Source and reporting-window identity allow corrected provider reports, not additive totals.
    key = json.dumps([post_id, value['source'], value['start'], value['end'], value['observed_at']])
    put('metric', key, {'post_id': post_id, **value})
