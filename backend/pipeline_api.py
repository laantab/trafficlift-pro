"""Owner-only pipeline configuration, campaign registry, media and analytics."""
import hashlib
import os
import re
import threading
import time
import uuid
from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from backend import pipeline as engine, pipeline_store as store, pipeline_providers as providers
from backend.photo_video_api import video_guard, storage
from backend.social_exports_api import _marketing_url
from backend.exact_product import amazon_asin

router = APIRouter(prefix='/api/v1/pipeline')
_stop = threading.Event()
_thread = None


def guard(request, write=False):
    video_guard(request)
    if write and request.headers.get('origin', '').rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Open the pipeline on the official TrafficLift website.')
    store.seal({})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Policy(StrictModel):
    enabled: StrictBool = False
    autonomous: StrictBool = False
    timezone: str = 'America/Los_Angeles'
    weekdays: list[int] = Field(default=[0, 2, 4], min_length=1, max_length=7)
    hour: int = Field(default=9, ge=0, le=23)
    weekly_limit: int = Field(default=3, ge=1, le=3)
    platforms: list[Literal['pinterest', 'youtube', 'tiktok']] = Field(default=['pinterest', 'youtube', 'tiktok'], min_length=1)
    research_enabled: StrictBool = False
    minimum_age_hours: int = Field(default=72, ge=72)
    minimum_impressions: int = Field(default=1000, ge=1000)
    minimum_clicks: int = Field(default=30, ge=30)

    @field_validator('timezone')
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Choose a valid IANA timezone.')
        return value

    @field_validator('weekdays')
    @classmethod
    def days(cls, value):
        if len(set(value)) != len(value) or any(d not in range(7) for d in value):
            raise ValueError('Choose distinct weekdays 0 through 6.')
        return value


class Evidence(StrictModel):
    url: str = Field(max_length=2048)
    observation: str = Field(min_length=10, max_length=1000)
    observed_at: float = Field(gt=0)

    @field_validator('url')
    @classmethod
    def url(cls, value):
        return _marketing_url(value)


class Campaign(StrictModel):
    title: str = Field(min_length=3, max_length=180)
    product_url: str = Field(max_length=2048)
    marketing_url: str = Field(max_length=2048)
    resolved_marketing_url: str = Field(max_length=2048)
    source_campaign_id: str = Field(default='', max_length=64)
    affiliate_eligible: Literal[True]
    rights_verified: Literal[True]
    exact_destination_verified: Literal[True]
    in_stock: Literal[True]
    verified_at: float = Field(gt=0)
    evidence: list[Evidence] = Field(min_length=1, max_length=20)

    @field_validator('title')
    @classmethod
    def plain_title(cls, value):
        if not value.strip() or any(c in '<>' or ord(c) < 32 for c in value):
            raise ValueError('Use a plain product name.')
        return value.strip()

    @field_validator('product_url', 'marketing_url', 'resolved_marketing_url')
    @classmethod
    def url(cls, value):
        return _marketing_url(value)


class Preparation(StrictModel):
    platform: Literal['pinterest', 'youtube', 'tiktok']
    asset_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    variant: int = Field(default=0, ge=0, le=1)
    approve: StrictBool = False


class Snapshot(StrictModel):
    source: Literal['pinterest', 'youtube', 'tiktok', 'amazon_shared']
    start: str = Field(min_length=1, max_length=40)
    end: date
    status: Literal['ready', 'processing', 'unavailable']
    observed_at: float = Field(gt=0)
    metrics: dict[str, float | None] = Field(default_factory=dict)

    @field_validator('metrics')
    @classmethod
    def measured(cls, value):
        allowed = {'views', 'impressions', 'outbound_clicks', 'pin_clicks', 'saves', 'likes', 'comments', 'shares', 'orders', 'commission'}
        import math
        if set(value) - allowed or any(v is not None and (not math.isfinite(v) or v < 0 or (k != 'commission' and int(v) != v)) for k, v in value.items()):
            raise ValueError('Use nonnegative finite metrics; counts must be integers.')
        return value


class HistoricalPost(StrictModel):
    platform: Literal['pinterest', 'youtube', 'tiktok']
    account: str = Field(min_length=1, max_length=128)
    asset_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    url: str = Field(max_length=2048)
    posted_at: float = Field(gt=0)
    confirmed_public: Literal[True]

    @field_validator('url')
    @classmethod
    def url(cls, value):
        return _marketing_url(value)


