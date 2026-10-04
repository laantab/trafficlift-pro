"""Automatic seller facts plus dated, model-specific editorial source packets.
No paid API calls. A reviewed packet is never represented as a live lookup.
"""
import re
from datetime import date
from backend.listing_photo import listing_images, product_name


def resolve_video_facts(name, url):
    title = product_name(name)
    # Exact ring model only: never apply ring features to a kit or accessory.
    if re.fullmatch(r'Oura Ring 4(?: (?:Silver|Black|Gold|Stealth|Brushed Silver|Rose Gold))?', title, re.I) and date.today() <= date(2026, 12, 31):
        return dict(
            benefit='Sleep and activity tracking with personalized insights in the Oura app through Oura Membership',
            buyer_need='Want a clearer picture of your sleep and daily activity',
            consideration='Use the Oura Ring 4 sizing kit. Full app features require Oura Membership',
            evidence_source='https://ouraring.com/store/rings/oura-ring-4/silver',
            evidence_origin='reviewed_manufacturer_packet',
            evidence_checked='2026-10-04',
            supporting_sources=['https://support.ouraring.com/hc/en-us/articles/360025590653-How-to-Choose-the-Right-Oura-Ring-Size'])
    details = {}
    listing_images(name, url, timeout=4, details=details)
    if not details.get('seller_benefit'):
        return {}
    return dict(benefit=details['seller_benefit'], buyer_need='', consideration='',
                evidence_source=details['seller_benefit_source'], evidence_origin='matched_seller_page')
