"""Evidence-led local script planning. No paid calls or simulated AI agents.

Seller text is attributed, never represented as independently verified.
Research, copy, visual planning and editorial review are separate stages.
"""
import re


def clean(value, limit=160):
    value = ' '.join(str(value or '').split())
    if len(value) > limit or any(c in value for c in '{}\\'):
        raise ValueError('Use short plain-text product facts without formatting codes.')
    return value.rstrip('.!?')


def review(plan, seconds):
    errors = []
    if not plan['evidence'] and plan.get('mode') not in {'photo_preview','product_overview'}:
        errors.append('A benefit-led script needs seller information.')
    script = ' '.join(plan['phrases'])
    # Honest photo-only output cannot prove performance or a real demonstration.
    if re.search(r'\b(watch (?:it|this)|as you can see|we tested|proven by|guaranteed|best ever|everyone loves|selling out|hurry|limited time)\b', script, re.I):
        errors.append('Remove unsupported proof, popularity, guarantees or urgency.')
    if re.search(r'[$£€]|\b\d+\s*%\s*off\b', script):
        errors.append('Use the link for current pricing; a dated price source is required before quoting prices.')
    if plan.get('mode') != 'product_overview' and len(script.split()) > int((seconds - 1.6) * 2.25):
        errors.append('The script needs a longer video. Choose 30 or 60 seconds, or shorten the seller benefit.')
    return {'status': 'FAIL' if errors else 'PASS', 'errors': errors,
            'evidence_status': ('No seller benefit supplied; photo preview makes no benefit claims'
                                if plan.get('mode') == 'photo_preview' else
                                plan.get('evidence_status', 'Seller text supplied by user; not independently verified')),
            'visual_mode': 'Real product photo and close-up framing; no performance demonstration'}


def build_sales_plan(name, benefit, destination, seconds, buyer_need='', consideration='', *, evidence_source=None, evidence_origin='user_supplied_seller_text', evidence_checked=None, supporting_sources=None, routine='', feature_details=None):
    from backend.listing_photo import product_name
    name = clean(product_name(name), 90)
    benefit = clean(benefit)
    need = clean(buyer_need, 100)
    consideration = clean(consideration, 120)
    routine = clean(routine, 160)
    details = [clean(fact,160) for fact in (feature_details or [])[:2]]
    if not benefit:
        # Missing seller text must not turn a valid photo render into a dead end.
        # Offer a complete, explicitly labeled buying overview without claims.
        phrases = [f'Considering {name}?',
                   f'Here is a closer look at {name}.',
                   'Picture where you would use it in your daily routine, and what you need from it.',
                   'Check the seller listing for dimensions, included items, and care details before deciding.',
                   'Tap the product link to compare the current price and options, then choose what fits your needs.']
        plan = {'version': '3.1', 'mode': 'product_overview', 'product': name,
                'buyer_need': '', 'hook_candidates': [phrases[0]], 'selected_hook': 0,
                'phrases': phrases,
                'captions': ['Could this fit your routine?', 'The actual product', 'Plan your daily use', 'Check size and included items', 'Check price and options'],
                'destination': destination, 'evidence': [],
                'evidence_status': 'Product identity and photo only; benefit details unavailable. No performance or feature claims added',
                'scenes': [{'stage': stage, 'narration': phrase, 'visual': 'Move across the actual product photo; no performance demonstration'}
                           for stage, phrase in zip(['hook', 'product', 'routine', 'buying_details', 'cta'], phrases)],
                'workflow': ['conversion_copy', 'visual_direction', 'editorial_review'],
                'engine': 'local product-overview rules; no language-model calls'}
        plan['review'] = review(plan, seconds)
        if plan['review']['errors']:
            raise ValueError(' '.join(plan['review']['errors']))
        return plan
    source = evidence_source or destination
    evidence = [{'text': benefit, 'source_url': source, 'origin': evidence_origin}]
    if consideration:
        evidence.append({'text': consideration, 'source_url': source, 'origin': evidence_origin,
                         'supporting_sources': supporting_sources or []})
    for fact in details + ([routine] if routine else []):
        evidence.append({'text':fact,'source_url':source,'origin':evidence_origin})
    if not need:
        low=benefit.lower()
        need=('Looking for an easier setup at your desk' if 'desk' in low else
              'Looking for a useful addition to your daily routine')
    hooks=[f'{need}?',f'Could {name} fit your routine?',f'Considering {name}?']
    body=f'{name}: {benefit}.'
    feature=('. '.join(details)+'.') if details else (routine+'.' if routine else
              'Think about where you would use this feature in your day.')
    buying=(consideration+'.') if consideration else 'Check the listed size and what is included to choose the right option.'
    phrases=[hooks[0],body,feature,buying,
             'Tap the product link to compare the current price and options, then choose what fits your needs.']
    status={'matched_seller_page':'Facts retrieved from the identity-matched seller page; seller claims are not independently tested',
            'indexed_seller_description':'Facts from research text attached to this exact seller listing; not independently tested',
            'reviewed_manufacturer_packet':f'Manufacturer information editorially checked {evidence_checked}; not a live lookup'}.get(
                evidence_origin,'Seller text supplied by user; not independently verified')
    caption=benefit if len(benefit)<=65 else 'Features for your daily routine'
    if 'sleep' in benefit.lower() and 'activity' in benefit.lower():caption='Sleep and activity insights'
    stages=['hook','benefit','routine','buying_details','cta']
    plan={'version':'3.0','mode':'benefit_led','product':name,'buyer_need':need,
          'hook_candidates':hooks,'selected_hook':0,'phrases':phrases,
          'captions':[need if len(need)<=65 else 'Could this fit your routine?',caption,'How it fits your day','Choose the right option','Check price and options'],
          'destination':destination,'evidence':evidence,'evidence_status':status,
          'scenes':[{'stage':stage,'narration':phrase,'visual':'Move across the actual product photo; do not imply a performance test'}
                    for stage,phrase in zip(stages,phrases)],
          'workflow':['buyer_research','conversion_copy','visual_direction','editorial_review'],
          'engine':'local evidence-led rules; no language-model calls'}
    plan['review'] = review(plan, seconds)
    if plan['review']['errors']:
        raise ValueError(' '.join(plan['review']['errors']))
    return plan
