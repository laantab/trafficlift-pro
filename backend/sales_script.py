"""Evidence-led local script planning. No paid calls or simulated AI agents.

Seller text is attributed, never represented as independently verified.
Research, copy, visual planning and editorial review are separate stages.
"""
import re


def clean(value, limit=160):
    """Turn seller-page snippets into spoken plain text, not markdown artifacts.

    This never invents features. Reject overlong or code-like input rather than
    clipping factual claims into misleading sentence fragments.
    """
    value = str(value or '')
    # Strip indexed research citations, Markdown headings and lightweight inline
    # markup before the text enters either narration or on-screen captions.
    value = re.sub(r'\[\d+(?:\s*[,;-]\s*\d+)*\]', '', value)
    value = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', value)
    value = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', value)
    value = re.sub(r'(?m)^\s*[-*]\s+', '', value)
    value = re.sub(r'[*_`]+', '', value)
    value = ' '.join(value.split())
    # A bare URL or truncated retailer snippet is not clean spoken narration.
    # Markdown links have already been reduced to their human-readable labels.
    if re.search(r'https?://|www\.|\.{3,}|…|\||\b(?:read more|see more)\b', value, re.I):
        raise ValueError('Use complete plain-text product facts without retailer fragments.')
    if len(value) > limit or any(c in value for c in '{}\\<>|'):
        raise ValueError('Use short plain-text product facts without formatting codes.')
    return value.rstrip('.!?')


GENERIC_CREATIVE = re.compile(
    r'\b(?:review listed features|picture your setup|key item features|'
    r'explore (?:tech(?:\s*&\s*gadgets)?|gadgets|product|useful(?:\s+product)?) ideas|product overview|'
    r'think about where you would use this feature)\b', re.I
)

def concise_caption(value, limit=65):
    """Use a meaningful seller-fact excerpt, never a generic filler heading.

    Stop at word boundaries and remove dangling conjunctions/prepositions.
    The full, unabridged fact remains in the narration and evidence record.
    """
    value = clean(value)
    if len(value) <= limit:
        return value
    words = []
    for word in value.split():
        if len(' '.join(words + [word])) > limit:
            break
        words.append(word)
    while words and words[-1].casefold().rstrip('.,;:') in {
        'a', 'an', 'the', 'and', 'or', 'for', 'to', 'of', 'in', 'on',
        'at', 'by', 'with', 'through', 'from', 'is', 'are',
    }:
        words.pop()
    if len(words) < 2:
        raise ValueError('Product fact needs a shorter complete caption.')
    return ' '.join(words).rstrip('.,;:')


