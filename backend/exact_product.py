"""Resolve an explicit product URL without search, substitute products or stock photos."""
import json
import re
from urllib.parse import urlsplit, urljoin, parse_qs
from bs4 import BeautifulSoup
from fastapi import HTTPException
from backend.url_resolver import _safe_get, ResolvedProduct


def amazon_asin(url):
    host=(urlsplit(url).hostname or '').lower()
    if not re.fullmatch(r'(?:[a-z0-9-]+\.)*amazon\.(?:com|ca|co\.uk|com\.au|de|fr|it|es|co\.jp|in|com\.mx)',host):
        return None
    match=re.search(r'/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:/|$)',urlsplit(url).path,re.I)
    if match:
        return match.group(1).upper()
    # Some Amazon sharing/product links carry the ASIN in a query
    # rather than a /dp/ path. Never accept arbitrary /s/ search URLs.
    path=urlsplit(url).path.rstrip('/').lower()
    if path in {'/gp/aw/d', '/gp/product', '/dp'}:
        args=parse_qs(urlsplit(url).query)
        for name in ('asin','ASIN'):
            value=(args.get(name) or [''])[0]
            if re.fullmatch(r'[A-Z0-9]{10}',value,re.I):
                return value.upper()
    return None



def _product_target_in_share_page(original_url, final_url, html):
    """A single explicit ASIN-bearing link in Amazon share-page metadata.

    Never use unrelated product recommendations or guessing from a short token.
    """
    from urllib.parse import unquote
    original_host=(urlsplit(original_url).hostname or '').lower()
    if original_host not in {'link.amazon', 'amzn.to', 'amzn.asia'}:
        return None
    candidates=[]
    for source in (original_url, final_url or ''):
        parsed=urlsplit(source)
        query=parse_qs(parsed.query)
        for key, values in query.items():
            if key.lower() not in {'url', 'u', 'redirect', 'redirecturl', 'destination', 'dest'}:
                continue
            for value in values[:2]:
                nested=unquote(value)
                if nested.startswith('/'):
                    nested=urljoin('https://www.amazon.com',nested)
                if amazon_asin(nested):
                    candidates.append(nested)
    if html:
        soup=BeautifulSoup(html, 'html.parser')
        for node in soup.select('link[rel="canonical"], meta[property="og:url"], meta[name="twitter:url"], meta[http-equiv="refresh"]'):
            value=node.get('href') or node.get('content') or ''
            if node.get('http-equiv', '').lower() == 'refresh':
                value=re.sub(r'^.*?url\s*=\s*', '', value, flags=re.I)
            value=value.strip(' "\'')
            value=urljoin(final_url or original_url, value)
            if amazon_asin(value):
                candidates.append(value)
    identities={amazon_asin(u) for u in candidates if amazon_asin(u)}
    if len(identities)!=1:
        return None
    return next(u for u in candidates if amazon_asin(u) in identities)




def _exact_product_from_share_redirects(url):
    """Inspect redirects, preserving an exact Amazon product encountered en route.

    A link.amazon token is not an ASIN. Do not infer a product from its token.
    Restrict every hop to Amazon-owned HTTP(S) hosts to avoid an open proxy.
    """
    import requests
    from urllib.parse import urljoin
    start=(urlsplit(url).hostname or '').lower()
    if start not in {'link.amazon', 'amzn.to', 'amzn.asia'}:
        return None
    current=url
    seen=set()
    for _ in range(7):
        if current in seen:
            break
        seen.add(current)
        parsed=urlsplit(current)
        host=(parsed.hostname or '').lower()
        if parsed.scheme!='https' or (host not in {'link.amazon','amzn.to','amzn.asia'} and
                not re.fullmatch(r'(?:[a-z0-9-]+\.)*amazon\.(?:com|ca|co\.uk|com\.au|de|fr|it|es|co\.jp|in|com\.mx)',host)):
            break
        asin=amazon_asin(current)
        if asin:
            return current
        try:
            reply=requests.get(current,allow_redirects=False,stream=True,
                timeout=6,headers={'User-Agent':'Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36',
                                   'Accept':'text/html,application/xhtml+xml'})
            try:
                destination=reply.headers.get('location','') if 300 <= reply.status_code < 400 else ''
            finally:
                reply.close()
        except requests.RequestException:
            break
        if not destination:
            break
        current=urljoin(current,destination)
    return None


def _products(obj):
    if isinstance(obj,list):
        for child in obj:yield from _products(child)
    elif isinstance(obj,dict):
        types=obj.get('@type',[])
        if 'Product' in ([types] if isinstance(types,str) else types):yield obj
        for child in obj.values():
            if isinstance(child,(dict,list)):yield from _products(child)


