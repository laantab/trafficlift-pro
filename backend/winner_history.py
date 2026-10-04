"""Durable, browser-scoped discovery rotation and atomic repeat prevention."""
import hashlib
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

WINDOW_SECONDS = 30 * 86400


def identity_keys(name, url):
    # Do not use Python hash(): its output changes across server processes.
    normalized = ' '.join(re.findall(r'[a-z0-9]+', name.lower()))
    keys = ['name-' + hashlib.sha256(normalized.encode()).hexdigest()[:24]]
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_')
             and k.lower() not in {'ref', 'tag', 'fbclid', 'gclid', 'affiliate', 'affid'}]
    canonical = urlunsplit(('https', (p.hostname or '').lower().removeprefix('www.'),
                           p.path.rstrip('/'), urlencode(sorted(query)), ''))
    if p.hostname:
        keys.append('url-' + hashlib.sha256(canonical.encode()).hexdigest()[:24])
    return keys


def product_id(name, url):
    return 'discover-' + identity_keys(name, url)[0][5:]


class WinnerHistory:
    def __init__(self, scope, path=None):
        self.scope = hashlib.sha256(scope.encode()).hexdigest()
        self.path = Path(path or os.getenv('TRAFFICLIFT_DISCOVERY_DB', 'data/discovery.sqlite3'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS discovery_seen (scope TEXT, identity TEXT, expires REAL, PRIMARY KEY(scope, identity))')
            db.execute('CREATE TABLE IF NOT EXISTS discovery_rotation (scope TEXT PRIMARY KEY, cursor INTEGER, touched REAL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def seen(self):
        with self.connect() as db:
            return {row[0] for row in db.execute('SELECT identity FROM discovery_seen WHERE scope=? AND expires>?', (self.scope, time.time()))}

    def next_cursor(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM discovery_seen WHERE expires<=?', (time.time(),))
            db.execute('DELETE FROM discovery_rotation WHERE touched<?', (time.time()-WINDOW_SECONDS,))
            row = db.execute('SELECT cursor FROM discovery_rotation WHERE scope=?', (self.scope,)).fetchone()
            cursor = row[0] if row else 0
            db.execute('INSERT OR REPLACE INTO discovery_rotation VALUES (?,?,?)', (self.scope, cursor+1, time.time()))
            return cursor

    def claim(self, keys):
        # Two concurrent requests cannot accept the same name or destination.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            placeholders = ','.join('?' for _ in keys)
            if db.execute(f'SELECT 1 FROM discovery_seen WHERE scope=? AND expires>? AND identity IN ({placeholders})', (self.scope, time.time(), *keys)).fetchone():
                return False
            for key in keys:
                db.execute('INSERT OR REPLACE INTO discovery_seen VALUES (?,?,?)', (self.scope, key, time.time()+WINDOW_SECONDS))
            return True
