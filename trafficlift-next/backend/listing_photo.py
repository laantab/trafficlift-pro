"""Read photos from the exact researched listing, with product identity checks."""
import json
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from backend.url_resolver import _safe_get

def product_name(name):
    return re.sub(r"\s*(?:[-|–—:]\s*)(?:Target|Walmart(?:\.com)?|Amazon(?:\.com)?|Etsy|Home Depot|Lowes|Lowe's)\s*$", '', name, flags=re.I).strip()


def usable_facts(value):
    """Keep complete factual sentences; never cut a qualification in half."""
    soup = BeautifulSoup(str(value or ''), 'html.parser')
    # Separate actual block/bullet boundaries, never inline emphasis that
    # could detach a qualification such as 'except narrow holders'.
    for block in soup.find_all(['p','li','div','br']):
        block.insert_before('\n')
    text = soup.get_text(' ', strip=False)
    text = '\n'.join(' '.join(line.split()) for line in text.splitlines())
    facts = []
    for sentence in re.split(r'(?<=[.!?])\s+|[•\n]', text):
        sentence = sentence.strip()
        if not 15 <= len(sentence) <= 160 or any(c in sentence for c in '{}\\'):
            continue
        if re.search(r"\b(shop|buy now|free shipping|guaranteed|best ever|selling out|hurry|limited time|customers|reviews)\b|[$£€]|\d+\s*%\s*off", sentence, re.I):
            continue
        if not re.search(r'\b(for|with|includes?|tracks?|adjustable|removable|designed|features?|supports?|provides?|helps?|allows?|made|fits?|waterproof|rechargeable|wireless|stainless steel|wide feed chute|reverse function|easy to clean|dishwasher safe)\b', sentence, re.I):
            continue
        if sentence not in facts:
            facts.append(sentence)
    return facts[:3]


def listing_images(name, url, *, timeout=4, details=None):
    final_url, html, error = _safe_get(url, timeout=timeout)
    if error or not html:
        return []
    from backend.product_facts import canonical
    if canonical(final_url or url)!=canonical(url):return []
    soup = BeautifulSoup(html, 'html.parser')
    normalize = lambda text: ' '.join(re.findall(r'[a-z0-9]+', str(text).lower()))
    # Search engines append retailer labels that are absent from Product.name.
    # Remove only known retailer suffixes, preserving the model and its options.
    identity_name = product_name(name)
    expected = normalize(identity_name)
    def matches(title):
        actual = normalize(title)
        return bool(expected and len(expected.split()) >= 2 and
                    f' {expected} ' in f' {actual} ')
    images = []
    def add(value):
        if isinstance(value, list):
            for item in value: add(item)
        elif isinstance(value, dict):
            add(value.get('url') or value.get('contentUrl'))
        elif isinstance(value, str) and value.strip():
            absolute = urljoin(final_url or url, value.strip())
            if absolute.startswith(('https://', 'http://')) and absolute not in images:
                images.append(absolute)
    def walk(obj):
        if isinstance(obj, list):
            for item in obj: walk(item)
        elif isinstance(obj, dict):
            types = obj.get('@type', [])
            if isinstance(types, str): types = [types]
            if 'Product' in types and matches(obj.get('name', '')) and (not obj.get('url') or canonical(urljoin(final_url or url,str(obj['url'])))==canonical(url)):
                add(obj.get('image'))
                rating = obj.get('aggregateRating')
                if details is not None and isinstance(rating, dict):
                    try:
                        count = int(rating.get('reviewCount') or rating.get('ratingCount') or 0)
                        value = float(rating.get('ratingValue'))
                        best = float(rating.get('bestRating', 5))
                        if count > 0 and 0 < value <= best <= 100:
                            details['seller_rating'] = dict(count=count, value=value, best=best)
                    except (ValueError, TypeError):
                        pass
                if details is not None and isinstance(obj.get('description'), str):
                    facts = usable_facts(obj['description'])
                    if facts:
                        details.update(seller_benefit=facts[0], seller_benefit_source=url,
                                       seller_facts=facts, evidence_origin='matched_seller_page')
            for key, child in obj.items():
                if isinstance(child, (list, dict)): walk(child)
    for script in soup.select('script[type="application/ld+json"]'):
        try: walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError): pass
    title = soup.select_one('meta[property="og:title"]')
    page_title = title.get('content', '') if title else (soup.title.get_text() if soup.title else '')
    if matches(page_title):
        if details is not None and not details.get('seller_benefit'):
            for meta in soup.select('meta[property="og:description"],meta[name="description"],meta[name="twitter:description"]'):
                facts = usable_facts(meta.get('content'))
                if facts:
                    details.update(seller_benefit=facts[0], seller_benefit_source=url,
                                   seller_facts=facts, evidence_origin='matched_seller_page')
                    break
        if not images:
            for meta in soup.select('meta[property="og:image"],meta[property="og:image:secure_url"],meta[name="twitter:image"]'):
                add(meta.get('content'))
    return images[:4]
