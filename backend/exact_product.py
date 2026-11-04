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
    if amazon and not asin:
        raise ValueError('Amazon link opened a department, search or shopping page instead of a product page.')
    meta=soup.select_one('meta[property="og:title"]')
    title=meta.get('content','').strip() if meta else ''
    main=soup.select_one('#productTitle') if amazon else None
    if main:title=main.get_text(' ',strip=True)
    if amazon:
        identity=soup.select_one('input#ASIN, input[name="ASIN"]')
        if identity and identity.get('value','').upper()!=asin:
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
        landing=soup.select_one('#landingImage, #imgBlkFront')
        if not main and not (matched and landing):
            raise ValueError('Amazon did not supply identifiable main-product details.')
        if landing:
            image=landing.get('data-old-hires') or landing.get('src')
            try:
                variants=json.loads(landing.get('data-a-dynamic-image','{}'))
                if variants:image=max(variants,key=lambda key:variants[key][0]*variants[key][1])
            except (ValueError,TypeError,IndexError):pass
    if not image and matched:
        value=matched.get('image')
        if isinstance(value,list):value=value[0] if value else None
        if isinstance(value,dict):value=value.get('url') or value.get('contentUrl')
        if isinstance(value,str):image=value
    if not image and (main or matched):
        meta_image=soup.select_one('meta[property="og:image"]')
        if meta_image:image=meta_image.get('content')
    if not title or not image or not (main or matched):
        raise ValueError('The page did not identify one product with its own photo.')
    description=str((matched or {}).get('description') or '')
    if amazon:
        bullets=soup.select_one('#feature-bullets')
        if bullets:description=bullets.get_text(' ',strip=True)
    if not description:
        meta_description=soup.select_one('meta[property="og:description"],meta[name="description"]')
        if meta_description:description=meta_description.get('content','')
    return dict(name=title,image_url=urljoin(final_url,image),asin=asin,description=description)


def analyze_exact_product(url):
    final_url,html,error=_safe_get(url,timeout=10)
    requested=amazon_asin(url)
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
        if not re.fullmatch(r'(?:[a-z0-9-]+\\.)*amazon\\.(?:com|ca|co\\.uk|com\\.au|de|fr|it|es|co\\.jp|in|com\\.mx)',host):
            host='www.amazon.com'
        canonical=f'https://{host}/dp/{known_asin}'
        retry_url,retry_html,retry_error=_safe_get(canonical,timeout=10)
        if not retry_error and retry_html and amazon_asin(retry_url or '')==known_asin:
            final_url,html,error=retry_url,retry_html,None
    if error or not html or not final_url:
        raise HTTPException(502,'The product listing could not be read. No unrelated product was selected. Try another link to this exact item.')
    if requested and amazon_asin(final_url)!=requested:
        raise HTTPException(422,'Amazon redirected away from the requested item. No other product was selected.')
    try:data=exact_listing(html,final_url)
    except ValueError as exc:
        raise HTTPException(422,str(exc)+' No other product was selected. This link must resolve to one identifiable item.') from exc
    from backend.product_research import _build_url_candidate, _card_category
    from backend.product_scout import get_researcher
    from backend.product_control_agent import ProductControlAgent
    resolution=ResolvedProduct(original_url=url,final_url=final_url,asin=data['asin'],title=data['name'],source='exact_seller_page')
    card=_build_url_candidate(resolution)
    winner=get_researcher()._materialize(card,_card_category(card))
    winner.update(image_url=data['image_url'],image_source=final_url,description=data['description'],source='exact_url_resolved',url_resolution=resolution.to_dict())
    report=ProductControlAgent.evaluate(winner)
    if not report.ok:
        raise HTTPException(422,'The selected product photo could not be validated. No other product or photo was selected.')
    result=report.product
    result.update(source='exact_url_resolved',product_identity_verified=True,url_resolution=resolution.to_dict(),description=data['description'],research_status='not_requested',trend_signals=[],trend_score=0,margin_estimate='Not established',affiliate_candidate_url=url if urlsplit(url).scheme=='https' else '')
    return result
