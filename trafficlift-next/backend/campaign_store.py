"""Local campaign snapshots. Independent of old campaign and video history."""
import json, os, sqlite3, time
from contextlib import contextmanager
from pathlib import Path

@contextmanager
def db():
    path=Path(os.environ.get('TRAFFICLIFT_WORKSPACE_DB', str(Path(os.environ.get('TRAFFICLIFT_VIDEO_DIR','video/studio')).parent/'workspace-v5.sqlite3')))
    path.parent.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(path,timeout=20)
    try:
        with connection:
            connection.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, updated REAL NOT NULL, data TEXT NOT NULL)')
            yield connection
    finally:
        connection.close()

def settings():
    with db() as c:row=c.execute('SELECT data FROM settings WHERE id=1').fetchone()
    return json.loads(row[0]) if row else None

def put_settings(data):
    with db() as c:c.execute('INSERT OR REPLACE INTO settings VALUES (1,?)',(json.dumps(data),))
    return data

def save(data):
    data=dict(data,updated_at=time.time())
    with db() as c:c.execute('INSERT OR REPLACE INTO campaigns VALUES (?,?,?)',(data['id'],data['updated_at'],json.dumps(data)))
    return data

def get(cid):
    with db() as c:row=c.execute('SELECT data FROM campaigns WHERE id=?',(cid,)).fetchone()
    return json.loads(row[0]) if row else None

def update(cid,**fields):
    # Read-modify-write inside one lock, never lose independent platform edits.
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT data FROM campaigns WHERE id=?',(cid,)).fetchone()
        if not row:return None
        data=json.loads(row[0]);data.update(fields,updated_at=time.time())
        c.execute('UPDATE campaigns SET updated=?,data=? WHERE id=?',(data['updated_at'],json.dumps(data),cid))
    return data

def all_campaigns():
    with db() as c:rows=c.execute('SELECT data FROM campaigns ORDER BY updated DESC LIMIT 200').fetchall()
    return [json.loads(r[0]) for r in rows]

def mutate(cid,callback):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT data FROM campaigns WHERE id=?',(cid,)).fetchone()
        if not row:return None
        data=json.loads(row[0]);callback(data);data['updated_at']=time.time()
        c.execute('UPDATE campaigns SET updated=?,data=? WHERE id=?',(data['updated_at'],json.dumps(data),cid))
    return data
