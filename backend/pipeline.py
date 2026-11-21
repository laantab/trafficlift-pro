"""Research -> eligible campaign -> existing asset -> durable posting -> measured revision.

No generation provider is called. An opted-in policy may select eligible campaigns,
but cannot invent product evidence, affiliate eligibility, asset rights or account access.
"""
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from backend import pipeline_store as store

DEFAULT_POLICY = {'enabled': False, 'autonomous': False, 'timezone': 'America/Los_Angeles',
                  'weekdays': [0, 2, 4], 'hour': 9, 'weekly_limit': 3,
                  'platforms': ['pinterest', 'youtube', 'tiktok'], 'research_enabled': False,
                  'minimum_age_hours': 72, 'minimum_impressions': 1000, 'minimum_clicks': 30,
                  'next_run': 0, 'revision': 0}


def policy():
    return {**DEFAULT_POLICY, **(store.get('policy', 'owner') or {})}


def next_slot(value, after):
    zone = ZoneInfo(value['timezone'])
    current = datetime.fromtimestamp(after, zone)
    for offset in range(8):
        day = current.date() + timedelta(days=offset)
        candidate = datetime(day.year, day.month, day.day, value['hour'], tzinfo=zone)
        if candidate.weekday() in value['weekdays'] and candidate.timestamp() > after:
            return candidate.timestamp()
    raise ValueError('No posting day selected')


def configure(value):
    with store.database() as db:
        row = db.execute("SELECT payload FROM entities WHERE kind='policy' AND id='owner'").fetchone()
        old = store.unseal(row['payload']) if row else DEFAULT_POLICY
        value = {**DEFAULT_POLICY, **value, 'revision': old.get('revision', 0) + 1}
        value['next_run'] = next_slot(value, time.time())
        store.write(db, 'policy', 'owner', value)
    return value


def evidence_reasons(campaign, now=None):
    now = time.time() if now is None else now
    reasons = []
    if not campaign.get('affiliate_eligible'):
        reasons.append('Confirm commission eligibility for the exact marketing destination.')
    if not campaign.get('rights_verified'):
        reasons.append('Confirm rights and exact product identity for existing assets.')
    if not campaign.get('in_stock'):
        reasons.append('Product availability is not verified.')
    if not campaign.get('evidence'):
        reasons.append('No dated market evidence; a discovery score alone is not demand evidence.')
    elif any(not 0 <= now - e.get('observed_at', 0) <= 7 * 86400 for e in campaign['evidence']):
        reasons.append('Refresh the dated market evidence (maximum seven days).')
    if not 0 <= now - campaign.get('verified_at', 0) <= 7 * 86400:
        reasons.append('Refresh availability and product evidence (maximum seven days).')
    return reasons


def copy_for(campaign, variant=0):
    title = campaign['title'].strip()[:70]
    hooks = [f'A closer look at {title}', f'Will {title} fit your setup?']
    description = ('#ad ' + hooks[variant % len(hooks)] + '. Check the seller listing for exact size, '
                   'care, options and delivery before ordering. Affiliate product reference: ' +
                   campaign['marketing_url'] + '\nI may earn a commission from qualifying purchases.')
    return {'title': hooks[variant % len(hooks)][:100], 'description': description}


def prepare(campaign_id, platform, asset_id, variant=0, approved=False):
    campaign = store.get('campaign', campaign_id)
    asset = store.get('asset', asset_id)
    if not campaign or not asset or asset['campaign_id'] != campaign_id:
        raise HTTPException(422, 'Select an existing asset for this exact campaign.')
    from backend.pipeline_providers import capabilities
    capability = capabilities().get(platform, {})
    if not capability.get('configured'):
        raise HTTPException(409, capability.get('reason', 'Platform is not configured.'))
    expected = 'image' if platform == 'pinterest' else 'video'
    if asset['kind'] != expected:
        raise HTTPException(422, f'{platform} requires an existing {expected} asset.')
    reasons = evidence_reasons(campaign)
    if reasons:
        raise HTTPException(409, ' '.join(reasons))
    current = policy()
    if not approved and not current['autonomous']:
        raise HTTPException(409, 'Approve the campaign or enable autonomous policy.')
    account = capability['account']
    dedupe = hashlib.sha256(f'{platform}:{account}:{asset["sha256"]}'.encode()).hexdigest()
    value = {'id': uuid.uuid4().hex, 'campaign_id': campaign_id, 'platform': platform,
             'account': account, 'asset_id': asset_id, 'asset_hash': asset['sha256'],
             'campaign_revision': campaign['revision'], 'variant': variant,
             'snapshot': {**copy_for(campaign, variant), 'marketing_url': campaign['marketing_url'],
                          'product_url': campaign['product_url'], 'product_key': campaign['product_key'],
                          'synthetic_media': asset.get('synthetic_media', False)},
             'state': 'queued', 'created_at': time.time(), 'events': [], 'approved': approved,
             'policy_revision': current['revision']}
    with store.database() as db:
        old = db.execute('SELECT payload FROM posts WHERE dedupe=?', (dedupe,)).fetchone()
        if old:
            return store.unseal(old['payload'])
        db.execute('INSERT INTO posts VALUES(?,?,?,?)', (value['id'], dedupe, 'queued', store.seal(value)))
    return value


