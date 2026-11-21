"""Offline, isolated pipeline regression checks. No generation or platform spend."""
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import Mock
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import pipeline as engine, pipeline_store as store, pipeline_api as api, pipeline_providers as providers

HOST = 'https://pipeline.test'
SECRET = 'test-owner-' + 'x' * 45


@pytest.fixture
def setup(monkeypatch, tmp_path):
    for key, value in {'TRAFFICLIFT_HOSTED_VIDEO': '1', 'TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN': SECRET,
                       'TRAFFICLIFT_VIDEO_DIR': str(tmp_path), 'TRAFFICLIFT_PUBLISHING_KEY': Fernet.generate_key().decode()}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(providers, 'capabilities', lambda: {'pinterest': {'configured': True, 'account': 'owner'},
                                                         'youtube': {'configured': True, 'account': 'channel'},
                                                         'tiktok': {'configured': True, 'account': 'creator'}})
    monkeypatch.setattr(providers.requests, 'request', Mock(side_effect=AssertionError('Unexpected network call')))
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app, base_url=HOST, headers={'x-trafficlift-video-token': SECRET, 'origin': HOST})
    return client, tmp_path


def register(client, **changes):
    body = {'title': 'Exact silicone baking mat', 'product_url': 'https://www.amazon.com/dp/B0HLRWFBHF',
            'marketing_url': 'https://link.amazon/owner', 'resolved_marketing_url': 'https://www.amazon.com/dp/B0HLRWFBHF?tag=owner',
            'affiliate_eligible': True, 'rights_verified': True, 'exact_destination_verified': True,
            'in_stock': True, 'verified_at': time.time(),
            'evidence': [{'url': 'https://www.amazon.com/dp/B0HLRWFBHF', 'observation': 'Seller listed 100+ bought last month', 'observed_at': time.time()}], **changes}
    return client.post('/api/v1/pipeline/campaigns', json=body)


def candidate(setup):
    client, tmp = setup
    campaign = register(client).json()
    aid = 'a' * 32
    asset = {'id': aid, 'campaign_id': campaign['id'], 'kind': 'image', 'content_type': 'image/jpeg'}
    path = providers.asset_path(asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'existing verified media')
    asset['sha256'] = providers.asset_hash(asset)
    store.put('asset', aid, asset)
    return campaign, asset


def enable_due():
    value = engine.configure({**engine.DEFAULT_POLICY, 'enabled': True, 'autonomous': True})
    value['next_run'] = time.time() - 1
    store.put('policy', 'owner', value)
    return value


def test_owner_and_csrf(setup):
    client, _ = setup
    assert client.get('/api/v1/pipeline/status', headers={'x-trafficlift-video-token': ''}).status_code == 401
    assert client.put('/api/v1/pipeline/policy', json={}, headers={'origin': 'https://evil.test'}).status_code == 403
    assert client.get('/api/v1/pipeline/status').headers['cache-control'] == 'private, no-store'


@pytest.mark.parametrize('field,value', [('timezone', 'Not/AZone'), ('weekdays', [7]), ('weekdays', [0, 0]),
                                          ('weekly_limit', 4), ('minimum_age_hours', 1), ('enabled', 'true')])
def test_bad_policy(setup, field, value):
    assert setup[0].put('/api/v1/pipeline/policy', json={field: value}).status_code == 422


def test_dst_and_exact_slot():
    p = engine.DEFAULT_POLICY
    # Monday March 9 is daylight time, not a fixed UTC offset.
    after = datetime.fromisoformat('2026-03-07T22:00:00+00:00').timestamp()
    assert datetime.fromtimestamp(engine.next_slot(p, after), __import__('datetime').timezone.utc).isoformat() == '2026-03-09T16:00:00+00:00'


@pytest.mark.parametrize('changes', [{'resolved_marketing_url': 'https://www.amazon.com/dp/B0HQ84DS7S'},
    {'verified_at': time.time() - 9 * 86400}, {'verified_at': time.time() + 3600}, {'evidence': []},
    {'rights_verified': False}, {'product_url': 'http://127.0.0.1/private'}])
def test_bad_campaign(setup, changes):
    assert register(setup[0], **changes).status_code == 422


def test_campaign_identity_revision_and_encryption(setup):
    client, root = setup
    first = register(client).json()
    second = register(client).json()
    assert second['id'] == first['id'] and second['revision'] == 2
    assert b'Exact silicone baking mat' not in (root / 'pipeline' / 'pipeline.sqlite3').read_bytes()


def test_prepare_requires_same_asset_eligibility_and_account(setup):
    client, _ = setup
    campaign, asset = candidate(setup)
    assert client.post(f'/api/v1/pipeline/campaigns/{campaign["id"]}/prepare', json={'platform': 'pinterest', 'asset_id': asset['id']}).status_code == 409
    result = client.post(f'/api/v1/pipeline/campaigns/{campaign["id"]}/prepare', json={'platform': 'pinterest', 'asset_id': asset['id'], 'approve': True})
    assert result.status_code == 200
    assert result.json()['state'] == 'queued'
    assert engine.prepare(campaign['id'], 'pinterest', asset['id'], variant=1, approved=True)['id'] == result.json()['id']


