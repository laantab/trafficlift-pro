"""Automatic seller facts plus dated, model-specific editorial source packets.
At most one connected search when exact seller HTML has no facts. A reviewed packet is never represented as a live lookup.
"""
import re
from datetime import date
from backend.listing_photo import listing_images, product_name, usable_facts
from urllib.parse import urlsplit, parse_qsl


def resolve_video_facts(name, url):
    title = product_name(name)
    # Exact ring model only: never apply ring features to a kit or accessory.
    if re.fullmatch(r'Oura Ring 4(?: (?:Silver|Black|Gold|Stealth|Brushed Silver|Rose Gold))?', title, re.I) and date.today() <= date(2026, 12, 31):
        return dict(
            benefit='Sleep and activity tracking with personalized insights in the Oura app through Oura Membership',
            buyer_need='Want a clearer picture of your sleep and daily activity',
            consideration='Use the Oura Ring 4 sizing kit. Full app features require Oura Membership',
            routine='See your sleep and activity patterns together in the app, so you have information to reflect on your daily routine',
            evidence_source='https://ouraring.com/store/rings/oura-ring-4/silver',
            evidence_origin='reviewed_manufacturer_packet',
            evidence_checked='2026-10-04',
            supporting_sources=['https://support.ouraring.com/hc/en-us/articles/360025590653-How-to-Choose-the-Right-Oura-Ring-Size'])
    details = {}
    photos = listing_images(name, url, timeout=4, details=details)
    if not details.get('seller_benefit'):
        # One bounded basic research request, only through the connected
        # provider; no repeated search or paid text generation.
        from backend import live_research
        if live_research._is_tavily_configured():
            envelope = live_research.research(f'"{title}" product features specifications', max_results=4)
            found = facts_from_sources(name, url, getattr(envelope,'research_sources',[]) or [])
            if found: return found
        return {}
    return dict(benefit=details['seller_benefit'], buyer_need='', consideration='',
                evidence_source=details['seller_benefit_source'], evidence_origin='matched_seller_page',
                feature_details=details.get('seller_facts',[])[1:3], product_images=photos)


def canonical(url):
    p=urlsplit(url)
    query=tuple(sorted((k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                       if not k.lower().startswith('utm_') and k.lower() not in {'ref','tag','fbclid','gclid'}))
    return (p.scheme.lower(),(p.hostname or '').lower().removeprefix('www.'),p.port,p.path.rstrip('/'),query)


def facts_from_sources(name, destination, sources):
    """Only text attached to this exact listing; never a generic article."""
    expected=canonical(destination)
    facts=[];images=[]
    identity=' '.join(re.findall(r'[a-z0-9]+',product_name(name).lower()))
    for source in sources or []:
        if canonical(source.get('url','')) != expected:
            continue
        title=' '.join(re.findall(r'[a-z0-9]+',product_name(source.get('title','')).lower()))
        if not title or title != identity:
            continue
        for fact in usable_facts(source.get('content') or source.get('snippet') or ''):
            if fact not in facts: facts.append(fact)
        for image in source.get('image_urls',[]) or []:
            if isinstance(image,str) and image not in images:images.append(image)
    if facts:
        return dict(benefit=facts[0],feature_details=facts[1:3],buyer_need='',consideration='',
                    evidence_source=destination,evidence_origin='indexed_seller_description',
                    product_images=images[:3])
    return {}