GENERIC_EVIDENCE = re.compile(
    r'^(?:check|review|compare|see|tap|visit|choose|browse)\\b.*'
    r'\\b(?:listing|listed|price|options|details|specifications|features|seller)\\b'
    r'|^(?:warranty and support|product description|key item features)
    """Fail closed on generic ad placeholders and missing product identity."""
    product = str(plan.get('product') or '').strip()
    phrases = plan.get('phrases') or []
    captions = plan.get('captions') or []
    if not product or len(phrases) < 3 or len(phrases) != len(captions):
        raise ValueError('Creative review needs one identified product and matching scenes.')
    spoken = ' '.join(phrases)
    visible = ' '.join(captions)
    if GENERIC_CREATIVE.search(spoken + ' ' + visible):
        raise ValueError('Generic placeholder copy is not acceptable for a product advertisement.')
    # Identity must survive retailer-name cleanup and be present in the ad.
    if product.casefold() not in spoken.casefold():
        raise ValueError('Narration must identify the exact product.')
    # Captions must not be interchangeable across unrelated products.
    identity_words = [word for word in re.findall(r'[A-Za-z0-9]+', product)
                      if len(word) >= 4]
    visible_words = {word.casefold() for word in re.findall(r'[A-Za-z0-9]+', visible)}
    model_words = [word for word in identity_words if any(char.isdigit() for char in word)]
    required_words = model_words or identity_words
    if required_words and not any(word.casefold() in visible_words for word in required_words):
        raise ValueError('On-screen captions must identify the product.')
    return {'status': 'PASS', 'product_identity': product, 'placeholder_copy': False}



def marketing_readiness_gate(plan):
    """A photo-only overview is not a publishable narrated advertisement.

    Preserve honest no-fact previews for research, but never spend CPU rendering
    five generic seller-listing reminders as a finished product video.
    Require two different product facts from the exact listing or an attributed
    seller/manufacturer brief. This checks source presence, not truth of claims;
    visual and factual human approval is still mandatory.
    """
    creative_copy_gate(plan)
    if plan.get('mode') != 'benefit_led':
        raise ValueError('Not enough product-specific seller facts for a narrated ad. No video was rendered.')
    distinct = []
    for item in (plan.get('evidence') or []):
        if not isinstance(item, dict):
            continue
        fact = str(item.get('text') or '').strip()
        if not fact or GENERIC_CREATIVE.search(fact) or GENERIC_EVIDENCE.search(fact):
            continue
        tokens = _fact_tokens(fact)
        if not tokens:
            continue
        # Two punctuation variants or a detail repeated with extra boilerplate
        # do not count as two independent selling points.
        if any(tokens <= prior or prior <= tokens for prior in distinct):
            continue
        distinct.append(tokens)
    if len(distinct) < 2:
        raise ValueError('A narrated ad needs at least two distinct product facts from the exact listing. No video was rendered.')
    return {'status': 'PASS', 'distinct_product_facts': len(distinct)}


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
                   f'This is the actual {name}, shown in the seller photo.',
                   f'Compare the shape of {name} with your intended space.',
                   'Check the seller listing for exact dimensions, included items, and care details.',
                   'Tap the product link to compare the current price and options before deciding.']
        plan = {'version': '3.1', 'mode': 'product_overview', 'product': name,
                'buyer_need': '', 'hook_candidates': [phrases[0]], 'selected_hook': 0,
                'phrases': phrases,
                'captions': [f'Considering {name[:42]}', f'{name[:42]}: actual photo',
                             f'{name[:42]}: check the fit', f'{name[:42]}: size and contents',
                             f'{name[:42]}: listing options'],
                'destination': destination, 'evidence': [],
                'evidence_status': 'Product identity and photo only; benefit details unavailable. No performance or feature claims added',
                'scenes': [{'stage': stage, 'narration': phrase, 'visual': 'Move across the actual product photo; no performance demonstration'}
                           for stage, phrase in zip(['hook', 'product', 'routine', 'buying_details', 'cta'], phrases)],
                'workflow': ['conversion_copy', 'visual_direction', 'editorial_review'],
                'engine': 'local product-overview rules; no language-model calls'}
        plan['creative_review'] = creative_copy_gate(plan)
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
        need=f'Considering {name}'
    hooks=[f'{need}?',f'Could {name} fit your routine?',f'Considering {name}?']
    body=f'{name}: {benefit}.'
    feature=('. '.join(details)+'.') if details else (routine+'.' if routine else
              (consideration+'.' if consideration else
               f'For {name}, compare the seller photo and full specifications.'))
    buying=(consideration+'.') if consideration else (
        details[-1]+'.' if details else (routine+'.' if routine else
        f'For {name}, check the current seller listing for exact fit.'))
    phrases=[hooks[0],body,feature,buying,
             'Tap the product link to compare the current price and options, then choose what fits your needs.']
    status={'matched_seller_page':'Facts retrieved from the identity-matched seller page; seller claims are not independently tested',
            'indexed_seller_description':'Facts from research text attached to this exact seller listing; not independently tested',
            'reviewed_manufacturer_packet':f'Manufacturer information editorially checked {evidence_checked}; not a live lookup'}.get(
                evidence_origin,'Seller text supplied by user; not independently verified')
    caption=concise_caption(benefit)
    feature_caption=concise_caption(details[0] if details else (routine or consideration or benefit))
    buying_caption=concise_caption(consideration or (details[-1] if details else (routine or benefit)))
    stages=['hook','benefit','routine','buying_details','cta']
    plan={'version':'3.0','mode':'benefit_led','product':name,'buyer_need':need,
          'hook_candidates':hooks,'selected_hook':0,'phrases':phrases,
          'captions':[concise_caption(need),
                      caption,
                      feature_caption,
                      buying_caption,
                      f'{concise_caption(name, 38)}: see current listing'],
          'destination':destination,'evidence':evidence,'evidence_status':status,
          'scenes':[{'stage':stage,'narration':phrase,'visual':'Move across the actual product photo; do not imply a performance test'}
                    for stage,phrase in zip(stages,phrases)],
          'workflow':['buyer_research','conversion_copy','visual_direction','editorial_review'],
          'engine':'local evidence-led rules; no language-model calls'}
    plan['creative_review'] = creative_copy_gate(plan)
    plan['review'] = review(plan, seconds)
    if plan['review']['errors']:
        raise ValueError(' '.join(plan['review']['errors']))
    return plan
, re.I
)


def _fact_tokens(value):
    return frozenset(
        token for token in re.findall(r'[a-z0-9]+', value.casefold())
        if token not in {'a', 'an', 'the', 'and', 'or', 'for', 'to', 'of', 'in',
                         'on', 'at', 'by', 'with', 'through', 'from', 'is', 'are',
                         'item', 'product', 'seller', 'page', 'exact', 'listed'}
    )


def creative_copy_gate(plan):
    """Fail closed on generic ad placeholders and missing product identity."""
    product = str(plan.get('product') or '').strip()
    phrases = plan.get('phrases') or []
    captions = plan.get('captions') or []
    if not product or len(phrases) < 3 or len(phrases) != len(captions):
        raise ValueError('Creative review needs one identified product and matching scenes.')
    spoken = ' '.join(phrases)
    visible = ' '.join(captions)
    if GENERIC_CREATIVE.search(spoken + ' ' + visible):
        raise ValueError('Generic placeholder copy is not acceptable for a product advertisement.')
    # Identity must survive retailer-name cleanup and be present in the ad.
    if product.casefold() not in spoken.casefold():
        raise ValueError('Narration must identify the exact product.')
    # Captions must not be interchangeable across unrelated products.
    identity_words = [word for word in re.findall(r'[A-Za-z0-9]+', product)
                      if len(word) >= 4]
    visible_words = {word.casefold() for word in re.findall(r'[A-Za-z0-9]+', visible)}
    model_words = [word for word in identity_words if any(char.isdigit() for char in word)]
    required_words = model_words or identity_words
    if required_words and not any(word.casefold() in visible_words for word in required_words):
        raise ValueError('On-screen captions must identify the product.')
    return {'status': 'PASS', 'product_identity': product, 'placeholder_copy': False}



