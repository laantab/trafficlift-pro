"""No network/provider spend: exercise real discovery with controlled research."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from backend import discovery_engine as engine
from backend.live_research import ResearchEnvelope
from backend.winner_history import WinnerHistory, identity_keys, product_id


def envelope(names=('Orbit Desk Lamp', 'Atlas Travel Mug', 'Cedar Water Bottle')):
    return ResearchEnvelope(research_status='live', research_timestamp='2026-10-03T00:00:00Z',
        research_provider='test', research_query='product research',
        research_sources=[{'title': name, 'snippet': f'{name} product reviews and best sellers',
                           'url': f'https://seller{i}.example/products/{i}', 'provider':'test'}
                          for i,name in enumerate(names)], research_image_urls=['https://images.example/product.jpg'])


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr('backend.listing_photo.listing_images',lambda *a,**k:[])
    monkeypatch.setenv('TRAFFICLIFT_DISCOVERY_DB', str(tmp_path/'history.sqlite3'))
    monkeypatch.setattr(engine.live_research,'research',lambda *a,**k: envelope())
    monkeypatch.setattr('backend.discovery._find_image_for_candidate',lambda *a,**k:'https://images.example/product.jpg')
    monkeypatch.setattr('backend.product_control_agent.ProductControlAgent.evaluate',lambda payload:SimpleNamespace(ok=True,product=payload))

def test_listing_photo_works_when_image_search_returns_nothing(isolated,monkeypatch):
    monkeypatch.setattr('backend.listing_photo.listing_images',lambda *a,**k:['https://seller.example/hero.jpg'])
    monkeypatch.setattr('backend.product_control_agent._validate_image',lambda *a,**k:SimpleNamespace(ok=True))
    monkeypatch.setattr('backend.discovery._find_image_for_candidate',lambda *a,**k:pytest.fail('Image search unnecessary'))
    winner=engine.discover(client_id='browser123456789',seed=4)
    assert winner['image_url']=='https://seller.example/hero.jpg'
    assert winner['image_origin']=='matched_product_listing'


def test_identity_stable_across_processes_and_tracking_parameters():
    name='Stanley Quencher 30 oz'
    assert identity_keys(name,'https://www.shop.example/p/mug/?utm_source=x&tag=y') == identity_keys(name,'https://shop.example/p/mug')
    code="from backend.winner_history import product_id; print(product_id('Stanley Quencher 30 oz','https://shop.example/p/mug'))"
    assert subprocess.check_output([sys.executable,'-c',code],text=True).strip()==product_id(name,'https://shop.example/p/mug')


def test_history_survives_reopening_and_is_browser_scoped(tmp_path):
    path=tmp_path/'history.sqlite3';keys=identity_keys('Desk Lamp','https://shop.example/lamp')
    assert WinnerHistory('browser-a',path).claim(keys)
    assert set(keys)<=WinnerHistory('browser-a',path).seen()
    assert not WinnerHistory('browser-a',path).claim(keys)
    assert WinnerHistory('browser-b',path).claim(keys)


def test_concurrent_claims_cannot_return_same_product(tmp_path):
    path=tmp_path/'history.sqlite3';history=WinnerHistory('same-browser',path)
    keys=identity_keys('Desk Lamp','https://shop.example/lamp')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:WinnerHistory('same-browser',path).claim(keys),range(4)))
    assert results.count(True)==1


def test_rotation_covers_concrete_product_types():
    plans=[engine.query_plan(i,seed=7) for i in range(4)]
    assert len({q for batch in plans for q in batch})==12
    for category in engine.PRODUCT_QUERIES:
        assert any(q.startswith(category+' ') for batch in plans for q in batch)
    assert engine.query_plan(0,seed=7)==engine.query_plan(0,seed=7)


def test_clicks_do_not_repeat_after_reopening_history(isolated):
    winners=[engine.discover(client_id='browser123456789',seed=4) for _ in range(3)]
    assert len({w['id'] for w in winners})==3
    with pytest.raises(HTTPException) as exc:engine.discover(client_id='browser123456789',seed=4)
    assert exc.value.status_code==404
    assert 'Previous products remain excluded' in exc.value.detail


def test_failed_first_photo_does_not_force_fallback(isolated,monkeypatch):
    tried=[]
    def image(card,**kwargs):
        tried.append(card.name)
        return None if card.name=='Orbit Desk Lamp' else 'https://images.example/product.jpg'
    monkeypatch.setattr('backend.discovery._find_image_for_candidate',image)
    winner=engine.discover(client_id='browser123456789',seed=4)
    assert len(tried)==3
    assert winner['name']!='Orbit Desk Lamp'
    assert winner['discovery']['fallback_used'] is None


def test_external_failure_never_substitutes_saved_pool(isolated,monkeypatch):
    monkeypatch.setattr(engine.live_research,'research',lambda *a,**k:None)
    with patch('backend.discovery._pick_curated_winner') as saved:
        with pytest.raises(HTTPException) as exc:engine.discover(client_id='browser123456789')
        assert exc.value.status_code==503
        saved.assert_not_called()


def test_exclusions_apply_before_image_work(isolated,monkeypatch):
    excluded=product_id('Orbit Desk Lamp','https://seller0.example/products/0')
    tried=[]
    monkeypatch.setattr('backend.discovery._find_image_for_candidate',lambda card,**kw:tried.append(card.name) or 'https://images.example/product.jpg')
    winner=engine.discover(exclude_ids={excluded},seed=4)
    assert 'Orbit Desk Lamp' not in tried
    assert winner['id']!=excluded


def test_broad_query_sources_are_not_product_evidence():
    env=envelope(('Unrelated Travel Mug',))
    assert engine.matching_sources('Orbit Desk Lamp',[env])==[]


def test_exact_evidence_and_unknown_profit_reported(isolated):
    winner=engine.discover(client_id='browser123456789',seed=4)
    assert len(winner['research_sources'])==1
    assert winner['margin_estimate']=='Not established'
    assert winner['competition']=='Not established'
    assert winner['trend_score']==12
    assert 'not a demand or profit prediction' in winner['discovery']['score_kind']


def test_no_photo_produces_actionable_failure(isolated,monkeypatch):
    monkeypatch.setattr('backend.discovery._find_image_for_candidate',lambda *a,**kw:None)
    with pytest.raises(HTTPException) as exc:engine.discover(client_id='browser123456789')
    assert exc.value.status_code==404


def test_local_browser_history_contract():
    html=(Path(__file__).parents[2]/'index.html').read_text()
    assert "params.set('client_id', discoveryClientId())" in html
    assert "params.set('exclude_keys', keys.join(','))" in html
    assert 'rememberDiscoveredWinner(winner)' in html
    assert 'const params = discoveryParams();' in html


def test_twenty_clicks_return_twenty_new_products(isolated,monkeypatch):
    def research(query,**kwargs):
        names=tuple(f'Brand{hashlib.sha256(query.encode()).hexdigest()[:6]} Desk Lamp Model {i}' for i in range(3))
        env=envelope(names)
        # Seller destinations must be different products across research batches.
        for source in env.research_sources:source['url']='https://shop.example/products/'+hashlib.sha256(source['title'].encode()).hexdigest()[:16]
        return env
    monkeypatch.setattr(engine.live_research,'research',research)
    # Current category rotation repeats every four batches. Unique variants
    # and exclusions yield new picks until all eligible products are exhausted.
    winners=[]
    for i in range(20):
        try:winners.append(engine.discover(client_id='browser123456789',seed=i))
        except HTTPException as exc:
            assert exc.status_code==404
    assert len(winners)==20
    assert len({w['id'] for w in winners})==20


def test_price_or_product_presence_alone_is_not_market_signal(isolated,monkeypatch):
    env=envelope()
    for source in env.research_sources:source['snippet']=source['title']+' comes in blue'
    monkeypatch.setattr(engine.live_research,'research',lambda *a,**kw:env)
    with pytest.raises(HTTPException) as exc:engine.discover(client_id='browser123456789')
    assert exc.value.status_code==404


def test_route_forwards_history_and_rejects_invalid_browser_id(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.product_scout import router
    captured={}
    def fake(**kwargs):captured.update(kwargs);return {'id':'test'}
    monkeypatch.setattr('backend.discovery.discover_winner',fake)
    app=FastAPI();app.include_router(router)
    with TestClient(app) as client:
        assert client.get('/api/v1/discover-winner?client_id=x').status_code==422
        response=client.get('/api/v1/discover-winner',params={'client_id':'browser123456789','exclude':'one,two','exclude_keys':'name-one,url-one','seed':4})
        assert response.status_code==200
        assert captured['exclude_ids']=={'one','two'}
        assert captured['exclude_keys']=={'name-one','url-one'}
        assert captured['client_id']=='browser123456789'


def test_simultaneous_discovery_requests_never_return_duplicates(isolated):
    def request(_):
        try:return engine.discover(client_id='browser123456789',seed=4)['id']
        except HTTPException as exc:
            assert exc.status_code==404
            return None
    with ThreadPoolExecutor(max_workers=5) as pool:
        results=[r for r in pool.map(request,range(5)) if r]
    assert len(results)==3
    assert len(set(results))==3


def test_history_expiration_allows_new_cycle(tmp_path,monkeypatch):
    from backend import winner_history
    monkeypatch.setattr(winner_history.time,'time',lambda:100)
    history=WinnerHistory('browser',tmp_path/'history.sqlite3')
    keys=identity_keys('Desk Lamp','https://shop.example/lamp')
    assert history.claim(keys)
    monkeypatch.setattr(winner_history.time,'time',lambda:100+winner_history.WINDOW_SECONDS+1)
    assert history.claim(keys)


def test_deadline_does_not_wait_for_slow_background_work():
    import threading
    import time
    release=threading.Event()
    started=time.monotonic()
    try:
        results=engine.bounded_map(lambda _:release.wait(.5),[1],.02)
        assert results==[]
        assert time.monotonic()-started < .3
    finally:release.set()


def test_exact_listing_photo_with_opaque_cdn_url_is_not_keyword_rejected(isolated,monkeypatch):
    url='https://cdn.example/847234.jpg'
    monkeypatch.setattr('backend.listing_photo.listing_images',lambda *a,**k:[url])
    monkeypatch.setattr('backend.product_control_agent._validate_image',lambda *a,**k:SimpleNamespace(ok=True))
    monkeypatch.setattr('backend.discovery._find_image_for_candidate',lambda *a,**k:pytest.fail('Exact listing photo must be used'))
    winner=engine.discover(client_id='browser123456789',seed=4)
    assert winner['image_url']==url
    assert winner['image_origin']=='matched_product_listing'


def test_checks_candidates_beyond_first_six(isolated,monkeypatch):
    names=tuple(f'Brand{i} Desk Lamp' for i in range(8))
    monkeypatch.setattr(engine.live_research,'research',lambda *a,**k:envelope(names))
    tried=[]
    def photo(card,**kwargs):
        tried.append(card.name)
        return 'https://images.example/product.jpg' if len(tried)>6 else None
    monkeypatch.setattr('backend.discovery._find_image_for_candidate',photo)
    winner=engine.discover(client_id='browser123456789',seed=4)
    assert winner['discovery']['candidates_attempted']==8
    assert len(tried)==8
    assert winner['image_status']=='verified'


def test_one_click_continues_to_next_empty_free_batch(monkeypatch):
    monkeypatch.setattr(engine.live_research,'_is_tavily_configured',lambda:False)
    attempts=[]
    def batch(**kwargs):
        attempts.append(kwargs)
        if len(attempts)<3: raise HTTPException(404,'No concrete product')
        return {'id':'winner','discovery':{}}
    monkeypatch.setattr(engine,'_discover_batch',batch)
    winner=engine.discover(client_id='browser123456789',exclude_ids=['old'])
    assert winner['discovery']['research_batches']==3
    assert len({attempt['request_deadline'] for attempt in attempts})==1
    assert all(attempt['exclude_ids']==['old'] for attempt in attempts)


def test_auto_batches_do_not_multiply_paid_calls(monkeypatch):
    monkeypatch.setattr(engine.live_research,'_is_tavily_configured',lambda:True)
    attempts=[]
    def batch(**kwargs):
        attempts.append(kwargs);raise HTTPException(404,'No concrete product')
    monkeypatch.setattr(engine,'_discover_batch',batch)
    with pytest.raises(HTTPException):engine.discover()
    assert len(attempts)==1


def test_auto_batches_stop_on_provider_failure(monkeypatch):
    monkeypatch.setattr(engine.live_research,'_is_tavily_configured',lambda:False)
    attempts=[]
    def batch(**kwargs):
        attempts.append(kwargs);raise HTTPException(503,'Provider unavailable')
    monkeypatch.setattr(engine,'_discover_batch',batch)
    with pytest.raises(HTTPException) as error:engine.discover()
    assert error.value.status_code==503
    assert len(attempts)==1


def test_auto_batches_respect_shared_time_limit(monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setattr(engine.live_research,'_is_tavily_configured',lambda:False)
    monkeypatch.setattr(engine.time,'monotonic',Mock(side_effect=[0,0,61]))
    batch=Mock(side_effect=HTTPException(404,'No concrete product'))
    monkeypatch.setattr(engine,'_discover_batch',batch)
    with pytest.raises(HTTPException) as error:engine.discover()
    assert batch.call_count==1
    assert 'after 1 research batch' in error.value.detail