def reserve(identifier, now=None):
    """Atomically reserve a weekly slot before any remote side effect.

    An abandoned dispatch is deliberately not automatically retried: an API may
    have accepted a post even when the client never received its response.
    """
    now = time.time() if now is None else now
    with store.database() as db:
        policy_row = db.execute("SELECT payload FROM entities WHERE kind='policy' AND id='owner'").fetchone()
        value = {**DEFAULT_POLICY, **(store.unseal(policy_row['payload']) if policy_row else {})}
        row = db.execute('SELECT payload FROM posts WHERE id=?', (identifier,)).fetchone()
        if not row or not value['enabled'] or value['next_run'] > now:
            return None
        post = store.unseal(row['payload'])
        if post['state'] != 'queued' or (not post['approved'] and not value['autonomous']):
            return None
        if post['platform'] not in value['platforms']:
            return None
        local = datetime.fromtimestamp(now, ZoneInfo(value['timezone']))
        monday = local.date() - timedelta(days=local.weekday())
        start = datetime(monday.year, monday.month, monday.day, tzinfo=local.tzinfo).timestamp()
        rows = db.execute('SELECT payload FROM posts').fetchall()
        used = sum(1 for r in rows if store.unseal(r['payload']).get('attempted_at', 0) >= start)
        if used >= value['weekly_limit']:
            return None
        post.update(state='dispatching', attempted_at=now)
        value['next_run'] = next_slot(value, now)
        db.execute('UPDATE posts SET state=?,payload=? WHERE id=?', ('dispatching', store.seal(post), identifier))
        store.write(db, 'policy', 'owner', value)
    return post


def dispatch(post):
    from backend import pipeline_providers as providers
    campaign = store.get('campaign', post['campaign_id'])
    asset = store.get('asset', post['asset_id'])
    try:
        if not campaign or campaign['revision'] != post['campaign_revision'] or evidence_reasons(campaign):
            raise HTTPException(409, 'Campaign changed or verification expired; prepare a new review.')
        if not asset or providers.asset_hash(asset) != post['asset_hash']:
            raise HTTPException(409, 'Existing asset bytes changed; prepare a new review.')
        capability = providers.capabilities()[post['platform']]
        if capability.get('account') != post['account']:
            raise HTTPException(409, 'Publishing account changed; prepare a new review.')
        receipt = providers.publish(post, campaign, asset)
        post.update(**receipt)
    except Exception:
        # No automatic resend after a remote timeout. Secrets and provider bodies
        # never become public error messages or logs.
        post.update(state='needs_attention', message='Posting stopped. Reconcile remote history before retrying.')
    post['events'].append({'at': time.time(), 'state': post['state']})
    store.save_post(post)
    return post


