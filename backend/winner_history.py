"""Durable, browser-scoped discovery rotation and atomic repeat prevention."""
import hashlib
import html
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from itertools import product as cartesian_product
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import unicodedata

# Keep product sizes, quantities, model numbers and brand words intact.
# These equivalences only cover common title spelling/packaging variations.
_TITLE_ALIASES = {
    'organiser': 'organizer', 'organisers': 'organizer',
    'organizers': 'organizer', 'organizer': 'organizer',
    'colour': 'color', 'colours': 'color', 'colors': 'color',
    'grey': 'gray',
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
    # DoubleClick and LinkedIn first-party ad click IDs identify visits,
    # not product variants. Keep exact keys; never strip sku/model selectors.
    'dclid', 'li_fat_id',
    # Shopify product-recommendation placement/source context, not the SKU
    # being viewed. Preserve variant, sku, selling_plan and other selectors.
    'pr_prod_strat', 'pr_rec_id', 'pr_ref_pid', 'pr_seq',
}

# eBay listing and variation IDs are stable when a buyer opens the same item
# through another official country marketplace. Use an explicit allowlist;
# matching arbitrary ebay.* hostnames would trust unrelated domains.
_EBAY_MARKETPLACES = frozenset({
    'ebay.com', 'ebay.co.uk', 'ebay.de', 'ebay.com.au', 'ebay.ca',
    'ebay.fr', 'ebay.it', 'ebay.es', 'ebay.nl', 'ebay.at',
    'ebay.ch', 'ebay.ie', 'ebay.be', 'ebay.pl',
})


# Amazon ASINs identify the same catalog item across official country stores.
# Only allowlisted marketplaces may claim this stable cross-domain identity;
# arbitrary amazon.* lookalikes must never inherit an Amazon ASIN key.
_AMAZON_MARKETPLACES = frozenset({
    'amazon.com', 'amazon.co.uk', 'amazon.de', 'amazon.co.jp',
    'amazon.ca', 'amazon.fr', 'amazon.it', 'amazon.es',
    'amazon.com.au', 'amazon.nl', 'amazon.se', 'amazon.pl',
    'amazon.com.br', 'amazon.com.mx', 'amazon.in', 'amazon.ae',
    'amazon.sa', 'amazon.sg', 'amazon.com.tr', 'amazon.com.be',
})


# These query fields change the retailer's *presentation*, not which item an
# unambiguous merchant SKU identifies. Only exempt them from the broad-SKU
# safety guard; retain them in URL keys for sites without a reliable SKU.
# Unknown selectors (size, color, option_id, etc.) remain variant-sensitive.
_SKU_PRESENTATION_PARAMS = {'view', 'sort_by'}

# Retailers frequently change query-key capitalization in redirects. These
# known variant selectors are case-insensitive *keys*, not values. Keep Blue
# and blue distinct, and never strip a selected size, color, or model.
_CASE_INSENSITIVE_VARIANT_KEYS = {'size', 'color', 'colour', 'model'}

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
        if lowered in {'sku', 'variant', 'preselect'} | _CASE_INSENSITIVE_VARIANT_KEYS:
            # Selector key casing and identical repeated key/value pairs do
            # not create a new variant. Trim only strong SKU selector values;
            # other values may be case/whitespace-sensitive at the retailer.
            normalized_value = value.strip() if lowered in {'sku', 'variant', 'preselect'} else value
            pair = (lowered, normalized_value)
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
    # Fold Latin accents without deleting meaningful Chinese, Arabic or other
    # non-Latin letters. ASCII-only encoding made unrelated international
    # product titles share the same permanent winner identity.
    ascii_name = ''.join(
        char for char in unicodedata.normalize('NFKD', name.casefold())
        if not unicodedata.combining(char)
    )
    # Seller listings often join quantities to the unit: 18pc, 18pcs,
    # 18-piece and 18 pieces describe the same pack, not new winners.
    # Keep the number so 18-piece and 20-piece remain different products.
    ascii_name = re.sub(r'\b(\d+)\s*-?\s*(?:pieces?|pcs?)\b', r'\1 piece', ascii_name)
    # Pack/count quantity labels can be joined to the number in listings:
    # 12pk, 12-pack and 12 packs are the same quantity, not fresh winners.
    # Do NOT equate 'ct' with count: in jewelry it can mean carat.
    # 'Pack of 3' and '3-pack' are one quantity. Preserve the numeric count.
    ascii_name = re.sub(r'\bpack\s+of\s+(\d+)\b', r'\1 pack', ascii_name)
    ascii_name = re.sub(r'\b(\d+)\s*-?\s*(?:packs?|pk)\b', r'\1 pack', ascii_name)
    ascii_name = re.sub(r'\b(\d+)\s*-?\s*(?:counts?)\b', r'\1 count', ascii_name)
    return [_TITLE_ALIASES.get(word, word) for word in re.findall(r'[^\W_]+', ascii_name)]


