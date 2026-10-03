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
    if not plan['evidence'] and plan.get('mode') != 'photo_preview':
        errors.append('A benefit-led script needs seller information.')
    script = ' '.join(plan['phrases'])
    # Honest photo-only output cannot prove performance or a real demonstration.
    if re.search(r'\b(watch (?:it|this)|as you can see|we tested|proven by|guaranteed|best ever|everyone loves|selling out|hurry|limited time)\b', script, re.I):
        errors.append('Remove unsupported proof, popularity, guarantees or urgency.')
    if re.search(r'[$£€]|\b\d+\s*%\s*off\b', script):
        errors.append('Use the link for current pricing; a dated price source is required before quoting prices.')
    if len(script.split()) > int((seconds - 1.6) * 2.25):
        errors.append('The script needs a longer video. Choose 30 or 60 seconds, or shorten the seller benefit.')
    return {'status': 'FAIL' if errors else 'PASS', 'errors': errors,
            'evidence_status': ('No seller benefit supplied; photo preview makes no benefit claims'
                                if plan.get('mode') == 'photo_preview' else
                                'Seller text supplied by user; not independently verified'),
            'visual_mode': 'Real product photo and close-up framing; no performance demonstration'}


def build_sales_plan(name, benefit, destination, seconds, buyer_need='', consideration=''):
    name = clean(name, 90)
    benefit = clean(benefit)
    need = clean(buyer_need, 100)
    consideration = clean(consideration, 120)
    if not benefit:
        # Discovery hands over a photo and identity, not a verified benefit.
        # Render an explicit photo preview instead of inventing a fact or
        # requiring the user to research and fill a field before every video.
        spoken_name = ' '.join(name.split()[:8])
        phrases = [f'Take a closer look at {spoken_name}.',
                   'Explore the product photos.',
                   'Tap the product link for current pricing and specifications.']
        plan = {'version': '2.1', 'mode': 'photo_preview', 'product': name,
                'buyer_need': '', 'hook_candidates': [phrases[0]], 'selected_hook': 0,
                'phrases': phrases, 'captions': ['A closer look', 'Product photo', 'Check details and price'],
                'destination': destination, 'evidence': [],
                'scenes': [{'stage': stage, 'narration': phrase, 'visual': 'Show the actual product photo; no performance demonstration'}
                           for stage, phrase in zip(['hook', 'photo', 'cta'], phrases)],
                'workflow': ['conversion_copy', 'visual_direction', 'editorial_review'],
                'engine': 'local photo-preview rules; no language-model calls'}
        plan['review'] = review(plan, seconds)
        if plan['review']['errors']:
            raise ValueError(' '.join(plan['review']['errors']))
        return plan
    # Buyer research: preserve the source and exact supplied fact rather than
    # treating generated Pinterest copy as factual evidence.
    evidence = [{'text': benefit, 'source_url': destination, 'origin': 'user_supplied_seller_text'}] if benefit else []
    if consideration:
        evidence.append({'text': consideration, 'source_url': destination, 'origin': 'user_supplied_seller_text'})
    # Conversion copy: three candidates grounded in this product and need.
    hooks = ([f'{need}? Take a closer look at {name}.',
              f'Looking for {need.lower()}? Meet {name}.',
              f'Meet {name}. A closer look for your everyday routine.'] if need else
             [f'{name}: {benefit}.', f'Considering {name}? Here is a reason to look closer.',
              f'Meet {name}. See how it could fit your routine.'])
    chosen = 0
    phrases = [hooks[chosen], benefit if need else f'Take a closer look at {name}.',
               'Tap the product link to check the current price and product details.']
    if consideration:
        phrases[1] += ' ' + consideration + '.'
    captions = [need or 'A reason to look closer', 'Product details', 'Check details and price']
    plan = {'version': '2.0', 'product': name, 'buyer_need': need,
            'hook_candidates': hooks, 'selected_hook': chosen, 'phrases': phrases,
            'captions': captions, 'destination': destination, 'evidence': evidence,
            'scenes': [{'stage': stage, 'narration': phrase, 'visual': visual}
                       for stage, phrase, visual in zip(['hook', 'benefit', 'cta'], phrases,
                       ['Show the actual product photo', 'Gently move across the actual product photo; do not imply a performance test',
                        'Keep the product visible with one destination CTA'])],
            'workflow': ['buyer_research', 'conversion_copy', 'visual_direction', 'editorial_review'],
            'engine': 'local evidence-led rules; no language-model calls'}
    plan['review'] = review(plan, seconds)
    if plan['review']['errors']:
        raise ValueError(' '.join(plan['review']['errors']))
    return plan