def test_concurrent_workers_claim_only_once(setup):
    campaign, asset = candidate(setup)
    enable_due()
    post = engine.prepare(campaign['id'], 'pinterest', asset['id'])
    with ThreadPoolExecutor(max_workers=5) as pool:
        claims = list(pool.map(lambda _: engine.reserve(post['id']), range(5)))
    assert sum(p is not None for p in claims) == 1
    assert engine.reserve(post['id']) is None


def test_weekly_cap_counts_uncertain_attempts(setup):
    campaign, asset = candidate(setup)
    for i in range(3):
        value = {'id': str(i), 'state': 'needs_attention', 'attempted_at': time.time()}
        with store.database() as db:
            db.execute('INSERT INTO posts VALUES(?,?,?,?)', (str(i), str(i), value['state'], store.seal(value)))
    enable_due()
    p = engine.prepare(campaign['id'], 'pinterest', asset['id'])
    assert engine.reserve(p['id']) is None


def test_timeout_never_retries_remote_create(setup, monkeypatch):
    campaign, asset = candidate(setup)
    enable_due()
    p = engine.prepare(campaign['id'], 'pinterest', asset['id'])
    publisher = Mock(side_effect=TimeoutError())
    monkeypatch.setattr(providers, 'publish', publisher)
    engine.dispatch(engine.reserve(p['id']))
    assert store.post(p['id'])['state'] == 'needs_attention'
    engine.tick()
    assert publisher.call_count == 1


@pytest.mark.parametrize('change', ['asset', 'campaign', 'account'])
def test_changed_approved_inputs_stop_before_network(setup, monkeypatch, change):
    campaign, asset = candidate(setup)
    enable_due()
    p = engine.prepare(campaign['id'], 'pinterest', asset['id'])
    p = engine.reserve(p['id'])
    if change == 'asset':
        providers.asset_path(asset).write_bytes(b'changed')
    elif change == 'campaign':
        campaign['revision'] += 1
        store.put('campaign', campaign['id'], campaign)
    else:
        monkeypatch.setattr(providers, 'capabilities', lambda: {'pinterest': {'configured': True, 'account': 'other'}})
    publisher = Mock()
    monkeypatch.setattr(providers, 'publish', publisher)
    assert engine.dispatch(p)['state'] == 'needs_attention'
    publisher.assert_not_called()


def test_processing_is_unknown_and_shared_sales_rejected(setup):
    client, _ = setup
    campaign, asset = candidate(setup)
    p = engine.prepare(campaign['id'], 'pinterest', asset['id'], approved=True)
    body = {'source': 'pinterest', 'start': '2026-11-20', 'end': '2026-11-20', 'status': 'processing',
            'observed_at': time.time(), 'metrics': {'impressions': 0, 'outbound_clicks': 0}}
    assert client.post(f'/api/v1/pipeline/posts/{p["id"]}/metrics', json=body).status_code == 200
    assert store.records('metric')[0]['metrics'] == {'impressions': None, 'outbound_clicks': None}
    body['source'] = 'amazon_shared'
    assert client.post(f'/api/v1/pipeline/posts/{p["id"]}/metrics', json=body).status_code == 422


def test_strategy_waits_for_maturity_and_comparable_clicks(setup):
    now = time.time()
    p = {'id': 'test', 'published_at': now - 4 * 86400}
    base = {'status': 'ready', 'observed_at': now, 'metrics': {'impressions': 1500, 'outbound_clicks': 3}}
    assert engine.assessment(p, [base])['action'] == 'revise_hook'
    base['status'] = 'processing'
    assert engine.assessment(p, [base])['action'] == 'wait'
    base.update(status='ready', metrics={'views': 10000})
    assert engine.assessment(p, [base])['action'] == 'wait'
    p['published_at'] = now - 3600
    base['metrics'] = {'impressions': 1500, 'outbound_clicks': 3}
    assert engine.assessment(p, [base])['action'] == 'wait'


def test_pinterest_pending_metrics_not_zero(setup, monkeypatch):
    monkeypatch.setattr(providers, 'pin', lambda *a, **k: {'all': {'daily_metrics': [{'data_status': 'PROCESSING'}],
                                                            'summary_metrics': {'IMPRESSION': 0, 'OUTBOUND_CLICK': 0}}})
    m = providers.metrics({'platform': 'pinterest', 'remote_id': '123', 'published_at': time.time()})
    assert m['status'] == 'processing' and all(v is None for v in m['metrics'].values())


def test_youtube_private_is_not_published(setup, monkeypatch):
    monkeypatch.setattr(providers.youtube_store, 'job', lambda _: {'state': 'private', 'actual_visibility': 'private', 'video_id': 'abc12345678'})
    assert providers.reconcile({'platform': 'youtube', 'remote_id': 'test'})['state'] == 'needs_attention'


