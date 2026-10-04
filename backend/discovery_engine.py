"""Fresh-only discovery: rotated research, evidence ranking, bounded qualification.

Randomness diversifies research and equal-quality candidates; it is not evidence
of demand. A saved-list item can never masquerade as a fresh winner here.
"""
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
import random
import time
from urllib.parse import urlsplit
from fastapi import HTTPException
import logging
from backend import live_research
from backend.winner_history import WinnerHistory, identity_keys, product_id

CATEGORIES = ['home organization', 'kitchen tools', 'pet care', 'desk accessories',
              'gardening tools', 'travel accessories', 'fitness accessories',
              'lighting and home decor', 'craft supplies', 'cleaning tools',
              'photography accessories', 'personal accessories']
SEARCH_VARIANTS = ['popular product reviews', 'best selling products', 'new product releases']
MERCHANT_FILTERS = ['site:amazon.com/dp/', 'site:walmart.com/ip/', 'site:target.com/p/', '']
MAX_QUERIES = 3
MAX_CANDIDATES = 12
MAX_QUALIFICATION = MAX_CANDIDATES
REQUEST_SECONDS = 32
RESEARCH_SECONDS = 16
logger = logging.getLogger(__name__)
_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix='discovery')


def query_plan(cursor, seed=None):
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    now = datetime.now(timezone.utc)
    # Deterministic category rotation guarantees coverage; phrasing varies.
    return [f'{CATEGORIES[(cursor*MAX_QUERIES+i)%len(CATEGORIES)]} '
            f'{rng.choice(SEARCH_VARIANTS)} {now.strftime("%B %Y")} '
            f'{MERCHANT_FILTERS[(cursor+i)%len(MERCHANT_FILTERS)]}'.strip() for i in range(MAX_QUERIES)]


def bounded_map(function, values, seconds):
    """At most three in-flight operations; do not wait past the phase deadline.

    Running network calls finish under provider timeouts; queued calls cancel.
    No background operation reserves or returns a winner.
    """
    # Shared pool caps threads across simultaneous HTTP requests; an expired
    # request never creates a fresh set of background workers on the next click.
    phase_deadline = time.monotonic()+max(0, seconds)
    def run(value):
        if time.monotonic() >= phase_deadline: return None
        return function(value)
    futures = {_POOL.submit(run, v): i for i, v in enumerate(values)}
    done, pending = wait(futures, timeout=max(0, seconds))
    results = []
    for future in sorted(done, key=lambda f: futures[f]):
        try:
            result = future.result()
            if result is not None: results.append(result)
        except Exception as exc:
            logger.warning('Discovery operation failed (%s)', type(exc).__name__)
    for future in pending: future.cancel()
    return results


def matching_sources(name, envelopes):
    # Exact normalized model/name phrase, not a shared token such as 'kitchen'.
    import re
    phrase = ' '.join(re.findall(r'[a-z0-9]+', name.lower()))
    found = {}
    for env in envelopes:
        for source in env.research_sources or []:
            text = ' '.join(re.findall(r'[a-z0-9]+', ' '.join([source.get('title',''), source.get('snippet',source.get('content',''))]).lower()))
            if phrase and f' {phrase} ' in f' {text} ':
                url = source.get('url','')
                if urlsplit(url).scheme in {'http','https'} and urlsplit(url).hostname:
                    found[url] = dict(source)
    return list(found.values())


def market_signals(sources):
    import re
    terms = r'\b(best[ -]?sell(?:er|ing)|popular|demand|trending|reviews?|ratings?|orders?|sold|sales)\b'
    return [s for s in sources if re.search(terms, s.get('title','')+' '+s.get('snippet',s.get('content','')), re.I)]