def exact_listing(html, final_url):
    """Main product data only. Amazon landing pages never establish identity."""
    soup=BeautifulSoup(html,'html.parser')
    asin=amazon_asin(final_url)
    host=(urlsplit(final_url).hostname or '').lower()
    amazon=host=='link.amazon' or host.startswith('amzn.') or bool(re.search(r'(^|\.)amazon\.',host))
    main=soup.select_one('#productTitle') if amazon else None
    landing=soup.select_one('#landingImage, #imgBlkFront') if amazon else None
    identity=soup.select_one('input#ASIN, input[name="ASIN"]') if amazon else None
    page_asin=(identity.get('value','').strip().upper() if identity else '')
    if amazon and not asin and re.fullmatch(r'[A-Z0-9]{10}',page_asin) and main and landing:
        asin=page_asin
    if amazon and not asin:
        raise ValueError('Amazon link opened a department, search or shopping page instead of a product page.')
    meta=soup.select_one('meta[property="og:title"]')
    title=meta.get('content','').strip() if meta else ''
    if main:title=main.get_text(' ',strip=True)
    if amazon:
        if identity and identity.get('value','').strip().upper()!=asin:
            raise ValueError('Amazon page identifier does not match the requested product.')
    products=[]
    for script in soup.select('script[type="application/ld+json"]'):
        try:products.extend(_products(json.loads(script.string or script.get_text())))
        except (ValueError,TypeError):pass
    normalize=lambda text:' '.join(re.findall(r'[a-z0-9]+',str(text).lower()))
    matched=None
    for product in products:
        name=str(product.get('name','')).strip()
        product_url=product.get('url')
        same_url=isinstance(product_url,str) and urlsplit(urljoin(final_url,product_url)).path.rstrip('/')==urlsplit(final_url).path.rstrip('/')
        same_name=bool(title and normalize(name) and normalize(name) in normalize(title))
        if name and (same_name or (not amazon and same_url)):
            matched=product;title=name;break
    image=None
    if amazon:
        # Pin Factory's proven rule: prefer the retailer's explicit primary
        # image before any gallery/social image. A verified ASIN in the URL
        # anchors product identity, so we do not require brittle DOM widgets
        # such as #productTitle when metadata still identifies the same item.
        if landing:
            image=landing.get('data-old-hires') or landing.get('src')
            try:
                variants=json.loads(landing.get('data-a-dynamic-image','{}'))
                if variants:
                    image=max(variants,key=lambda key:variants[key][0]*variants[key][1])
            except (ValueError,TypeError,IndexError):
                pass
    if not image and matched:
        value=matched.get('image')
        if isinstance(value,list):value=value[0] if value else None
        if isinstance(value,dict):value=value.get('url') or value.get('contentUrl')
        if isinstance(value,str):image=value
    if not image:
        meta_image=soup.select_one('meta[property="og:image"],meta[name="twitter:image"]')
        if meta_image:image=meta_image.get('content')
    if amazon:
        if not asin:
            raise ValueError('Amazon link did not identify one exact product.')
        if not title or not image:
            raise ValueError('The Amazon product page did not provide both a product title and primary image.')
    elif not title or not image or not (main or matched):
        raise ValueError('The page did not identify one product with its own photo.')
    description=str((matched or {}).get('description') or '')
    if amazon:
        bullets=soup.select_one('#feature-bullets')
        if bullets:description=bullets.get_text(' ',strip=True)
    if not description:
        meta_description=soup.select_one('meta[property="og:description"],meta[name="description"]')
        if meta_description:description=meta_description.get('content','')
    return dict(name=title,image_url=urljoin(final_url,image),asin=asin,description=description)



def _pin_factory_product_fallback(url):
    """Resolve the same explicit URL through Pin Factory's proven extractor.

    This is used only when Amazon gives TrafficLift a generic shopping page to
    the Render server. Pin Factory must return a concrete product name and
    primary image for the same submitted URL; otherwise the fallback is
    rejected. No search or substitute product is allowed.
    """
    import os
    import requests

    endpoint=os.getenv(
        'PIN_FACTORY_RESOLVER_URL',
        'https://pin-factory-pro.onrender.com/api/text',
    )
    try:
        reply=requests.post(
            endpoint,
            json={'input':url,'niche':'home','tone':'high-energy'},
            timeout=20,
            headers={'User-Agent':'TrafficLift-Pro/1.0'},
        )
        if reply.status_code!=200:
            return None
        payload=reply.json()
    except (requests.RequestException, ValueError, TypeError):
        return None

    if not isinstance(payload,dict) or not payload.get('ok'):
        return None
    name=str(payload.get('productName') or '').strip()
    image=str(payload.get('productImage') or '').strip()
    if not name or not image or not image.startswith(('http://','https://')):
        return None

    final=str(payload.get('finalUrl') or payload.get('url') or url).strip()
    asin=amazon_asin(final) or None
    description=''
    copy=payload.get('copy')
    if isinstance(copy,dict):
        descriptions=copy.get('descriptions')
        if isinstance(descriptions,list) and descriptions:
            description=str(descriptions[0] or '').strip()

    return {
        'name':name,
        'image_url':image,
        'asin':asin,
        'description':description,
        'final_url':final if final.startswith(('http://','https://')) else url,
        'source':'pin_factory_exact_url',
    }


