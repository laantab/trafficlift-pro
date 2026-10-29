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
    r'think about where you would use this feature|'
    r'looking for (?:a|an) (?:useful|handy|practical|simple) addition to (?:your|the) (?:daily )?routine)\b', re.I
)

def concise_caption(value, limit=65):
    """Preserve product-specific on-screen text without generic headings."""
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
    r'^(?:check|review|compare|see|tap|visit|choose|browse)\b.*'
    r'\b(?:listing|listed|price|options|details|specifications|features|seller)\b'
    r'|^(?:warranty and support|product description|key item features)$', re.I
)


def _fact_tokens(value):
    return frozenset(
        token for token in re.findall(r'[a-z0-9]+', value.casefold())
        if token not in {'a', 'an', 'the', 'and', 'or', 'for', 'to', 'of', 'in',
                         'on', 'at', 'by', 'with', 'through', 'from', 'is', 'are',
                         'item', 'product', 'seller', 'page', 'exact', 'listed',
                         'this', 'that', 'these', 'those', 'as', 'has', 'have'}
    )


def is_distinct_product_fact(value, product):
    """Accept a seller claim only when it is neither title metadata nor filler.

    This is a text-quality gate, not independent verification of seller truth.
    Provenance must still be attached by the caller to the exact listing.
    """
    try:
        fact = reject_retailer_fragment(clean(value, 160))
    except ValueError:
        return False
    tokens = _fact_tokens(fact)
    return bool(tokens and tokens != _fact_tokens(clean(product, 160)) and
                not GENERIC_CREATIVE.search(fact) and
                not GENERIC_EVIDENCE.search(fact))


def reject_retailer_fragment(value):
    """Do not narrate raw numbered listing bullets or storefront section labels.

    A numeric product specification such as '4-piece' is not a list marker.
    Cleaning Markdown remains separate so other callers can format headings.
    """
    if re.match(r'^\s*\d{1,2}\s*[,.)]\s+', value) or re.match(
        r'^(?:warranty\s*(?:and|&)\s*support|product description|'
        r'customer reviews?|product information|frequently bought together)'
        r'(?:\s*$|\s*[.,:;]\s+)', value, re.I
    ):
        raise ValueError('Seller section headings and numbered listing fragments are not product facts.')
    return value


def creative_copy_gate(plan):
    """Fail closed on generic ad placeholders and missing product identity."""
    product = str(plan.get('product') or '').strip()
    phrases = plan.get('phrases') or []
    captions = plan.get('captions') or []
    if not product or len(phrases) < 3 or len(phrases) != len(captions):
        raise ValueError('Creative review needs one identified product and matching scenes.')
    # Treat the final review as a trust boundary: plans can be restored from
    # saved jobs or supplied by callers that bypass build_sales_plan().
    # Recheck every spoken and visible scene, not just the initial seller facts.
    for value in [*phrases, *captions]:
        if not isinstance(value, str):
            raise ValueError('Creative review requires plain-text scene copy.')
        reject_retailer_fragment(clean(value, 360))
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
    # Listing sizes, quantities and selectable variants are not model/SKU
    # identifiers. A title like "MEZMUT 20 oz ... (Mixed1,6Pack)" must not
    # require those option tokens in a readable video headline. Genuine
    # alphanumeric model codes such as JAS11242 remain mandatory.
    def is_model(word):
        value = word.casefold()
        if re.fullmatch(r'(?:mixed|assorted|color|colour|variant|style|size)\d+', value):
            return False
        if re.fullmatch(r'\d+(?:pack|pk|count|ct|pcs?|pieces?|oz|ml|lb|g|kg|in|inch|cm|mm)', value):
            return False
        return len(word) >= 4 and any(c.isalpha() for c in word) and any(c.isdigit() for c in word)

    model_words = [word for word in identity_words if is_model(word)]
    if model_words:
        # A model number alone is not a meaningful product-specific headline.
        # Require the actual model AND at least two distinguishing product
        # descriptors when available. "JAS11242" with interchangeable
        # "Explore Lighting Ideas" captions must not pass final review.
        if not all(word.casefold() in visible_words for word in model_words):
            raise ValueError('On-screen captions must identify the exact product model.')
        descriptors = [word.casefold() for word in identity_words
                       if not any(char.isdigit() for char in word)
                       and word.casefold() not in {'model', 'series', 'color', 'finish'}]
        if len(set(descriptors) & visible_words) < min(2, len(set(descriptors))):
            raise ValueError('On-screen captions must identify the product type, not only its model.')
    elif identity_words:
        # Without a model code, require a recognizable brand/name plus the
        # product type, not a generic category headline or variant token.
        descriptors = [word.casefold() for word in identity_words
                       if not any(c.isdigit() for c in word)
                       and word.casefold() not in {'with', 'from', 'and', 'model', 'series', 'color', 'finish'}]
        if descriptors and (descriptors[0] not in visible_words or
                            len(set(descriptors) & visible_words) < min(2, len(set(descriptors)))):
            raise ValueError('On-screen captions must identify the product brand and type.')
    return {'status': 'PASS', 'product_identity': product, 'placeholder_copy': False}



