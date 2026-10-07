"""Resolve an explicit product URL without search, substitute products or stock photos."""
import json
import re
from urllib.parse import urlsplit, urljoin
from backend.product_facts import canonical
from bs4 import BeautifulSoup
from fastapi import HTTPException
from backend.url_resolver import _safe_get, ResolvedProduct


def amazon_asin(url):
    host=(urlsplit(url).hostname or '').lower()
    if not re.fullmatch(r'(?:[a-z0-9-]+\.)*amazon\.(?:com|ca|co\.uk|com\.au|de|fr|it|es|co\.jp|in|com\.mx)',host):
        return None
    match=re.search(r'/(?:dp|gp/product|product)/([A-Z0-9]{10})(?:[/;?#]|$)',urlsplit(url).path,re.I)
    return match.group(1).upper() if match else None


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
        same_url=isinstance(product_url,str) and canonical(urljoin(final_url,product_url))==canonical(final_url)
        same_name=bool(title and normalize(name) and normalize(name)==normalize(title))
        if name and (same_name or (not amazon and same_url)):
            if matched is not None and normalize(matched.get('name','')) != normalize(name):
                raise ValueError('The page identifies multiple conflicting products.')
            matched=product
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
    if error or not html or not final_url:
        raise HTTPException(502,'The seller page could not be read. No other product was selected. For Amazon, use the full product page URL containing /dp/.')
    requested=amazon_asin(url)
    actual=amazon_asin(final_url)
    if requested and actual!=requested:
        raise HTTPException(422,'Amazon redirected to a different product or page. No other product was selected.')
    try:data=exact_listing(html,final_url)
    except ValueError as exc:
        raise HTTPException(422,str(exc)+' No other product was selected. Use the full product page URL containing /dp/.') from exc
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
    result.update(source='exact_url_resolved',product_identity_verified=True,url_resolution=resolution.to_dict(),description=data['description'],research_status='not_requested',trend_signals=[],trend_score=0,margin_estimate='Not established')
    return result
