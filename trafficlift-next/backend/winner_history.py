"""Atomic, durable per-browser discovery exclusions; no external calls."""
import hashlib,json,os,re,sqlite3,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qsl,urlencode


def identity_keys(name,url):
    parsed=urlsplit(url)
    host=(parsed.hostname or '').lower().removeprefix('www.')
    path=parsed.path.rstrip('/')
    query=urlencode(sorted((k,v) for k,v in parse_qsl(parsed.query) if not k.lower().startswith('utm_') and k not in {'tag','ref','gclid','fbclid'}))
    keys={'url:'+host+path+('?' + query if query else '')}
    asin=re.search(r'/(?:dp|gp/product)/([a-z0-9]{10})(?:/|$)',path,re.I)
    if asin and re.search(r'(^|\.)amazon\.',host):keys.add('asin:'+asin.group(1).upper())
    normalized=' '.join(re.findall(r'[a-z0-9]+',name.lower()))
    if normalized:keys.add('name:'+normalized)
    return sorted(keys)


def product_id(name,url):
    return 'live-'+hashlib.sha256(json.dumps(identity_keys(name,url),sort_keys=True).encode()).hexdigest()[:24]


class WinnerHistory:
    def __init__(self,client_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',client_id):raise ValueError('Invalid browser identifier.')
        self.client_id=client_id
        self.path=Path(os.environ.get('TRAFFICLIFT_HISTORY_DB',str(Path(os.environ.get('TRAFFICLIFT_VIDEO_DIR','video/studio'))/'winner-history.sqlite3')))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS seen (client TEXT, key TEXT, at REAL, PRIMARY KEY(client,key))')
            db.execute('CREATE TABLE IF NOT EXISTS cursor (client TEXT PRIMARY KEY, value INTEGER NOT NULL)')
    def connect(self):return sqlite3.connect(self.path,timeout=10)
    def seen(self):
        with self.connect() as db:
            return {row[0] for row in db.execute('SELECT key FROM seen WHERE client=? AND at>?',(self.client_id,time.time()-30*86400))}
    def next_cursor(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT value FROM cursor WHERE client=?',(self.client_id,)).fetchone();value=row[0] if row else 0
            db.execute('INSERT OR REPLACE INTO cursor VALUES (?,?)',(self.client_id,value+1));return value
    def claim(self,keys):
        keys=set(keys)
        if not keys:return False
        now=time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM seen WHERE at<?',(now-30*86400,))
            placeholders=','.join('?' for _ in keys)
            if db.execute(f'SELECT 1 FROM seen WHERE client=? AND key IN ({placeholders}) LIMIT 1',(self.client_id,*keys)).fetchone():return False
            db.executemany('INSERT INTO seen VALUES (?,?,?)',[(self.client_id,key,now) for key in keys]);return True
