"""Read photos from the exact researched listing, with product identity checks."""
import json
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from backend.url_resolver import _safe_get

def listing_images(name, url, *, timeout=4, details=None):
    final_url, html, error = _safe_get(url, timeout=timeout)
    if error or not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    normalize = lambda text: ' '.join(re.findall(r'[a-z0-9]+', str(text).lower()))
    # Search engines append retailer labels that are absent from Product.name.
    # Remove only known retailer suffixes, preserving the model and its options.
    identity_name = re.sub(r'\s*(?:[-|–—:]\s*)(?:Target|Walmart(?:\.com)?|Amazon(?:\.com)?|Etsy|Home Depot|Lowes|Lowe\'s)\s*$', '', name, flags=re.I)
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
            if 'Product' in types and matches(obj.get('name', '')):
                add(obj.get('image'))
                if details is not None and isinstance(obj.get('description'), str):
                    description = BeautifulSoup(obj['description'], 'html.parser').get_text(' ', strip=True)
                    description = ' '.join(description.split())
                    if len(description)>160:
                        description = description[:161].rsplit(' ',1)[0]
                    if description and not any(char in description for char in '{}\\'):
                        details.update(seller_benefit=description, seller_benefit_source=url)
            for key, child in obj.items():
                if isinstance(child, (list, dict)): walk(child)
    for script in soup.select('script[type="application/ld+json"]'):
        try: walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError): pass
    title = soup.select_one('meta[property="og:title"]')
    page_title = title.get('content', '') if title else (soup.title.get_text() if soup.title else '')
    if matches(page_title):
        for meta in soup.select('meta[property="og:image"],meta[property="og:image:secure_url"],meta[name="twitter:image"]'):
            add(meta.get('content'))
    return images[:4]