def marketing_readiness_gate(plan):
    """A photo-only overview is not a publishable narrated advertisement.

    Preserve honest no-fact previews for research, but never spend CPU rendering
    five generic seller-listing reminders as a finished product video.
    Require two different product facts from the exact listing or an attributed
    seller/manufacturer brief. This checks source presence, not truth of claims;
    visual and factual human approval is still mandatory.
    """
    creative_copy_gate(plan)
    if plan.get('mode') != 'benefit_led':
        raise ValueError('Not enough product-specific seller facts for a narrated ad. No video was rendered.')
    facts = {
        re.sub(r'\W+', ' ', str(item.get('text') or '')).strip().casefold()
        for item in (plan.get('evidence') or [])
        if isinstance(item, dict) and str(item.get('text') or '').strip()
    }
    if len(facts) < 2:
        raise ValueError('A narrated ad needs at least two distinct product facts from the exact listing. No video was rendered.')
    return {'status': 'PASS', 'distinct_product_facts': len(facts)}


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
                   f'This is the actual {name}, shown in the seller photo.',
                   f'Compare the shape of {name} with your intended space.',
                   'Check the seller listing for exact dimensions, included items, and care details.',
                   'Tap the product link to compare the current price and options before deciding.']
        plan = {'version': '3.1', 'mode': 'product_overview', 'product': name,
                'buyer_need': '', 'hook_candidates': [phrases[0]], 'selected_hook': 0,
                'phrases': phrases,
                'captions': [f'Considering {name[:42]}', f'{name[:42]}: actual photo',
                             f'{name[:42]}: check the fit', f'{name[:42]}: size and contents',
                             f'{name[:42]}: listing options'],
                'destination': destination, 'evidence': [],
                'evidence_status': 'Product identity and photo only; benefit details unavailable. No performance or feature claims added',
                'scenes': [{'stage': stage, 'narration': phrase, 'visual': 'Move across the actual product photo; no performance demonstration'}
                           for stage, phrase in zip(['hook', 'product', 'routine', 'buying_details', 'cta'], phrases)],
                'workflow': ['conversion_copy', 'visual_direction', 'editorial_review'],
                'engine': 'local product-overview rules; no language-model calls'}
        plan['creative_review'] = creative_copy_gate(plan)
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
        need=f'Considering {name}'
    hooks=[f'{need}?',f'Could {name} fit your routine?',f'Considering {name}?']
    body=f'{name}: {benefit}.'
    feature=('. '.join(details)+'.') if details else (routine+'.' if routine else
              f'For {name}, compare the seller photo and full specifications.')
    buying=(consideration+'.') if consideration else 'Check the listed size and what is included to choose the right option.'
    phrases=[hooks[0],body,feature,buying,
             'Tap the product link to compare the current price and options, then choose what fits your needs.']
    status={'matched_seller_page':'Facts retrieved from the identity-matched seller page; seller claims are not independently tested',
            'indexed_seller_description':'Facts from research text attached to this exact seller listing; not independently tested',
            'reviewed_manufacturer_packet':f'Manufacturer information editorially checked {evidence_checked}; not a live lookup'}.get(
                evidence_origin,'Seller text supplied by user; not independently verified')
    caption=benefit if len(benefit)<=65 else f'{name[:36]}: seller-listed details'
    if 'sleep' in benefit.lower() and 'activity' in benefit.lower():caption='Sleep and activity insights'
    stages=['hook','benefit','routine','buying_details','cta']
    plan={'version':'3.0','mode':'benefit_led','product':name,'buyer_need':need,
          'hook_candidates':hooks,'selected_hook':0,'phrases':phrases,
          'captions':[need if len(need)<=65 else f'{name[:40]}: a closer look',
                      caption,
                      (details[0][:65] if details else f'{name[:38]}: exact listing photo'),
                      f'{name[:38]}: size and options',
                      f'{name[:38]}: see listing'],
          'destination':destination,'evidence':evidence,'evidence_status':status,
          'scenes':[{'stage':stage,'narration':phrase,'visual':'Move across the actual product photo; do not imply a performance test'}
                    for stage,phrase in zip(stages,phrases)],
          'workflow':['buyer_research','conversion_copy','visual_direction','editorial_review'],
          'engine':'local evidence-led rules; no language-model calls'}
    plan['creative_review'] = creative_copy_gate(plan)
    plan['review'] = review(plan, seconds)
    if plan['review']['errors']:
        raise ValueError(' '.join(plan['review']['errors']))
    return plan