WINDOW_SECONDS = 30 * 86400  # Category rotation only; winning products never expire


def identity_keys(name, url):
    # Do not use Python hash(): its output changes across server processes.
    normalized = ' '.join(re.findall(r'[a-z0-9]+', name.lower()))
    # Preserve legacy ASCII hashes for older stored winners. When a title
    # contains non-Latin letters, however, the legacy hash discards product
    # identity (sometimes becoming SHA256 of the empty string). Do not emit
    # that ambiguous key: it would permanently block unrelated winners.
    # Historical non-Latin hash-only rows cannot be safely reconstructed.
    unicode_folded = ''.join(
        char for char in unicodedata.normalize('NFKD', name.casefold())
        if not unicodedata.combining(char)
    )
    if any(ord(char) > 127 and char.isalnum() for char in unicode_folded):
        normalized = ' '.join(re.findall(r'[^\W_]+', unicode_folded))
    keys = ['name-' + hashlib.sha256(normalized.encode()).hexdigest()[:24]]
    # Retailer titles may arrive HTML-escaped ("&amp;") or decoded ("&").
    # Keep the raw name hash above for existing rows, but share a decoded
    # identity so a changed product URL cannot recycle the same item.
    decoded_name = html.unescape(name)
    tokens = _title_tokens(decoded_name)
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
    # Preserve older HTML-escaped title hashes: before normalization, an
    # encoded ampersand introduced an extra "amp" token. Include both
    # spellings on *either* incoming form so old SQLite rows still exclude
    # relisted winners after the seller changes their product URL. Do not
    # drop size, count, brand, or model tokens from these alternate keys.
    legacy_spellings = {tuple(_title_tokens(name))}
    # Earlier permanent rows store only hashed titles. Before grey/gray
    # normalization, a UK-spelled listing stored "grey" in its folded/title
    # hash; emit that historical spelling on either incoming form so the
    # same winner remains excluded after a URL or seller-title change.
    # Other colors, pack sizes and model numbers remain unchanged.
    if 'gray' in tokens:
        legacy_spellings.add(tuple('grey' if token == 'gray' else token
                                   for token in tokens))
    if '&' in decoded_name:
        legacy_spellings.add(tuple(_title_tokens(decoded_name.replace('&', '&amp;'))))
    # A retailer can relist "Bamboo & Steel Drawer Organizer" as "Bamboo
    # and Steel Drawer Organiser". Existing permanent rows store only hashed
    # titles, so emit BOTH historical spellings on either input, rather than
    # only a new key that old rows cannot match. Apply this conservative
    # conjunction alias only to specific titles with >=4 distinct non-"and"
    # tokens; keep quantities, sizes, brand and model numbers untouched.
    # Short/generic titles must not collide just because they contain "and".
    if len(set(tokens) - {'and'}) >= 4:
        if 'and' in tokens:
            legacy_spellings.add(tuple(token for token in tokens if token != 'and'))
        if '&' in decoded_name:
            legacy_spellings.add(tuple(_title_tokens(decoded_name.replace('&', ' and '))))
    for old_tokens in sorted(legacy_spellings):
        if not old_tokens or list(old_tokens) == tokens:
            continue
        old_folded = ' '.join(old_tokens)
        keys.append('name-folded-' + hashlib.sha256(old_folded.encode()).hexdigest()[:24])
        if len(old_tokens) >= 3 and len(set(old_tokens)) >= 3:
            keys.append('title-' + hashlib.sha256(
                ' '.join(sorted(old_tokens)).encode()).hexdigest()[:24])
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
    # Include old compact and plural folded spellings when checking permanent
    # history: existing rows contain hashes, not the original listing names.
    # Older piece-count rows may contain 18pcs, 18 pcs or 18 pieces instead
    # of today's normalized 18 piece. Preserve all historical hashes even
    # when the retailer also changes title order and listing URL. Quantities
    # remain exact; pack and count stay distinct, and 'ct' is not count.
    for unit, spellings in (
        ('piece', ('piece', 'pieces', 'pc', 'pcs')),
        ('pack', ('pack', 'packs', 'pk')),
        ('count', ('count', 'counts')),
    ):
        pattern = r'\b(\d+)\s+' + unit + r'\b'
        if re.search(pattern, folded_name):
            for spelling in spellings:
                measurement_variants.add(re.sub(
                    pattern, lambda match: f'{match.group(1)}{spelling}', folded_name
                ))
                measurement_variants.add(re.sub(
                    pattern, lambda match: f'{match.group(1)} {spelling}', folded_name
                ))
        if unit == 'pack':
            # Preserve pre-upgrade hashes for 'pack of N' titles, even
            # when a seller changes the URL and reorders the title.
            measurement_variants.add(re.sub(
                pattern, lambda match: f'pack of {match.group(1)}', folded_name
            ))
    # Product titles also interchange 12oz, 12 oz, 12-ounce and 12 ounces.
    # Emit historical folded/sorted-title hashes on both forms: permanent
    # SQLite rows saved before this alias may contain only a title hash.
    # Keep the numeric capacity, so 12 oz and 16 oz remain distinct.
    ounce_pattern = r'\b(\d+)\s*(?:ounces?|oz)\b'
    if re.search(ounce_pattern, folded_name):
        for spelling in ('oz', 'ounce', 'ounces'):
            measurement_variants.add(re.sub(
                ounce_pattern,
                lambda match: f'{match.group(1)} {spelling}',
                folded_name,
            ))
        measurement_variants.add(re.sub(
            ounce_pattern, lambda match: f'{match.group(1)}oz', folded_name
        ))
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
    # Seller listings interchange 4ft, 4 ft, 4-foot and 4 feet. Emit
    # every folded spelling so permanent rows saved before this upgrade
    # (which contain only hashed titles) still exclude the same product.
    # Retain the numeric size and unit: 5 feet, 4 inches and 4 cm are distinct.
    foot_pattern = r'\b(\d+)\s*(?:feet|foot|ft)\b'
    if re.search(foot_pattern, folded_name):
        for spelling in ('ft', 'foot', 'feet'):
            measurement_variants.add(re.sub(
                foot_pattern, lambda match: f'{match.group(1)} {spelling}', folded_name
            ))
        measurement_variants.add(re.sub(
            foot_pattern, lambda match: f'{match.group(1)}ft', folded_name
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
        # Retailers can change both the product slug and display name while
        # retaining their own SKU. Claim a host-scoped key only when that SKU
        # is strong and unambiguous. A parent SKU accompanied by size, color,
        # variant, or any other non-tracking selector is NOT safe to collapse:
        # the additional selector may identify a different sellable variant.
        # SKU values are case-sensitive; only query *key* case and boundary
        # whitespace are normalized. Existing URL/name keys remain intact.
        sku_values = [value.strip() for key, value in query_pairs
                      if key.lower() == 'sku']
        other_selectors = [
            key for key, _ in query_pairs
            if key.lower() != 'sku'
            and not key.lower().startswith('utm_')
            and key.lower() not in _TRACKING_PARAMS
            and key.lower() not in _SKU_PRESENTATION_PARAMS
        ]
        # Entity-like and reserved separators in a product path can identify
        # different routes even when a query SKU happens to match. Keep those
        # paths distinct, as required by the existing encoded-path safeguards.
        safe_sku_path = not re.search(r'&|;|%(?:26|3b)', raw_p.path, re.I)
        if (sku_values and len(set(sku_values)) == 1
                and not other_selectors and safe_sku_path):
            sku = sku_values[0]
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{3,63}', sku):
                keys.append('merchant-sku-' + hashlib.sha256(
                    f'{host_name}:{sku}'.encode()).hexdigest()[:24])
        keys.append('url-' + hashlib.sha256(canonical.encode()).hexdigest()[:24])
        # Before query-key normalization, permanent SQLite history recorded
        # URL hashes for common uppercase/Title-case size and color selectors.
        # Include bounded historical aliases in either direction so a renamed
        # listing still matches those old hashes. Values and other selectors
        # are never rewritten; cap combinations to avoid unbounded key growth.
        case_indices = [i for i, (key, _) in enumerate(query)
                        if key in _CASE_INSENSITIVE_VARIANT_KEYS]
        if 0 < len(case_indices) <= 2:
            for forms in cartesian_product(('lower', 'upper', 'title'), repeat=len(case_indices)):
                historical_query = list(query)
                for index, form in zip(case_indices, forms):
                    key, value = historical_query[index]
                    alias_key = key.upper() if form == 'upper' else key.capitalize() if form == 'title' else key
                    historical_query[index] = (alias_key, value)
                historical_url = urlunsplit(('https', host_name, canonical_path,
                                           urlencode(sorted(historical_query)), ''))
                if historical_url != canonical:
                    keys.append('url-' + hashlib.sha256(historical_url.encode()).hexdigest()[:24])
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
            # Shopify mobile storefronts also expose unselected product pages
            # without ?variant. The same /products/<slug> on merchant.example
            # and m.merchant.example is one winner even after a title rewrite.
            # Add the opposite-host URL hash on BOTH forms so pre-upgrade
            # desktop-only/mobile-only permanent rows remain effective.
            # Only do this for an unselected product (no non-tracking query
            # selectors): a size, custom option or variant may identify a
            # different sellable item and must not inherit the parent key.
            if not query:
                alternate_host = None
                if host.startswith('m.') and host.count('.') >= 2:
                    alternate_host = host[2:]
                elif not host.startswith('m.') and '.' in host:
                    alternate_host = 'm.' + host
                if alternate_host:
                    alternate_url = urlunsplit(
                        ('https', alternate_host, canonical_path, '', '')
                    )
                    keys.append('url-' + hashlib.sha256(
                        alternate_url.encode()).hexdigest()[:24])
            # A seller redirect may append the same variant selector twice
            # (sometimes with different parameter capitalization). Those
            # identical values still name one SKU; do not let the duplicate
            # query keys bypass the merchant-scoped permanent winner history.
            # Conflicting values are ambiguous: never choose one arbitrarily.
            variants = {v.strip() for k, v in query_pairs if k.lower() == 'variant'}
            if len(variants) == 1:
                variant = next(iter(variants))
                if re.fullmatch(r'[0-9]{8,20}', variant):
                    # Shopify mobile storefront links commonly switch
                    # between merchant.example and m.merchant.example.
                    # Emit BOTH aliases on either host: this also preserves
                    # exclusions saved before mobile-host normalization,
                    # when history contained only the old host-specific hash.
                    # Never alias a two-label m.example registrable domain,
                    # and never combine unrelated merchant hostnames.
                    merchant_hosts = {host}
                    if host.startswith('m.') and host.count('.') >= 2:
                        merchant_hosts.add(host[2:])
                    elif not host.startswith('m.') and '.' in host:
                        merchant_hosts.add('m.' + host)
                    for merchant_host in sorted(merchant_hosts):
                        merchant_variant = f'{merchant_host}:{variant}'
                        keys.append('shopify-variant-' + hashlib.sha256(
                            merchant_variant.encode()).hexdigest()[:24])
        # Etsy listing IDs survive seller title/slug edits, mobile links and
        # recommendation click tracking. Only official Etsy hosts can claim
        # these identities. A selected variation is a different sellable
        # item from the parent or another variation, so never attach the
        # broad listing key when a variation selector is present.
        if host in {'etsy.com', 'm.etsy.com'}:
            etsy_listing = re.fullmatch(
                r'/(?:[a-z]{2}(?:-[a-z]{2})?/)?listing/([0-9]{6,})(?:/[^/]*)?',
                canonical_path, re.I
            )
            if etsy_listing:
                etsy_attribution = {
                    'click_key', 'click_sum', 'ga_order', 'ga_search_type',
                    'ga_view_type', 'ga_search_query', 'organic_search_click',
                }
                selectors = [
                    (key.lower(), value.strip()) for key, value in query_pairs
                    if key.lower() not in etsy_attribution
                    and key.lower() not in _TRACKING_PARAMS
                    and not key.lower().startswith('utm_')
                ]
                listing_id = etsy_listing.group(1)
                if not selectors:
                    keys.append('etsy-listing-' + listing_id)
                elif all(key in {'variation0', 'variation1'} for key, _ in selectors):
                    # Repeated identical selectors are harmless; conflicting
                    # values are ambiguous and must not claim a child SKU.
                    chosen = {}
                    for key, value in selectors:
                        if (not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', value)
                                or (key in chosen and chosen[key] != value)):
                            break
                        chosen[key] = value
                    else:
                        fingerprint = hashlib.sha256(
                            urlencode(sorted(chosen.items())).encode()
                        ).hexdigest()[:24]
                        keys.append('etsy-variation-' + listing_id + '-' + fingerprint)
        # eBay's numeric item ID is stable across rewritten listing slugs,
        # mobile subdomains and advertising click URLs. Claim it only for
        # unambiguous item links: unknown selectors might choose a distinct
        # size, colour or variation within the same parent listing. In that
        # case retain the exact canonical URL keys instead of broadening.
        if any(host == market or host.endswith('.' + market)
               for market in _EBAY_MARKETPLACES):
            ebay_item = re.fullmatch(
                r'/itm/(?:[^/]+/)?([0-9]{9,15})/?', canonical_path, re.I
            )
            ebay_tracking = {
                'mkcid', 'mkevt', 'mkrid', 'campid', 'toolid', 'customid',
                'hash', 'itmmeta', 'itmprp', '_trkparms', '_trksid',
            }
            if ebay_item:
                # An eBay ?var=<numeric ID> selects one sellable variation.
                # A changed slug/title, mobile host or click tracking must not
                # recycle that same child SKU. Do not attach the parent item
                # identity to a selected variation: other children and the
                # unselected parent must remain independently eligible.
                # Repeated identical var selectors are unambiguous; conflicting
                # values or unknown selectors are not.
                non_tracking = [
                    (key.lower(), value.strip()) for key, value in query_pairs
                    if key.lower() not in ebay_tracking
                    and key.lower() not in _TRACKING_PARAMS
                    and not key.lower().startswith('utm_')
                ]
                if not non_tracking:
                    keys.append('ebay-item-' + ebay_item.group(1))
                elif all(key == 'var' for key, _ in non_tracking):
                    variations = {value for _, value in non_tracking}
                    if len(variations) == 1:
                        variation = next(iter(variations))
                        if re.fullmatch(r'[0-9]{9,20}', variation):
                            keys.append(
                                'ebay-variant-' + ebay_item.group(1) + '-' + variation
                            )
        if any(host == market or host.endswith('.' + market)
               for market in _AMAZON_MARKETPLACES):
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
            # Target child IDs can be padded or repeated by redirects.
            # Normalize boundary whitespace, but never choose arbitrarily
            # between conflicting child selectors: that could mark a
            # different variant as already won. An ambiguous selection
            # must not fall back to claiming the parent TCIN either.
            # Only a known, unambiguous preselect may claim a child TCIN.
            # Unknown query selectors may select a different size, bundle or
            # custom option. Keep the exact canonical URL in that case, but
            # do not claim a broad parent/child TCIN that could block another
            # legitimate variant. Known marketing/presentation fields do not
            # select a SKU and remain safe to ignore here.
            selectors = [
                (k.lower(), v.strip()) for k, v in query_pairs
                if not k.lower().startswith('utm_')
                and k.lower() not in _TRACKING_PARAMS
                and k.lower() not in _SKU_PRESENTATION_PARAMS
            ]
            preselected = {v for k, v in selectors if k == 'preselect'}
            unknown_selector = any(k != 'preselect' for k, _ in selectors)
            if not unknown_selector and len(preselected) == 1:
                child = next(iter(preselected))
                if re.fullmatch(r'[0-9]{6,}', child):
                    keys.append('target-tcin-' + child)
            elif not selectors and match:
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

    def import_scope(self, source_scope):
        """Copy older browser winner exclusions into an authenticated owner ledger.

        Keep the browser's original rows: this is an additive, transactional
        bridge, not an identity reset. The source scope is hashed just like
        normal browser scopes. Never import into another customer's account;
        callers must verify the owner session before invoking this method.
        """
        if not source_scope:
            return
        source_hash = hashlib.sha256(source_scope.encode()).hexdigest()
        if source_hash == self.scope:
            return
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute(
                'INSERT OR IGNORE INTO discovery_seen (scope, identity, expires) '
                'SELECT ?, identity, expires FROM discovery_seen WHERE scope=?',
                (self.scope, source_hash),
            )

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