def assessment(post, snapshots=None, now=None):
    """Suggest a new version only on ready, sufficiently mature, comparable data."""
    value = policy()
    now = time.time() if now is None else now
    if now - post.get('published_at', now) < value['minimum_age_hours'] * 3600:
        return {'action': 'wait', 'reason': 'Allow at least 72 hours before revising.'}
    candidates = snapshots if snapshots is not None else [s for s in store.records('metric') if s['post_id'] == post['id']]
    ready = [s for s in candidates if s['status'] == 'ready' and s.get('metrics')]
    if not ready:
        return {'action': 'wait', 'reason': 'Analytics are unavailable or still processing.'}
    latest = max(ready, key=lambda s: s['observed_at'])
    metrics = latest['metrics']
    impressions, clicks = metrics.get('impressions'), metrics.get('outbound_clicks')
    if impressions is not None and impressions >= value['minimum_impressions'] and clicks is not None:
        if clicks / impressions < .01:
            return {'action': 'revise_hook', 'reason': 'Low outbound click rate on a mature reporting window.',
                    'baseline': latest, 'requires_new_asset': True}
    if clicks is not None and clicks >= value['minimum_clicks']:
        return {'action': 'check_destination', 'reason': 'Inspect exact offer, delivery and attributed sales before more creative.'}
    return {'action': 'wait', 'reason': 'Insufficient comparable click data; views alone do not establish conversion.'}


def research():
    """Run existing discovery; persist provenance without calling text/video generation."""
    from backend.discovery import discover_winner
    winner = discover_winner(client_id='owner:pipeline:v1')
    identifier = uuid.uuid4().hex
    store.put('recommendation', identifier, {'id': identifier, 'at': time.time(), 'winner': winner,
               'state': 'needs_eligibility', 'message': 'Supply verified exact-product link eligibility and reusable assets.'})
    return identifier


def tick():
    from backend import pipeline_providers as providers
    value = policy()
    # Resume receipts only. Never replay an interrupted remote create.
    for post in store.posts():
        if post['state'] == 'dispatching' and time.time() - post.get('attempted_at', 0) > 900:
            post.update(state='needs_attention', message='Worker interrupted during dispatch. Reconcile remote history; do not resend.')
            store.save_post(post)
        if post['state'] in {'submitted', 'processing'} and post.get('next_poll', 0) <= time.time():
            try:
                post.update(**providers.reconcile(post))
                post['next_poll'] = time.time() + 300
                store.save_post(post)
            except Exception:
                post['next_poll'] = time.time() + 900
                store.save_post(post)
        if post['state'] == 'published' and time.time() - post.get('last_metrics', 0) >= 21600:
            try:
                metric = providers.metrics(post)
                store.snapshot(post['id'], metric)
                post['last_metrics'] = time.time()
                store.save_post(post)
            except Exception:
                pass
    if not value['enabled'] or value['next_run'] > time.time():
        return
    for post in reversed(store.posts()):
        claimed = reserve(post['id'])
        if claimed:
            dispatch(claimed)
            return
    if not value['autonomous']:
        return
    # Existing eligible campaigns and registered media are preferred to new discovery.
    for campaign in store.records('campaign'):
        if evidence_reasons(campaign):
            continue
        for platform in value['platforms']:
            if not providers.capabilities().get(platform, {}).get('configured'):
                continue
            prior = [p for p in store.posts() if p['campaign_id'] == campaign['id'] and p['platform'] == platform]
            variant = 0
            if prior:
                # One measured A/B revision only, never blind perpetual reposts.
                latest = max(prior, key=lambda p: p['created_at'])
                if latest['state'] != 'published' or latest.get('variant', 0) >= 1 or assessment(latest)['action'] != 'revise_hook':
                    continue
                variant = 1
            assets = store.records('asset')
            sources = [a for a in assets if a['campaign_id'] == campaign['id'] and a['kind'] == 'image' and not a.get('source_asset_id')]
            if sources:
                from backend.pipeline_creative import package
                assets = package(campaign, sources[0], variant)
            for asset in assets:
                if asset['campaign_id'] != campaign['id']:
                    continue
                try:
                    post = prepare(campaign['id'], platform, asset['id'], variant=variant)
                    claimed = reserve(post['id'])
                    if claimed:
                        dispatch(claimed)
                        return
                except HTTPException:
                    continue
    if value['research_enabled']:
        # Claim the discovery slot before network work, across multiple processes.
        with store.database() as db:
            row = db.execute("SELECT payload FROM entities WHERE kind='policy' AND id='owner'").fetchone()
            current = store.unseal(row['payload'])
            if current['next_run'] > time.time():
                return
            current['next_run'] = next_slot(current, time.time())
            store.write(db, 'policy', 'owner', current)
        try:
            research()
        except Exception:
            store.put('notice', uuid.uuid4().hex, {'at': time.time(), 'message': 'Research could not finish; no substitute was published.'})