def discover(*, exclude_ids=None, exclude_keys=None, client_id=None, seed=None):
    """One click continues through empty free-research batches, within 60s."""
    started = time.monotonic()
    deadline = started + 60
    # Never multiply paid-provider calls through automatic retries.
    batches = 1 if live_research._is_tavily_configured() else 3
    last_error = None
    completed_batches = 0
    for attempt in range(batches):
        if time.monotonic() >= deadline: break
        completed_batches += 1
        try:
            winner = _discover_batch(exclude_ids=exclude_ids, exclude_keys=exclude_keys,
                                     client_id=client_id, seed=seed, request_deadline=deadline)
            winner.setdefault('discovery', {}).update(research_batches=attempt+1,
                elapsed_seconds=round(time.monotonic()-started,3))
            return winner
        except HTTPException as exc:
            if exc.status_code != 404: raise
            last_error = exc
            logger.info('Empty discovery batch %d/%d; continuing free research', attempt+1,batches)
    detail = last_error.detail if last_error else 'Research time limit reached.'
    raise HTTPException(404, f'No qualified new product after {completed_batches} research batch(es). '
                        'Previous products remain excluded. ' + str(detail))


def _discover_batch(*, exclude_ids=None, exclude_keys=None, client_id=None, seed=None, request_deadline=None):
    # Imports deferred to avoid the existing product_research/discovery cycle.
    from backend.discovery import _build_candidates_from_envelope, _find_image_for_candidate
    from backend.product_research import ProductResearcher
    from backend.product_control_agent import ProductControlAgent
    started = time.monotonic()
    deadline = min(started + REQUEST_SECONDS, request_deadline or float("inf"))
    history = WinnerHistory(client_id) if client_id else None
    seen = set(exclude_keys or []) | (history.seen() if history else set())
    excluded = set(exclude_ids or [])
    cursor = history.next_cursor() if history else random.SystemRandom().randrange(len(CATEGORIES))
    queries = query_plan(cursor, seed)
    envelopes = bounded_map(lambda q: live_research.research(q, max_results=6), queries, min(RESEARCH_SECONDS, max(0,deadline-time.monotonic())))
    available = [e for e in envelopes if e.research_status in {'live','partial'} and e.research_sources]
    if not available:
        errors = sorted({message for env in envelopes if env is not None
                         for message in getattr(env, 'research_errors', [])})
        detail = ' '.join(errors) or 'Search providers timed out or returned no usable results.'
        raise HTTPException(503, 'Live product research is unavailable. ' + detail +
                            ' No saved-list product was substituted.')
    titles = set()
    candidates = []
    candidate_keys = set()
    for env in available:
        for card in _build_candidates_from_envelope(env, titles):
            if card.id in excluded: continue
            keys = identity_keys(card.name, card.url)
            card.id = product_id(card.name, card.url)
            if card.id in excluded or set(keys) & (seen | candidate_keys): continue
            sources = matching_sources(card.name, available)
            if not sources: continue
            signals = market_signals(sources)
            if not signals: continue
            domains = {urlsplit(s['url']).hostname.removeprefix('www.') for s in sources}
            # Evidence breadth is a transparent research signal, not sales,
            # profitability, or a fabricated trend percentage.
            score = min(len(domains), 5)*8 + min(len(sources), 5)*2 + min(len(signals), 5)*2
            candidates.append({'card': card, 'keys': keys, 'sources': sources,
                               'domains': len(domains), 'score': score, 'market_signals': signals,
                               'images': list(getattr(env, 'research_image_urls', []) or [])})
            candidate_keys.update(keys)
            if len(candidates) >= MAX_CANDIDATES: break
        if len(candidates) >= MAX_CANDIDATES: break
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    rng.shuffle(candidates)
    candidates.sort(key=lambda c: c['score'], reverse=True)
    if not candidates:
        raise HTTPException(404, 'No new concrete product qualified in this research batch. Previous products remain excluded. Try again to research different categories.')

    rejection_log = []

    def qualify(candidate):
        if time.monotonic() >= deadline: return None
        card = candidate['card']
        per_candidate_deadline = min(deadline, time.monotonic()+9)
        from backend.listing_photo import listing_images
        from backend.product_control_agent import _validate_image, rank_image_candidates
        listing_details = {}
        listing_urls = listing_images(card.name, card.url, details=listing_details, timeout=min(4, max(.1, per_candidate_deadline-time.monotonic())))
        # Listing identity was checked before these photos were returned.
        # URL-keyword scores sort photos; they must not veto a verified photo
        # just because its seller CDN uses an opaque filename.
        ranked_listing = rank_image_candidates(listing_urls, product_name=card.name, category=card.category)
        listing_result = None
        for url, score in ranked_listing:
            if time.monotonic() >= per_candidate_deadline: break
            if _validate_image(url, head_timeout=1).ok:
                listing_result = (url, score)
                break
        image = listing_result[0] if listing_result else None
        if not image:
            image = _find_image_for_candidate(card, tavily_image_urls=candidate['images'], deadline_monotonic=per_candidate_deadline)
        if not image or time.monotonic() >= deadline:
            rejection_log.append('photo')
            logger.info('Discovery rejected photo: product=%r listing_images=%d', card.name, len(listing_urls))
            return None
        card.image_url = image
        payload = ProductResearcher()._materialize(card, card.category)
        payload.update(source='discovery', image_url=image, **listing_details)
        report = ProductControlAgent.evaluate(payload)
        if not report.ok:
            rejection_log.append('product')
            logger.info('Discovery rejected product=%r reasons=%s', card.name, report.reasons)
            return None
        report.product['image_origin'] = 'matched_product_listing' if listing_result else 'image_research'
        return candidate, report.product

    attempts = candidates[:MAX_QUALIFICATION]
    qualified = bounded_map(qualify, attempts, deadline-time.monotonic())
    qualified.sort(key=lambda item: item[0]['score'], reverse=True)
    for candidate, winner in qualified:
        if history and not history.claim(candidate['keys']): continue
        winner.update(id=candidate['card'].id, name=candidate['card'].name,
                      url=candidate['card'].url, source='discovery', image_status='verified',
                      identity_keys=candidate['keys'], research_status='live',
                      research_timestamp=datetime.now(timezone.utc).isoformat(),
                      research_provider=', '.join(sorted({getattr(e, 'research_provider', 'unknown') for e in available})),
                      research_sources=candidate['sources'],
                      trend_score=candidate['score'], margin_estimate='Not established',
                      competition='Not established', evergreen_score=None,
                      competition_reasons=['This research does not establish competition or profit.'],
                      trend_signals=[f'Exact product name found in {len(candidate["sources"])} source(s) across {candidate["domains"]} domain(s).',
                                     f'{len(candidate["market_signals"])} source(s) mention review, popularity or sales terms; actual sales volume is not verified.'],
                      selection_rationale='Selected by product-specific source breadth among new products with qualified photos. This is a researched opportunity, not a guarantee of sales.',
                      viral_hook=f'Take a closer look at {candidate["card"].name}.',
                      angle=f'Explore {candidate["card"].name} and its product details.',
                      pin_title=candidate['card'].name,
                      pin_description=f'Explore {candidate["card"].name}. Check the product listing for current specifications, availability and price.',
                      hashtags=['#ProductDiscovery'],
                      discovery={'version': '3.0', 'research_queries': queries,
                                 'raw_results': sum(len(e.research_sources or []) for e in available),
                                 'normalized_candidates': len(candidates), 'candidates_attempted': len(attempts),
                                 'qualified_candidates': len(qualified), 'winner_evidence_count': len(candidate['sources']),
                                 'winner_source_url': candidate['card'].url, 'fallback_used': None,
                                 'score_kind': 'source breadth; not a demand or profit prediction',
                                 'market_signal_sources': candidate['market_signals'],
                                 'source_recency': 'Retrieved now; publication age and actual sales not independently verified',
                                 'history_window_days': 30, 'elapsed_seconds': round(time.monotonic()-started,3)})
        return winner
    raise HTTPException(404, 'No new product passed this research batch. '
                        f'Candidates: {len(candidates)}; checked: {len(attempts)}; '
                        f'photo rejections: {rejection_log.count("photo")}; '
                        f'product rejections: {rejection_log.count("product")}; '
                        f'incomplete or timed out: {max(0,len(attempts)-len(rejection_log)-len(qualified))}. '
                        'Previous products remain excluded; no saved-list result was substituted.')