def analyze_exact_product(url):
    final_url,html,error=_safe_get(url,timeout=10)
    shared_target=_product_target_in_share_page(url,final_url,html)
    # Some mobile share links point through an exact /dp/ product URL
    # before eventually landing on a generic Amazon page in a bot client.
    if not shared_target and not amazon_asin(final_url or ''):
        shared_target=_exact_product_from_share_redirects(url)
    if shared_target and not amazon_asin(final_url or ''):
        share_url,share_html,share_error=_safe_get(shared_target,timeout=10)
        if not share_error and share_html and amazon_asin(share_url or '')==amazon_asin(shared_target):
            final_url,html,error=share_url,share_html,None
    requested=amazon_asin(url) or amazon_asin(shared_target or '')
    actual=amazon_asin(final_url or '')
    if requested and actual and actual!=requested:
        raise HTTPException(422,'Amazon redirected to a different product. No other product was selected.')
    # Retry the canonical product address only when the original/redirected
    # URL already identifies a single ASIN. This handles mobile/share URLs
    # that bounce through alternate Amazon routes; it never guesses a product
    # from an affiliate homepage, category, or search link.
    known_asin=requested or actual
    if known_asin and (error or not html or not final_url or not actual):
        host=(urlsplit(final_url or url).hostname or '').lower()
        if not re.fullmatch(r'(?:[a-z0-9-]+\.)*amazon\.(?:com|ca|co\.uk|com\.au|de|fr|it|es|co\.jp|in|com\.mx)',host):
            host='www.amazon.com'
        canonical=f'https://{host}/dp/{known_asin}'
        retry_url,retry_html,retry_error=_safe_get(canonical,timeout=10)
        if not retry_error and retry_html and amazon_asin(retry_url or '')==known_asin:
            final_url,html,error=retry_url,retry_html,None
    if error or not html or not final_url:
        raise HTTPException(502,'The product listing could not be read. No unrelated product was selected. Try another link to this exact item.')
    if requested and amazon_asin(final_url)!=requested:
        raise HTTPException(422,'Amazon redirected away from the requested item. No other product was selected.')
    try:
        data=exact_listing(html,final_url)
    except ValueError as exc:
        # Amazon often serves Render a generic shopping/mission page for
        # share links. Reuse Pin Factory's extractor for the exact same URL
        # instead of asking the user to hunt for a different link.
        data=_pin_factory_product_fallback(url)
        if not data:
            raise HTTPException(422,str(exc)+' No other product was selected. This link must resolve to one identifiable item.') from exc
        final_url=data.pop('final_url',final_url or url)
    if data.get('asin') and not amazon_asin(final_url or ''):
        host=(urlsplit(final_url or url).hostname or '').lower()
        if not re.fullmatch(r'(?:[a-z0-9-]+\.)*amazon\.(?:com|ca|co\.uk|com\.au|de|fr|it|es|co\.jp|in|com\.mx)',host):
            host='www.amazon.com'
        final_url=f'https://{host}/dp/{data["asin"]}'
    from backend.product_research import _build_url_candidate, _card_category
    from backend.product_scout import get_researcher
    from backend.product_control_agent import ProductControlAgent
    fallback_source=data.pop('source',None)
    resolution=ResolvedProduct(
        original_url=url,
        final_url=final_url,
        asin=data.get('asin'),
        title=data['name'],
        source=fallback_source or 'exact_seller_page',
    )
    card=_build_url_candidate(resolution)
    winner=get_researcher()._materialize(card,_card_category(card))
    winner.update(
        image_url=data['image_url'],
        image_source=final_url,
        description=data.get('description',''),
        source=fallback_source or 'exact_url_resolved',
        url_resolution=resolution.to_dict(),
    )
    report=ProductControlAgent.evaluate(winner)
    if not report.ok:
        raise HTTPException(422,'The selected product photo could not be validated. No other product or photo was selected.')
    result=report.product
    result.update(
        source=fallback_source or 'exact_url_resolved',
        product_identity_verified=True,
        url_resolution=resolution.to_dict(),
        description=data.get('description',''),
        research_status='not_requested',
        trend_signals=[],
        trend_score=0,
        margin_estimate='Not established',
        affiliate_candidate_url=url if urlsplit(url).scheme=='https' else '',
    )
    return result
