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
    # Official ad/analytics click attribution: TikTok click ID, Google's
    # cross-domain linker, and Pinterest's promoted-traffic click identifier.
    # Use exact keys only; never strip product-specific sku/variant/size fields.
    'ttclid', '_gl', 'epik',
}

# Scraped product URLs sometimes contain HTML-escaped query separators.
# Decode only ampersand entities in the QUERY, not paths, product values, or
# arbitrary HTML entities (which could merge genuinely different SKUs).
_HTML_QUERY_AMPERSAND = re.compile(r'&(?:amp|#0*38|#x0*26);', re.I)

# RFC 3986 unreserved bytes are the same URL path characters whether a
# retailer percent-encodes them or not. Never decode reserved separators
# (especially %2F): doing so could merge different products or routes.
_UNRESERVED_PATH = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')


def _canonical_query_pairs(pairs):
    """Normalize only retailer SKU selectors; keep all distinct selector values.

    Integrations sometimes repeat an identical ?sku or ?variant parameter or
    change its capitalization. Both are one product identity, but conflicting
    values remain separate. Do not rewrite unrelated, possibly case-sensitive
    query fields or SKU *values*.
    """
    cleaned = []
    seen_selectors = set()
    for key, value in pairs:
        lowered = key.lower()
        if lowered.startswith('utm_') or lowered in _TRACKING_PARAMS:
            continue
        if lowered in {'sku', 'variant'}:
            # Retailer redirects sometimes URL-encode padding around a
            # selector value. Boundary whitespace does not change a SKU, but
            # interior whitespace can be meaningful (e.g. AB 12 vs AB12).
            pair = (lowered, value.strip())
            if pair in seen_selectors:
                continue
            seen_selectors.add(pair)
        else:
            pair = (key, value)
        cleaned.append(pair)
    return sorted(cleaned)


