"""Durable, browser-scoped discovery rotation and atomic repeat prevention."""
import hashlib
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import unicodedata

# Keep product sizes, quantities, model numbers and brand words intact.
# These equivalences only cover common title spelling/packaging variations.
_TITLE_ALIASES = {
    'organiser': 'organizer', 'organisers': 'organizer',
    'organizers': 'organizer', 'organizer': 'organizer',
    'colour': 'color', 'colours': 'color', 'colors': 'color',
    'pcs': 'piece', 'pc': 'piece', 'pieces': 'piece',
    'lights': 'light', 'lamps': 'lamp', 'bottles': 'bottle',
    'drawers': 'drawer', 'holders': 'holder', 'racks': 'rack',
}
_TRACKING_PARAMS = {
    'ref', 'ref_', 'refid', 'referrer', 'referral', 'referral_id', 'tag',
    'fbclid', 'gclid', 'affiliate', 'affiliate_id', 'affid', 'aff_id',
    'mc_cid', 'mc_eid', 'igshid', 'srsltid', 'clickid', 'click_id',
    'campaign', 'campaignid', 'campaign_id', 'cid', 'spm', 'ascsubtag', 'subid',
    'sub_id', 'irclickid', 'irgwc', 'gclsrc', 'twclid', 'msclkid',
    # Shopify search placement and Google Ads attribution do not select SKUs.
    # Keep meaningful variant, size, sku, and model selectors in the URL.
    '_pos', '_sid', '_ss', 'gad_source', 'gad_campaignid', 'gbraid', 'wbraid',
}

def _title_tokens(name):
    ascii_name = unicodedata.normalize('NFKD', name.casefold()).encode('ascii', 'ignore').decode('ascii')
    # Seller listings often join quantities to the unit: 18pc, 18pcs,
    # 18-piece and 18 pieces describe the same pack, not new winners.
    # Keep the number so 18-piece and 20-piece remain different products.
    ascii_name = re.sub(r'\b(\d+)\s*-?\s*(?:pieces?|pcs?)\b', r'\1 piece', ascii_name)
    return [_TITLE_ALIASES.get(word, word) for word in re.findall(r'[a-z0-9]+', ascii_name)]


WINDOW_SECONDS = 30 * 86400  # Category rotation only; winning products never expire


def identity_keys(name, url):
    # Do not use Python hash(): its output changes across server processes.
    normalized = ' '.join(re.findall(r'[a-z0-9]+', name.lower()))
    keys = ['name-' + hashlib.sha256(normalized.encode()).hexdigest()[:24]]
    tokens = _title_tokens(name)
    # A conservative second key catches reordered titles, UK/US spelling,
    # and piece/pc variants without conflating short generic names.
    if len(tokens) >= 3 and len(set(tokens)) >= 3:
        canonical_title = ' '.join(sorted(tokens))
        keys.append('title-' + hashlib.sha256(canonical_title.encode()).hexdigest()[:24])
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_')
             and k.lower() not in _TRACKING_PARAMS]
    canonical = urlunsplit(('https', (p.hostname or '').lower().removeprefix('www.'),
                           p.path.rstrip('/'), urlencode(sorted(query)), ''))
    if p.hostname:
        keys.append('url-' + hashlib.sha256(canonical.encode()).hexdigest()[:24])
        # Amazon product pages use several equivalent paths for one ASIN.
        # Keep the merchant-scoped product ID separate from the full URL,
        # without merging unrelated products whose names share generic words.
        host = (p.hostname or '').lower().removeprefix('www.')
        # Shopify variant IDs are merchant-specific and remain stable when a
        # seller changes the /products/<slug> or listing title. Do not apply
        # this to arbitrary ?variant=1 selectors used by other storefronts.
        # A real numeric Shopify variant remains distinct from another SKU.
        if re.fullmatch(r'/products/[^/]+/?', p.path, re.I):
            variants = [v for k, v in parse_qsl(p.query) if k.lower() == 'variant']
            if len(variants) == 1 and re.fullmatch(r'[0-9]{8,20}', variants[0]):
                merchant_variant = f'{host}:{variants[0]}'
                keys.append('shopify-variant-' + hashlib.sha256(
                    merchant_variant.encode()).hexdigest()[:24])
        if host == 'amazon.com' or host.endswith('.amazon.com'):
            match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([a-z0-9]{10})(?:/|$)', p.path, re.I)
            if match:
                keys.append('amazon-asin-' + match.group(1).lower())
        elif host == 'walmart.com' or host.endswith('.walmart.com'):
            match = re.search(r'/ip/(?:[^/]+/)?([0-9]{6,})(?:/|$)', p.path, re.I)
            if match:
                keys.append('walmart-item-' + match.group(1))
        elif host == 'target.com' or host.endswith('.target.com'):
            # Target's search-result links may omit or rewrite the product
            # slug while retaining the /-/A-<TCIN> product identifier.
            # Target's ?preselect=<child TCIN> selects a specific variant.
            # Claim the child's stable TCIN (also used by its direct listing),
            # never the parent TCIN. This blocks relisted child URLs without
            # conflating other sizes, packs, or the unselected parent.
            match = re.search(r'/p/(?:[^/]+/)?-/A-([0-9]{6,})(?:/|$)', p.path, re.I)
            preselected = next((v for k, v in parse_qsl(p.query)
                                if k.lower() == 'preselect' and v), None)
            if preselected:
                if re.fullmatch(r'[0-9]{6,}', preselected):
                    keys.append('target-tcin-' + preselected)
            elif match:
                keys.append('target-tcin-' + match.group(1))
    return keys


def product_id(name, url):
    return 'discover-' + identity_keys(name, url)[0][5:]


def default_history_path():
    """Keep winner exclusions on the same durable disk as saved campaigns.

    The old project-relative default was ephemeral on Render even when the
    campaign DB used /var/data. Explicit overrides remain authoritative.
    """
    configured = os.getenv('TRAFFICLIFT_DISCOVERY_DB', '').strip()
    if configured:
        return Path(configured)
    campaigns = os.getenv('CAMPAIGNS_DB_PATH', '').strip()
    if campaigns:
        return Path(campaigns).expanduser().resolve().parent / 'discovery.sqlite3'
    if os.getenv('RENDER_SERVICE_ID') and Path('/var/data').is_dir():
        return Path('/var/data/discovery.sqlite3')
    return Path('data/discovery.sqlite3')


class WinnerHistory:
    def __init__(self, scope, path=None):
        self.scope = hashlib.sha256(scope.encode()).hexdigest()
        self.path = Path(path) if path is not None else default_history_path()
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
            return {row[0] for row in db.execute('SELECT identity FROM discovery_seen WHERE scope=?', (self.scope,))}

    def next_cursor(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
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
            if db.execute(f'SELECT 1 FROM discovery_seen WHERE scope=? AND identity IN ({placeholders})', (self.scope, *keys)).fetchone():
                return False
            for key in keys:
                db.execute('INSERT OR REPLACE INTO discovery_seen VALUES (?,?,?)', (self.scope, key, time.time()+WINDOW_SECONDS))  # legacy expires field retained for old databases
            return True
