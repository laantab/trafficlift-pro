"""Current studio integration regressions. Seller/provider responses are controlled."""
import json,time,concurrent.futures
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def studio(monkeypatch,tmp_path):
    monkeypatch.setenv('TRAFFICLIFT_FREE_VIDEO','1');monkeypatch.setenv('TRAFFICLIFT_VIDEO_DIR',str(tmp_path/'videos'));monkeypatch.setenv('TRAFFICLIFT_HISTORY_DB',str(tmp_path/'history.sqlite'))
    monkeypatch.setenv('OPENAI_API_KEY','');monkeypatch.setenv('MINIMAX_API_KEY','');monkeypatch.setenv('TAVILY_API_KEY','')
    import requests
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Unexpected external request')))
    import trafficlift_pro,backend.photo_video_api as api
    monkeypatch.setattr(api,'_validate_public_https_url',lambda url:None)
    return TestClient(trafficlift_pro.app),api,tmp_path


def test_health_and_studio(studio):
    client,api,_=studio
    assert client.get('/api/v1/health').status_code==200
    assert 'Create My Marketing Materials' in client.get('/studio').text
    assert 'createMarketingKit' in client.get('/studio-classic').text
    assert client.get('/api/v1/photo-videos',headers={'origin':'https://untrusted.example'}).status_code==403


def test_exact_product_and_redirect_rejection(studio,monkeypatch):
    client,api,_=studio
    import backend.exact_product as exact,backend.product_control_agent as control
    html='<span id="productTitle">Test Travel Bottle</span><input id="ASIN" value="B012345678"><img id="landingImage" src="https://example.com/bottle.jpg">'
    monkeypatch.setattr(exact,'_safe_get',lambda url,**kw:(url,html,None))
    monkeypatch.setattr(control,'_validate_image',lambda url,**kw:control.ImageCheckResult(ok=True,image_status='verified',image_url=url))
    response=client.get('/api/v1/analyze-product-url',params={'url':'https://amazon.com/dp/B012345678'})
    assert response.status_code==200,response.text
    assert response.json()['name']=='Test Travel Bottle';assert response.json()['image_url']=='https://example.com/bottle.jpg'
    monkeypatch.setattr(exact,'_safe_get',lambda url,**kw:('https://amazon.com/dp/B087654321',html,None))
    assert client.get('/api/v1/analyze-product-url',params={'url':'https://amazon.com/dp/B012345678'}).status_code==422
    assert client.get('/api/v1/analyze-product-url',params={'url':'audit report, not a URL'}).status_code==400


def test_wrong_seller_and_variant():
    from backend.exact_product import exact_listing
    from backend.product_facts import canonical,facts_from_sources
    html='<meta property="og:title" content="Correct Bottle"><script type="application/ld+json">'+json.dumps({'@type':'Product','name':'Wrong Bottle','url':'https://other.example/product','image':'https://other.example/wrong.jpg'})+'</script>'
    with pytest.raises(ValueError):exact_listing(html,'https://seller.example/product')
    assert canonical('https://seller.example/p?variant=red')!=canonical('https://seller.example/p?variant=blue')
    assert not facts_from_sources('Correct Bottle','https://seller.example/p',[{'url':'https://seller.example/p','content':'Includes a removable lid'}])


def test_job_download_history_archive_and_damage(studio,monkeypatch):
    client,api,tmp=studio
    from PIL import Image,ImageDraw
    def photo(url,directory):
        path=directory/'product.png';image=Image.new('RGB',(1000,1000),'white');ImageDraw.Draw(image).rectangle((200,100,800,900),fill='blue');image.save(path);return path
    def render(image,directory,*args,**kwargs):
        (directory/'video.mp4').write_bytes(b'test-encoded-video');(directory/'quality.json').write_text('{"duration":15}')
    monkeypatch.setattr(api,'dependencies',lambda:None);monkeypatch.setattr(api,'_download_product_image',photo);monkeypatch.setattr(api,'render_bounded',render)
    payload={'name':'Test Travel Bottle','product_url':'https://seller.example/bottle','image_url':'https://seller.example/photo.png','seconds':15,'fact_sources':[{'title':'Test Travel Bottle','url':'https://seller.example/bottle','content':'Includes a removable lid for easy filling.'}]}
    response=client.post('/api/v1/photo-videos',json=payload);assert response.status_code==202,response.text
    job=response.json()['video']['id']
    for _ in range(100):
        record=client.get('/api/v1/photo-videos/'+job).json()['video']
        if record['status'] in {'succeeded','failed'}:break
        time.sleep(.02)
    assert record['status']=='succeeded',record
    assert client.get(record['video_url']).content==b'test-encoded-video'
    damaged=api.storage()/('b'*32);damaged.mkdir();(damaged/'job.json').write_text('{}')
    history=client.get('/api/v1/photo-videos');assert history.status_code==200;assert history.json()['unavailable_records']==['b'*32]
    api.archive_completed(api.storage(),limit=2)
    assert (api.storage()/'archive'/job/'video.mp4').exists()
    assert client.get(record['video_url']).content==b'test-encoded-video'
    assert any(r['id']==job for r in client.get('/api/v1/photo-videos').json()['videos'])