@router.get('/status')
def status(request: Request):
    guard(request)
    return JSONResponse({'policy': engine.policy(), 'capabilities': providers.capabilities(),
        'campaigns': [{**c, 'blockers': engine.evidence_reasons(c)} for c in store.records('campaign')],
        'recommendations': store.records('recommendation'), 'assets': store.records('asset'),
        'posts': [{**p, 'assessment': engine.assessment(p) if p['state'] == 'published' else None} for p in store.posts()],
        'metrics': store.records('metric'), 'notices': store.records('notice')[-20:]},
        headers={'Cache-Control': 'private, no-store'})


@router.put('/policy')
def policy(payload: Policy, request: Request):
    guard(request, True)
    return engine.configure(payload.model_dump())


@router.post('/campaigns')
def campaign(payload: Campaign, request: Request):
    guard(request, True)
    value = payload.model_dump()
    now = time.time()
    if not 0 <= now - value['verified_at'] <= 7 * 86400 or any(not 0 <= now - e['observed_at'] <= 7 * 86400 for e in value['evidence']):
        raise HTTPException(422, 'Use evidence and verification from the past seven days.')
    source, target = amazon_asin(value['product_url']), amazon_asin(value['resolved_marketing_url'])
    if source or target:
        from urllib.parse import urlsplit
        if not source or source != target or urlsplit(value['product_url']).hostname != urlsplit(value['resolved_marketing_url']).hostname:
            raise HTTPException(422, 'The resolved affiliate destination must match the exact product and marketplace.')
        key = urlsplit(value['product_url']).hostname + ':' + source
    else:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        from backend.winner_history import _canonical_query_pairs, _canonical_product_path
        def canonical(url):
            parsed = urlsplit(url)
            return urlunsplit(('https', parsed.netloc.lower(), _canonical_product_path(parsed.path),
                              urlencode(_canonical_query_pairs(parse_qsl(parsed.query))), ''))
        if canonical(value['product_url']) != canonical(value['resolved_marketing_url']):
            raise HTTPException(422, 'The resolved destination differs from the exact product.')
        key = canonical(value['product_url'])
    # Re-registering the same item refreshes evidence, not the product identity.
    identifier = hashlib.sha256(key.encode()).hexdigest()[:32]
    old = store.get('campaign', identifier)
    value.update(id=identifier, product_key=key, revision=(old or {}).get('revision', 0) + 1,
                 created_at=(old or {}).get('created_at', now))
    store.put('campaign', identifier, value)
    return value


@router.post('/campaigns/{identifier}/assets')
async def asset(identifier: str, request: Request, synthetic_media: bool = False):
    guard(request, True)
    if not store.get('campaign', identifier):
        raise HTTPException(404, 'Campaign not found.')
    mime = request.headers.get('content-type', '').split(';')[0]
    if mime not in {'image/jpeg', 'image/png', 'video/mp4'}:
        raise HTTPException(415, 'Upload an existing JPEG, PNG or MP4.')
    aid = uuid.uuid4().hex
    value = {'id': aid, 'campaign_id': identifier, 'kind': 'video' if mime == 'video/mp4' else 'image',
             'content_type': mime, 'synthetic_media': synthetic_media, 'created_at': time.time()}
    path = providers.asset_path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with path.open('wb') as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 60 * 1024 * 1024:
                    raise HTTPException(413, 'Existing assets must be at most 60 MB.')
                out.write(chunk)
        if value['kind'] == 'video':
            from backend.publishing_api import inspect_media
            value['technical'] = inspect_media(path)
        else:
            from PIL import Image
            with Image.open(path) as image:
                if image.format not in {'JPEG', 'PNG'} or image.width * image.height > 25_000_000:
                    raise HTTPException(422, 'Use a valid product image under 25 megapixels.')
                if (mime == 'image/jpeg') != (image.format == 'JPEG'):
                    raise HTTPException(422, 'Image type does not match uploaded content.')
                value['technical'] = {'width': image.width, 'height': image.height}
                image.verify()
        value['sha256'] = providers.asset_hash(value)
        store.put('asset', aid, value)
        return value
    except Exception as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, 'Existing asset could not be decoded. No post was queued.') from exc


@router.get('/assets/{identifier}')
def read_asset(identifier: str, request: Request):
    guard(request)
    value = store.get('asset', identifier)
    if not value:
        raise HTTPException(404, 'Asset not found.')
    return FileResponse(providers.asset_path(value), media_type=value['content_type'], headers={'Cache-Control': 'private, no-store'})


@router.post('/campaigns/{identifier}/package')
def package(identifier: str, payload: Preparation, request: Request):
    guard(request, True)
    campaign = store.get('campaign', identifier)
    source = store.get('asset', payload.asset_id)
    if not campaign or not source or source['campaign_id'] != identifier or source['kind'] != 'image':
        raise HTTPException(422, 'Select this campaign’s registered original product photo.')
    from backend.pipeline_creative import package as render_package
    return {'assets': render_package(campaign, source, payload.variant)}