def marketing_readiness_gate(plan):
    """A photo-only overview is not a publishable narrated advertisement.

    Preserve honest no-fact previews for research, but never spend CPU rendering
    five generic seller-listing reminders as a finished product video.
    Require two different product facts from the exact listing or an attributed
    seller/manufacturer brief. This checks source presence, not truth of claims;
    visual and factual human approval is still mandatory.
    """
    # Explain the evidence failure before running creative review. Otherwise a
    # title-only listing can throw the misleading "Generic placeholder" error.
    if plan.get('mode') != 'benefit_led':
        raise ValueError('Not enough product-specific seller facts for a narrated ad: the benefit is missing or repeats the title. Supply two distinct facts from the exact seller listing before rendering. No video was rendered.')
    distinct = []
    for item in (plan.get('evidence') or []):
        if not isinstance(item, dict):
            continue
        fact = str(item.get('text') or '').strip()
        if not is_distinct_product_fact(fact, plan.get('product', '')):
            continue
        tokens = _fact_tokens(fact)
        if not tokens:
            continue
        # Identical facts with formatting or boilerplate changes count once;
        # a longer fact adding a genuine new attribute still counts separately.
        if tokens in distinct:
            continue
        distinct.append(tokens)
    if len(distinct) < 2:
        raise ValueError('A narrated ad needs at least two distinct product facts from the exact listing. No video was rendered.')
    creative_copy_gate(plan)
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
    benefit = reject_retailer_fragment(clean(benefit))
    # Buyer-need hooks are spoken too; reject storefront section headings and
    # numbered listing scraps here just as we do for factual narration.
    need = reject_retailer_fragment(clean(buyer_need, 100)) if benefit else ''
    # A seller's interchangeable lifestyle teaser is not a product-specific hook.
    # Replace it with the exact product identity rather than narrating boilerplate.
    if GENERIC_CREATIVE.search(need):
        need = f'Considering {name}'
    consideration = reject_retailer_fragment(clean(consideration, 120))
    routine = reject_retailer_fragment(clean(routine, 160))
    details = [reject_retailer_fragment(clean(fact,160)) for fact in (feature_details or [])[:2]]
    # Source snippets and user-entered hooks can contain generic ad templates.
    # Discard those rather than allowing them to contaminate narration or
    # become false evidence. A repeated Markdown product title is not a fact.
    if not is_distinct_product_fact(benefit, name):
        benefit = ''
    if consideration and not is_distinct_product_fact(consideration, name):
        consideration = ''
    if routine and not is_distinct_product_fact(routine, name):
        routine = ''
    details = [fact for fact in details if is_distinct_product_fact(fact, name)]
    if not benefit:
        # If the exact seller listing actually supplied two different facts,
        # use those instead of a title-only pseudo-benefit. Never fabricate
        # facts from the product title, category or photograph.
        valid_details = []
        for fact in details:
            tokens = _fact_tokens(fact)
            if (is_distinct_product_fact(fact, name) and
                    tokens not in [_fact_tokens(x) for x in valid_details]):
                valid_details.append(fact)
        if len(valid_details) >= 2:
            benefit, details = valid_details[0], valid_details[1:]
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
    # Each factual scene must introduce a NEW seller-supported attribute.
    # With only two distinct facts, use four scenes rather than repeating the
    # same specification in both the feature and buying-details scenes.
    factual = []
    seen_facts = {_fact_tokens(benefit)}
    for fact in [*details, routine, consideration]:
        tokens = _fact_tokens(fact)
        if (not tokens or tokens in seen_facts or GENERIC_EVIDENCE.search(fact)):
            continue
        seen_facts.add(tokens)
        factual.append(fact)
    if factual:
        scene_facts = factual[:2]
        phrases = [hooks[0], body] + [fact + '.' for fact in scene_facts] + [
            'Tap the product link to compare the current price and options, then choose what fits your needs.'
        ]
        captions = [concise_caption(need), concise_caption(benefit)] + [
            concise_caption(fact) for fact in scene_facts
        ] + [concise_caption(name, 38)]
        stages = ['hook', 'benefit'] + (
            ['routine'] if len(scene_facts) == 1 else ['routine', 'buying_details']
        ) + ['cta']
    else:
        # A single fact can be previewed, but marketing_readiness_gate
        # refuses to render it as a finished narrated advertisement.
        phrases = [hooks[0], body,
                   f'For {name}, compare the seller photo and full specifications.',
                   f'For {name}, check the current seller listing for exact fit.',
                   'Tap the product link to compare the current price and options, then choose what fits your needs.']
        captions = [concise_caption(need), concise_caption(benefit),
                    concise_caption(name, 48), concise_caption(name, 48),
                    concise_caption(name, 38)]
        stages = ['hook', 'benefit', 'routine', 'buying_details', 'cta']
    status={'matched_seller_page':'Facts retrieved from the identity-matched seller page; seller claims are not independently tested',
            'indexed_seller_description':'Facts from research text attached to this exact seller listing; not independently tested',
            'reviewed_manufacturer_packet':f'Manufacturer information editorially checked {evidence_checked}; not a live lookup'}.get(
                evidence_origin,'Seller text supplied by user; not independently verified')
    plan={'version':'3.0','mode':'benefit_led','product':name,'buyer_need':need,
          'hook_candidates':hooks,'selected_hook':0,'phrases':phrases,
          'captions':captions,
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