def test_failed_render_releases_worker(studio,monkeypatch):
    client,api,tmp=studio
    monkeypatch.setattr(api,'dependencies',lambda:None)
    monkeypatch.setattr(api,'resolve_video_facts',lambda *a:{})
    monkeypatch.setattr(api,'_download_product_image',lambda *a:(_ for _ in ()).throw(ValueError('Photo unavailable')))
    payload={'name':'Bottle','product_url':'https://seller.example/b','image_url':'https://seller.example/p.jpg','seconds':15}
    response=client.post('/api/v1/photo-videos',json=payload);assert response.status_code==202
    for _ in range(100):
        if not api.lock.locked():break
        time.sleep(.02)
    assert not api.lock.locked()
    assert client.get('/api/v1/photo-videos/'+response.json()['video']['id']).json()['video']['status']=='failed'


def test_durable_atomic_exclusions(monkeypatch,tmp_path):
    from backend.winner_history import WinnerHistory
    monkeypatch.setenv('TRAFFICLIFT_HISTORY_DB',str(tmp_path/'h.sqlite'))
    h=WinnerHistory('test-browser-12345')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes=list(pool.map(lambda _:h.claim(['same-product']),range(20)))
    assert sum(outcomes)==1
    assert 'same-product' in WinnerHistory('test-browser-12345').seen()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        cursors=list(pool.map(lambda _:h.next_cursor(),range(20)))
    assert len(set(cursors))==20


@pytest.mark.parametrize('seconds',[15,30,60])
def test_scripts_fit_contract(seconds):
    from backend.sales_script import build_sales_plan
    plan=build_sales_plan('Travel Bottle','Includes a removable lid','https://seller.example/b',seconds)
    assert plan['review']['status']=='PASS'
    assert len(plan['captions'])==len(plan['phrases'])
    long_name='Brand Travel Bottle with Stainless Steel Construction and Removable Lid ' * 3
    assert len(build_sales_plan(long_name,'','https://seller.example/b',60)['product'])<=90


def configure_discovery(monkeypatch,*,photo=True,empty=False):
    import types
    from backend import discovery,discovery_engine,listing_photo,live_research,product_control_agent
    from backend.product_research import _build_url_candidate
    from backend.url_resolver import ResolvedProduct
    cards=[_build_url_candidate(ResolvedProduct(original_url='https://seller.example/old',final_url='https://seller.example/old',title='Old Travel Bottle')),_build_url_candidate(ResolvedProduct(original_url='https://seller.example/new',final_url='https://seller.example/new',title='New Travel Bottle'))]
    sources=[{'title':c.name,'url':c.url,'snippet':c.name+' includes a removable lid.'} for c in cards]
    env=types.SimpleNamespace(research_status='live',research_sources=[] if empty else sources,research_image_urls=[],research_provider='fixture',research_errors=[])
    monkeypatch.setattr(live_research,'research',lambda *a,**kw:env)
    monkeypatch.setattr(discovery,'_build_candidates_from_envelope',lambda *a,**kw:cards)
    monkeypatch.setattr(discovery,'_find_image_for_candidate',lambda *a,**kw:'https://seller.example/photo.jpg' if photo else None)
    monkeypatch.setattr(listing_photo,'listing_images',lambda *a,**kw:[])
    monkeypatch.setattr(product_control_agent,'_validate_image',lambda url,**kw:product_control_agent.ImageCheckResult(ok=photo,image_status='verified' if photo else 'missing',image_url=url))
    return discovery_engine,cards