def _canonical_product_path(path):
    def normalize_escape(match):
        value = chr(int(match.group(1), 16))
        return value if value in _UNRESERVED_PATH else '%' + match.group(1).upper()
    return re.sub(r'%([0-9A-Fa-f]{2})', normalize_escape, path).rstrip('/')


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
    # Retain the legacy name key so existing ASCII winner records still match.
    # Also store a Unicode-folded key: the legacy ASCII regex drops accented
    # letters ("Café Mug" became "caf mug"), letting short renamed listings
    # repeat when the URL changed. Short titles intentionally do not get the
    # broader sorted-token key, but their exact folded form is still stable.
    folded_name = ' '.join(tokens)
    if folded_name:
        keys.append('name-folded-' + hashlib.sha256(folded_name.encode()).hexdigest()[:24])
    # A conservative second key catches reordered titles, UK/US spelling,
    # and piece/pc variants without conflating short generic names.
    if len(tokens) >= 3 and len(set(tokens)) >= 3:
        canonical_title = ' '.join(sorted(tokens))
        keys.append('title-' + hashlib.sha256(canonical_title.encode()).hexdigest()[:24])
    # Retailers alternate "12oz" / "12 oz" and "500ml" / "500 ml" when
    # relisting the same item. Add *both* spelling keys without replacing the
    # existing hashes: old durable records created before this fix must still
    # exclude the relisted product. Keep the quantity and unit so other sizes
    # and model numbers are not accidentally treated as the same SKU.
    units = r'(?:oz|ml|cm|mm|kg|lbs?)'
    measurement_variants = {
        re.sub(r'\b(\d+)\s+(' + units + r')\b', r'\1\2', folded_name),
        re.sub(r'\b(\d+)(' + units + r')\b', r'\1 \2', folded_name),
    }
    # Seller titles interchange 12in, 12 inch, 12 inches, and 12-inch.
    # Emit all historical folded spellings so pre-upgrade SQLite hashes
    # remain effective even when a retailer changes the listing URL.
    # Do not interpret numeric "2 in 1" feature descriptions as dimensions.
    inch_pattern = r'\b(\d+)\s*(?:inches|inch|in)\b(?!\s+\d)'
    if re.search(inch_pattern, folded_name):
        for spelling in ('in', 'inch', 'inches'):
            measurement_variants.add(re.sub(
                inch_pattern, lambda match: f'{match.group(1)} {spelling}', folded_name
            ))
        measurement_variants.add(re.sub(
            inch_pattern, lambda match: f'{match.group(1)}in', folded_name
        ))
    for alternate in sorted(measurement_variants - {folded_name}):
        if not alternate:
            continue
        keys.append('name-folded-' + hashlib.sha256(alternate.encode()).hexdigest()[:24])
        alt_tokens = alternate.split()
        if len(alt_tokens) >= 3 and len(set(alt_tokens)) >= 3:
            keys.append('title-' + hashlib.sha256(' '.join(sorted(alt_tokens)).encode()).hexdigest()[:24])
    # Decimal/hex HTML entities contain '#', which urlsplit otherwise treats
    # as the start of a fragment. Decode ampersand separators in the query
    # portion *before* URL splitting, never in the product path.
    raw_p = urlsplit(url)
    if '?' in url:
        prefix, query_tail = url.split('?', 1)
        p = urlsplit(prefix + '?' + _HTML_QUERY_AMPERSAND.sub('&', query_tail))
    else:
        p = raw_p
    canonical_path = _canonical_product_path(p.path)
    query_pairs = parse_qsl(p.query)
    query = _canonical_query_pairs(query_pairs)
    host_name = (p.hostname or '').lower().removeprefix('www.')
    canonical = urlunsplit(('https', host_name, canonical_path,
                           urlencode(sorted(query)), ''))
    if p.hostname:
        keys.append('url-' + hashlib.sha256(canonical.encode()).hexdigest()[:24])
        # Preserve the pre-upgrade identity for an already stored encoded URL.
        # Historical hashes alone cannot recover a *different* old encoding;
        # all newly claimed winners store the normalized key going forward.
        # Also retain the pre-upgrade raw-entity URL hash for existing
        # permanent records saved before HTML query normalization.
        legacy_query = [(k, v) for k, v in parse_qsl(raw_p.query)
                        if not k.lower().startswith('utm_')
                        and k.lower() not in _TRACKING_PARAMS]
        legacy = urlunsplit(('https', host_name, raw_p.path.rstrip('/'),
                             urlencode(sorted(legacy_query)), ''))
        if legacy != canonical:
            keys.append('url-' + hashlib.sha256(legacy.encode()).hexdigest()[:24])
        # Amazon product pages use several equivalent paths for one ASIN.
        # Keep the merchant-scoped product ID separate from the full URL,
        # without merging unrelated products whose names share generic words.
        host = (p.hostname or '').lower().removeprefix('www.')
        # Shopify variant IDs are merchant-specific and remain stable when a
        # seller changes the /products/<slug> or listing title. Do not apply
        # this to arbitrary ?variant=1 selectors used by other storefronts.
        # A real numeric Shopify variant remains distinct from another SKU.
        # Shopify Markets can add a language/region prefix to product URLs.
        # The same numeric variant must stay excluded across locale changes,
        # even if the merchant also renames the product and URL slug.
        # Only accept a root or single locale prefix, never arbitrary paths.
        if re.fullmatch(r'/(?:[a-z]{2,3}(?:-[a-z]{2})?/)?products/[^/]+/?', canonical_path, re.I):
            # A seller redirect may append the same variant selector twice
            # (sometimes with different parameter capitalization). Those
            # identical values still name one SKU; do not let the duplicate
            # query keys bypass the merchant-scoped permanent winner history.
            # Conflicting values are ambiguous: never choose one arbitrarily.
            variants = {v.strip() for k, v in query_pairs if k.lower() == 'variant'}
            if len(variants) == 1:
                variant = next(iter(variants))
                if re.fullmatch(r'[0-9]{8,20}', variant):
                    merchant_variant = f'{host}:{variant}'
                    keys.append('shopify-variant-' + hashlib.sha256(
                        merchant_variant.encode()).hexdigest()[:24])
        if host == 'amazon.com' or host.endswith('.amazon.com'):
            match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([a-z0-9]{10})(?:/|$)', canonical_path, re.I)
            if match:
                keys.append('amazon-asin-' + match.group(1).lower())
        elif host == 'walmart.com' or host.endswith('.walmart.com'):
            match = re.search(r'/ip/(?:[^/]+/)?([0-9]{6,})(?:/|$)', canonical_path, re.I)
            if match:
                keys.append('walmart-item-' + match.group(1))
        elif host == 'target.com' or host.endswith('.target.com'):
            # Target's search-result links may omit or rewrite the product
            # slug while retaining the /-/A-<TCIN> product identifier.
            # Target's ?preselect=<child TCIN> selects a specific variant.
            # Claim the child's stable TCIN (also used by its direct listing),
            # never the parent TCIN. This blocks relisted child URLs without
            # conflating other sizes, packs, or the unselected parent.
            match = re.search(r'/p/(?:[^/]+/)?-/A-([0-9]{6,})(?:/|$)', canonical_path, re.I)
            preselected = next((v for k, v in query_pairs
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