def test_tiktok_public_id_required(setup, monkeypatch):
    monkeypatch.setattr(providers, 'tik', lambda *a, **k: {'status': 'PUBLISH_COMPLETE'})
    assert providers.reconcile({'platform': 'tiktok', 'remote_id': 'test', 'account': 'owner'})['state'] == 'processing'


def test_real_existing_photo_package_without_provider_calls(setup):
    from PIL import Image
    from backend.pipeline_creative import package
    client, _ = setup
    campaign = register(client).json()
    import io
    buf = io.BytesIO()
    Image.new('RGB', (600, 400), 'red').save(buf, format='JPEG')
    r = client.post(f'/api/v1/pipeline/campaigns/{campaign["id"]}/assets', content=buf.getvalue(), headers={'content-type': 'image/jpeg'})
    assert r.status_code == 200
    assets = package(campaign, r.json())
    assert {a['kind'] for a in assets} == {'image', 'video'}
    assert next(a for a in assets if a['kind'] == 'video')['technical']['duration'] == pytest.approx(12, abs=.1)
    assert [a['id'] for a in package(campaign, r.json())] == [a['id'] for a in assets]


def test_recorded_manual_upload_blocks_repost(setup):
    client, _ = setup
    campaign, asset = candidate(setup)
    body = {'platform': 'pinterest', 'account': 'owner', 'asset_sha256': asset['sha256'],
            'url': 'https://www.pinterest.com/pin/123/', 'posted_at': time.time() - 100, 'confirmed_public': True}
    imported = client.post(f'/api/v1/pipeline/campaigns/{campaign["id"]}/history', json=body)
    assert imported.status_code == 200
    post = engine.prepare(campaign['id'], 'pinterest', asset['id'], approved=True)
    assert post['id'] == imported.json()['id'] and post['state'] == 'published_recorded'
    enable_due()
    assert engine.reserve(post['id']) is None


def test_interrupted_dispatch_becomes_attention_without_resend(setup, monkeypatch):
    campaign, asset = candidate(setup)
    enable_due()
    p = engine.prepare(campaign['id'], 'pinterest', asset['id'])
    p = engine.reserve(p['id'])
    p['attempted_at'] -= 1000
    store.save_post(p)
    publisher = Mock()
    monkeypatch.setattr(providers, 'publish', publisher)
    engine.tick()
    assert store.post(p['id'])['state'] == 'needs_attention'
    publisher.assert_not_called()


def test_successful_pinterest_create_and_remote_verification(setup, monkeypatch):
    campaign, asset = candidate(setup)
    enable_due()
    post = engine.reserve(engine.prepare(campaign['id'], 'pinterest', asset['id'])['id'])
    monkeypatch.setenv('PINTEREST_BOARD_ID', 'board')
    remote = {'id': '123', 'board_id': 'board', 'board_owner': {'username': 'owner'},
              'link': campaign['marketing_url'], 'title': post['snapshot']['title'], 'description': post['snapshot']['description']}
    calls = []
    def pin(method, path, **kwargs):
        calls.append((method, path))
        if path == 'user_account':
            return {'username': 'owner'}
        if path == 'boards/board':
            return {'id': 'board', 'owner': {'username': 'owner'}, 'privacy': 'PUBLIC'}
        return remote
    monkeypatch.setattr(providers, 'pin', pin)
    result = engine.dispatch(post)
    assert result['state'] == 'published'
    assert result['url'] == 'https://www.pinterest.com/pin/123/'
    assert calls.count(('POST', 'pins')) == 1


def test_tiktok_checks_real_creator_and_public_options_before_init(setup, monkeypatch):
    campaign, image = candidate(setup)
    video = {**image, 'id': 'b' * 32, 'kind': 'video', 'technical': {'duration': 12}}
    providers.asset_path(video).write_bytes(b'mp4')
    video['sha256'] = providers.asset_hash(video)
    store.put('asset', video['id'], video)
    p = engine.prepare(campaign['id'], 'tiktok', video['id'], approved=True)
    call = Mock(return_value={'creator_username': 'creator', 'privacy_level_options': ['SELF_ONLY']})
    monkeypatch.setattr(providers, 'tik', call)
    with pytest.raises(Exception):
        providers.publish(p, campaign, video)
    assert call.call_count == 1


def test_asset_source_must_match_campaign(setup):
    client, _ = setup
    campaign, asset = candidate(setup)
    other = register(client, product_url='https://www.amazon.com/dp/B0HQ84DS7S',
                     resolved_marketing_url='https://www.amazon.com/dp/B0HQ84DS7S').json()
    assert client.post(f'/api/v1/pipeline/campaigns/{other["id"]}/prepare',
                      json={'platform': 'pinterest', 'asset_id': asset['id'], 'approve': True}).status_code == 422