@router.post('/campaigns/{identifier}/prepare')
def prepare(identifier: str, payload: Preparation, request: Request):
    guard(request, True)
    return engine.prepare(identifier, payload.platform, payload.asset_id, payload.variant, payload.approve)


@router.post('/campaigns/{identifier}/history')
def record_history(identifier: str, payload: HistoricalPost, request: Request):
    guard(request, True)
    campaign = store.get('campaign', identifier)
    if not campaign:
        raise HTTPException(404, 'Campaign not found.')
    from urllib.parse import urlsplit
    hosts = {'pinterest': {'www.pinterest.com', 'pinterest.com'}, 'youtube': {'www.youtube.com', 'youtube.com', 'youtu.be'},
             'tiktok': {'www.tiktok.com', 'tiktok.com'}}
    if urlsplit(payload.url).hostname not in hosts[payload.platform] or payload.posted_at > time.time() + 60:
        raise HTTPException(422, 'Use a nonfuture publication and a URL on the selected platform.')
    dedupe = hashlib.sha256(f'{payload.platform}:{payload.account}:{payload.asset_sha256}'.encode()).hexdigest()
    value = {'id': uuid.uuid4().hex, 'campaign_id': identifier, 'platform': payload.platform, 'account': payload.account,
             'asset_hash': payload.asset_sha256, 'url': payload.url, 'published_at': payload.posted_at,
             'created_at': time.time(), 'state': 'published_recorded', 'variant': 0,
             'source': 'owner-confirmed external publication', 'events': [],
             'message': 'Imported publication; no API verification claimed.'}
    with store.database() as db:
        row = db.execute('SELECT payload FROM posts WHERE dedupe=?', (dedupe,)).fetchone()
        if row:
            old = store.unseal(row['payload'])
            if old['state'] != 'needs_attention':
                return old
            old.update(state='published_recorded', url=payload.url, published_at=payload.posted_at,
                       source='owner-confirmed external publication', message='Owner reconciled remote history; no resend.')
            db.execute('UPDATE posts SET state=?,payload=? WHERE id=?', (old['state'], store.seal(old), old['id']))
            return old
        db.execute('INSERT INTO posts VALUES(?,?,?,?)', (value['id'], dedupe, value['state'], store.seal(value)))
    return value


@router.post('/posts/{identifier}/reconcile')
def reconcile(identifier: str, request: Request):
    guard(request, True)
    post = store.post(identifier)
    if not post or not post.get('remote_id'):
        raise HTTPException(409, 'No remote receipt is available. Inspect platform history and record the confirmed publication; do not resend.')
    post.update(**providers.reconcile(post))
    post['events'].append({'at': time.time(), 'state': post['state']})
    store.save_post(post)
    return post


@router.post('/research')
def research(request: Request):
    guard(request, True)
    if not engine.policy()['research_enabled']:
        raise HTTPException(409, 'Enable research in the owner policy first; research may use configured provider credits.')
    return {'recommendation_id': engine.research()}


@router.post('/posts/{identifier}/metrics')
def metric(identifier: str, payload: Snapshot, request: Request):
    guard(request, True)
    post = store.post(identifier)
    if not post:
        raise HTTPException(404, 'Post not found.')
    if payload.source == 'amazon_shared':
        raise HTTPException(422, 'Shared Amazon reports cannot be attributed to an individual post.')
    if payload.source != post['platform'] or payload.observed_at > time.time() + 60:
        raise HTTPException(422, 'Use this platform source and a nonfuture observation time.')
    value = payload.model_dump(mode='json')
    if value['status'] != 'ready':
        value['metrics'] = {k: None for k in value['metrics']}
    store.snapshot(identifier, value)
    return {'saved': True}


@router.post('/tick')
def run(request: Request):
    guard(request, True)
    engine.tick()
    return {'checked': True}


def loop():
    while not _stop.wait(30):
        try:
            engine.tick()
        except Exception:
            # Configuration/temporary outages do not stop the existing app.
            pass


@router.on_event('startup')
def start():
    global _thread
    if os.getenv('TRAFFICLIFT_PIPELINE_ENABLED') == '1' and (_thread is None or not _thread.is_alive()):
        _stop.clear()
        _thread = threading.Thread(target=loop, daemon=True, name='campaign-pipeline')
        _thread.start()


@router.on_event('shutdown')
def stop():
    _stop.set()
