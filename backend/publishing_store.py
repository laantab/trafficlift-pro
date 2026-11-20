"""Durable owner publishing queue, separate from campaign/render records.

All payloads (including OAuth grants and resumable-session URLs) are encrypted.
The database only indexes random IDs, deduplication hashes and queue leases.
"""
import json
import os
import sqlite3
import time
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from backend.photo_video_api import storage


def cipher():
    try:
        return Fernet(os.environ['TRAFFICLIFT_PUBLISHING_KEY'].encode())
    except (KeyError, ValueError) as exc:
        raise HTTPException(503, 'Publishing credential storage requires configuration.') from exc


def seal(value):
    return cipher().encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()


def unseal(value):
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(503, 'Publishing storage could not be unlocked.') from exc


@contextmanager
def database():
    root = storage() / 'publishing'
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / 'publishing.sqlite3', timeout=10) as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, payload TEXT NOT NULL, '
                   'PRIMARY KEY(kind,id))')
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL, '
                   'state TEXT NOT NULL, lease REAL NOT NULL DEFAULT 0, payload TEXT NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        yield db


def get(kind, identifier):
    with database() as db:
        row = db.execute('SELECT payload FROM records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
    return unseal(row['payload']) if row else None


def put(kind, identifier, value):
    with database() as db:
        db.execute('INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload',
                   (kind, identifier, seal(value)))


def pop(kind, identifier):
    with database() as db:
        row = db.execute('SELECT payload FROM records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        db.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, identifier))
    return unseal(row['payload']) if row else None


def records(kind):
    with database() as db:
        rows = db.execute('SELECT payload FROM records WHERE kind=?', (kind,)).fetchall()
    return [unseal(row['payload']) for row in rows]


def job(identifier):
    with database() as db:
        row = db.execute('SELECT payload FROM jobs WHERE id=?', (identifier,)).fetchone()
    if not row:
        raise HTTPException(404, 'Publishing job not found.')
    return unseal(row['payload'])


def create_job(value, dedupe):
    with database() as db:
        row = db.execute('SELECT payload FROM jobs WHERE dedupe=?', (dedupe,)).fetchone()
        if row:
            existing = unseal(row['payload'])
            if existing['snapshot'] != value['snapshot']:
                if existing['state'] != 'review':
                    raise HTTPException(409, 'This video already has an approved upload for this channel. Open its history.')
                value['id'] = existing['id']
                value['events'] = existing['events'] + value['events']
                db.execute('UPDATE jobs SET payload=? WHERE id=?', (seal(value), existing['id']))
                return value
            return existing
        db.execute('INSERT INTO jobs(id,dedupe,state,payload) VALUES(?,?,?,?)',
                   (value['id'], dedupe, value['state'], seal(value)))
    return value


def approve(identifier, digest):
    with database() as db:
        row = db.execute('SELECT payload FROM jobs WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, 'Publishing job not found.')
        value = unseal(row['payload'])
        if value['review_hash'] != digest:
            raise HTTPException(409, 'Review has changed. Open the current preview.')
        if value['state'] == 'review':
            value.update(state='queued', approved_at=time.time())
            value['events'].append({'at': time.time(), 'state': 'queued'})
            db.execute('UPDATE jobs SET state=?,payload=? WHERE id=?', ('queued', seal(value), identifier))
    return value


def claim(identifier):
    with database() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=? AND lease<?', (identifier, time.time())).fetchone()
        if not row or row['state'] not in {'queued', 'uploading', 'reconciling', 'uploaded', 'processing'}:
            return None
        value = unseal(row['payload'])
        db.execute('UPDATE jobs SET lease=? WHERE id=?', (time.time() + 180, identifier))
    return value


def save_job(value, release=False):
    with database() as db:
        db.execute('UPDATE jobs SET state=?,lease=?,payload=? WHERE id=?',
                   (value['state'], 0 if release else time.time() + 180, seal(value), value['id']))


def jobs():
    with database() as db:
        rows = db.execute('SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 100').fetchall()
    return [unseal(row['payload']) for row in rows]


def pending_jobs():
    # History pagination must not hide older approved work from the queue.
    with database() as db:
        rows = db.execute("SELECT payload FROM jobs WHERE state IN "
                          "('queued','uploading','reconciling','uploaded','processing') ORDER BY rowid").fetchall()
    return [unseal(row['payload']) for row in rows]
