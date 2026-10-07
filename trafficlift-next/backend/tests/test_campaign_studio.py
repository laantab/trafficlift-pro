import io,json,zipfile,time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from PIL import Image,ImageDraw
from backend import campaign_store as store,campaign_studio as studio
from backend.platform_kit import CHANNELS,render_images
from trafficlift_pro import app
@pytest.fixture
def client(tmp_path,monkeypatch):
 for k,v in {'TRAFFICLIFT_FREE_VIDEO':'1','TRAFFICLIFT_WORKSPACE_DB':str(tmp_path/'work.db'),'TRAFFICLIFT_VIDEO_DIR':str(tmp_path/'videos'),'TAVILY_API_KEY':'','OPENAI_API_KEY':'','MINIMAX_API_KEY':''}.items():monkeypatch.setenv(k,v)
 studio._active.clear()
 with TestClient(app) as c:yield c

def profile(c,mode='own_store',url='https://tryme.gumroad.com'):
 return c.put('/api/v2/settings',json=dict(brand='TryMe',mode=mode,store_url=url,research_domains=['amazon.com'],channels=CHANNELS)).json()['profile']
def product():return dict(name='Example travel tumbler',url='https://amazon.com/dp/B012345678',image_url='https://images.example.com/photo.jpg',source='discovery',category='Kitchen',research_sources=[dict(title='Example travel tumbler',snippet='Seller reports reviews',url='https://amazon.com/dp/B012345678')])
def campaign(c):return studio.new_campaign(product(),profile(c))
def dest(c,data,url='https://tryme.gumroad.com/l/tumbler'):
 return c.put('/api/v2/campaigns/'+data['id']+'/destination',json={'url':url,'same_product':True})
def ready(c,data):
 data=dest(c,data).json()['campaign'];d=studio.campaign_dir(data['id']);d.mkdir(parents=True,exist_ok=True)
 p=d/'source.png';im=Image.new('RGB',(800,1200),'white');ImageDraw.Draw(im).rectangle((100,50,700,1150),fill='teal');im.save(p);render_images(p,d,data['product']['name'],'TryMe')
 jid='f'*32;v=studio.storage()/jid;v.mkdir(parents=True,exist_ok=True);(v/'video.mp4').write_bytes(b'video-fixture');(v/'job.json').write_text(json.dumps(dict(id=jid,name='Tumbler',created_at=time.time(),status='succeeded',video_url=f'/api/v1/photo-videos/{jid}/video')))
 return store.update(data['id'],status='ready',video_id=jid,video_url=f'/api/v1/photo-videos/{jid}/video',script={'phrases':['Product','See our profile']})
def test_setup_and_connection_truth(client):
 assert client.post('/api/v2/discover',json={'client_id':'c'*32}).status_code==503
 assert all(not v['connected'] for v in client.get('/api/v2/workspace').json()['publishing'].values())
 assert 'SIMPLE STUDIO' in client.get('/studio').text
 assert client.get('/studio-classic').status_code==200
@pytest.mark.parametrize('url',['http://store.com','https://127.0.0.1/product','https://localhost/product','https://store.com:8000/product','https://user:pass@store.com/product','javascript:alert(1)'])
def test_invalid_store(client,url):
 assert client.put('/api/v2/settings',json=dict(brand='Shop',store_url=url,research_domains=['amazon.com'])).status_code==422

def test_research_domains_and_signal_gate(client,monkeypatch):
 profile(client)
 def fake(**kw):
  assert 'allowed_domains' not in kw and kw['require_signals'] is True and kw['broad_market'] is True
  return product()
 monkeypatch.setattr('backend.discovery_engine.discover',fake)
 data=client.post('/api/v2/discover',json={'client_id':'c'*32}).json()['campaign'];assert not data['destination_url']
 # Imports accept any validated exact listing, without research filters.
 p=product();p['url']='https://target.com/p/tumbler'
 with pytest.raises(HTTPException):studio.new_campaign(p,store.settings())
def test_destination_and_channel_copy(client):
 data=campaign(client)
 assert dest(client,data,'https://target.com/p/tumbler').status_code==422
 assert dest(client,data,'https://tryme.gumroad.com/').status_code==422
 assert client.put('/api/v2/campaigns/'+data['id']+'/destination',json={'url':'https://tryme.gumroad.com/l/tumbler'}).status_code==422
 data=dest(client,data).json()['campaign'];assert 'utm_source=pinterest' in data['posts']['pinterest']['destination_url']
 assert 'profile' in data['posts']['youtube']['caption'] and 'not clickable' in data['posts']['youtube']['instructions']
 assert not data['posts']['amazon']['video_available']
def test_affiliate_exact_identity(client):
 data=studio.new_campaign(product(),profile(client,'affiliate','https://amazon.com'))
 assert dest(client,data,'https://amazon.com/dp/B999999999?tag=mytag-20').status_code==422
 url='https://amazon.com/dp/B012345678?tag=mytag-20';data=dest(client,data,url).json()['campaign']
 assert all(p['destination_url']==url for p in data['posts'].values())
 assert 'commission' in data['posts']['pinterest']['caption']
def test_ready_export_persistence_snapshot(client):
 data=campaign(client)
 assert client.get('/api/v2/campaigns/'+data['id']+'/export').status_code==409
 assert client.get('/api/v2/campaigns/'+data['id']+'/assets/secret.env').status_code==404
 data=ready(client,data);profile(client,'affiliate','https://amazon.com')
 r=client.get('/api/v2/campaigns/'+data['id']).json()['campaign'];assert r['profile']['store_url']=='https://tryme.gumroad.com'
 response=client.get('/api/v2/campaigns/'+data['id']+'/export');assert response.status_code==200
 with zipfile.ZipFile(io.BytesIO(response.content)) as z:
  assert 'short-video.mp4' in z.namelist() and all(ch+'/posting-guide.txt' in z.namelist() for ch in CHANNELS)
  assert not any('.env' in n for n in z.namelist())
  assert Image.open(io.BytesIO(z.read('pin.jpg'))).size==(1000,1500)
  assert 'tryme.gumroad.com' in z.read('pinterest/posting-guide.txt').decode()
 assert dest(client,data).status_code==409

def test_manual_post_calendar_metrics(client):
 data=ready(client,campaign(client));base='/api/v2/campaigns/'+data['id'];path=base+'/post'
 assert client.put(path,json={'channel':'pinterest','state':'posted'}).status_code==422
 assert client.put(path,json={'channel':'tiktok','state':'planned'}).status_code==422
 r=client.put(path,json=dict(channel='pinterest',state='planned',scheduled_at='2026-11-01T12:00:00-08:00',caption='Edited copy',views=100,clicks=12,sales=2)).json()['campaign']['posts']['pinterest']
 assert r['metrics_origin']=='user_reported' and not r['automatic_publish']
 assert 'DTSTART:20261101T200000Z' in client.get(base+'/calendar').text
 assert client.put(path,json={'channel':'pinterest','sales':-1}).status_code==422
 assert client.get(base).json()['campaign']['posts']['pinterest']['caption']=='Edited copy'
def test_same_origin(client):
 profile(client);assert client.get('/api/v2/workspace',headers={'Origin':'https://evil.example'}).status_code==403
@pytest.mark.parametrize('size',[(400,1400),(1400,400),(1000,1000)])
def test_image_full_source_centered(tmp_path,size):
 p=tmp_path/'photo.png';im=Image.new('RGB',size,'white');ImageDraw.Draw(im).rectangle((0,0,size[0]-1,size[1]-1),outline='red',width=15);im.save(p);render_images(p,tmp_path/'out','Product','Brand')
 import numpy as np
 a=np.asarray(Image.open(tmp_path/'out/pin.jpg'));red=(a[:,:,0]>150)&(a[:,:,1]<120)&(a[:,:,2]<120)&(np.indices(a.shape[:2])[0]<1020)
 y,x=np.where(red);assert x.min()>=60 and x.max()<=940 and y.min()>=60
 assert abs((x.min()+x.max())/2-500)<3

def test_failed_build_preserves_destination(client,monkeypatch):
 data=dest(client,campaign(client)).json()['campaign']
 monkeypatch.setattr('backend.photo_video.dependencies',lambda:('model','voice'))
 monkeypatch.setattr(studio,'_download_product_image',lambda *a:(_ for _ in ()).throw(ValueError('Seller photo unavailable')))
 assert client.post('/api/v2/campaigns/'+data['id']+'/build',json={}).status_code==202
 for _ in range(100):
  result=store.get(data['id'])
  if result['status']=='failed':break
  time.sleep(.01)
 assert result['status']=='failed' and result['destination_url'] and 'Seller photo unavailable' in result['message']


def test_focused_queries_and_no_cross_store_rotation(client):
 from backend.discovery_engine import query_plan
 queries=query_plan(2,seed=1,merchant_filters=['site:amazon.com'],product_queries=['travel tumbler'])
 assert all('travel tumbler' in q and 'site:amazon.com' in q for q in queries)
 assert all('target' not in q and 'walmart' not in q for q in queries)
 assert client.put('/api/v2/settings',json=dict(brand='Shop',store_url='https://shop.com',research_domains=['amazon.com'],product_queries=['a'*101])).status_code==422


def test_one_click_affiliate_and_owned_listing_destinations(client):
 p=profile(client,'affiliate','https://amazon.com');p['amazon_tag']='example-20'
 c=studio.new_campaign(product(),p)
 assert c['status']=='draft' and 'tag=example-20' in c['destination_url']
 assert all(post['destination_url']==c['destination_url'] for post in c['posts'].values())
 p=profile(client,'own_store','https://tryme.gumroad.com');p['research_domains']=['tryme.gumroad.com']
 own=product();own['url']='https://tryme.gumroad.com/l/tumbler'
 c=studio.new_campaign(own,p)
 assert c['destination_url']==c['product']['url']
 p=profile(client,'own_store','https://amazon.com')
 assert studio.new_campaign(product(),p)['status']=='needs_destination'
 p=profile(client,'own_store','https://tryme.gumroad.com')
 assert studio.new_campaign(product(),p)['status']=='needs_destination'


def test_build_keeps_purchase_link_separate_from_fact_source(client,monkeypatch,tmp_path):
 data=dest(client,campaign(client)).json()['campaign']
 picture=tmp_path/'photo.png';Image.new('RGB',(800,1200),'teal').save(picture)
 monkeypatch.setattr(studio,'_download_product_image',lambda *a:picture)
 monkeypatch.setattr('backend.photo_video_api._validate_public_https_url',lambda u:u)
 def create(payload,request):
  assert payload.product_url=='https://amazon.com/dp/B012345678'
  assert payload.campaign_destination=='https://tryme.gumroad.com/l/tumbler'
  return {'video':{'id':'a'*32}}
 monkeypatch.setattr(studio,'create_video',create)
 monkeypatch.setattr(studio,'read_record',lambda jid:dict(status='succeeded',seconds=15,video_url='/fixture.mp4',sales_plan={'destination':data['destination_url'],'phrases':['Product','Visit our profile']}))
 studio.build_worker(data['id'],studio.BuildRequest(),None)
 saved=store.get(data['id']);assert saved['status']=='ready' and saved['script']['destination']==data['destination_url']


def test_atomic_campaign_updates_close_connections(client):
 from concurrent.futures import ThreadPoolExecutor
 data=campaign(client)
 def increment(i):
  def change(c):c['counter']=c.get('counter',0)+1
  return store.mutate(data['id'],change)
 with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(increment,range(80)))
 assert store.get(data['id'])['counter']==80
 assert store.settings()['brand']=='TryMe'
 # Export-independent database backup succeeds after concurrent requests.
 import sqlite3,os
 with sqlite3.connect(os.environ['TRAFFICLIFT_WORKSPACE_DB']) as c:
  assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_one_button_without_setup_and_ignores_old_topics(client,monkeypatch):
 def fake(**kw):
  assert kw == dict(client_id='c'*32,require_signals=True,broad_market=True)
  p=product();p['url']='https://independent-seller.com/products/new-item';return p
 monkeypatch.setattr('backend.discovery_engine.discover',fake)
 data=client.post('/api/v2/discover',json={'client_id':'c'*32}).json()['campaign']
 assert data['status']=='needs_destination' and data['profile']['store_url']==''
 assert store.settings() is None
 saved=client.put('/api/v2/campaigns/'+data['id']+'/destination',json=dict(url='https://myshop.com/products/new-item',same_product=True,mode='own_store'))
 assert saved.status_code==200
 assert saved.json()['campaign']['profile']['store_url']=='https://myshop.com'
 p=profile(client);p['product_queries']=['camera tripod'];store.put_settings(p)
 data=client.post('/api/v2/discover',json={'client_id':'c'*32}).json()['campaign']
 assert data['product']['url'].startswith('https://independent-seller.com')
 assert data['profile']['product_queries']==[] and data['profile']['research_domains']==[]
 assert data['profile']['store_url']=='https://tryme.gumroad.com'
 assert dest(client,data,'https://target.com/p/same').status_code==422

def test_broad_market_queries_have_no_topic_or_retailer_lock():
 from backend.discovery_engine import query_plan,PRODUCT_QUERIES
 queries=query_plan(0,seed=1,merchant_filters=['site:target.com'],product_queries=['camera tripod'],broad_market=True)
 assert len(queries)==3
 assert all('camera tripod' not in q and ('inurl:' in q or 'site:' in q) for q in queries)
 assert all(not any(topic in q for topic in PRODUCT_QUERIES) for q in queries)
 assert query_plan(1,broad_market=True)!=queries


def test_inline_url_uses_explicit_purchase_page_and_preserves_saved_store(client,monkeypatch):
 monkeypatch.setattr('backend.exact_product.analyze_exact_product',lambda url:dict(product(),url=url,source='user_url'))
 own='https://newshop.com/products/tumbler'
 response=client.post('/api/v2/import',json=dict(url=own,use_as_destination=True))
 assert response.status_code==200
 data=response.json()['campaign'];assert data['status']=='draft' and data['destination_url']==own
 assert data['profile']['store_url']=='https://newshop.com'
 profile(client)
 data=client.post('/api/v2/import',json=dict(url=own,use_as_destination=True)).json()['campaign']
 assert data['status']=='needs_destination' and not data['destination_url']
 assert data['profile']['store_url']=='https://tryme.gumroad.com'


@pytest.mark.parametrize('url',['https://www.shopperapproved.com/','https://www.shopperapproved.com/product-reviews/123/bottle','https://results.shopperapproved.com/products/bottle','https://trustpilot.com/review/shop.com','https://seller.com/','https://seller.com/reviews/bottle'])
def test_non_seller_pages_cannot_be_product_or_destination(client,url):
 from backend.discovery import _is_listing_url
 assert not _is_listing_url(url)
 assert client.post('/api/v2/import',json=dict(url=url,use_as_destination=True)).status_code==422
 data=campaign(client)
 assert dest(client,data,url).status_code==422
 p=product();p['url']=url
 with pytest.raises(HTTPException):studio.new_campaign(p,dict(profile(client),research_domains=[]))
 assert not store.get(data['id'])['destination_url']

def test_review_service_cannot_be_configured_as_store(client):
 assert client.put('/api/v2/settings',json=dict(brand='Shop',store_url='https://shopperapproved.com',research_domains=[])).status_code==422


def test_old_review_campaign_is_not_exported_and_does_not_break_home(client):
 data=campaign(client);p=dict(data['product'],url='https://shopperapproved.com/products/bottle')
 store.update(data['id'],product=p,status='ready')
 workspace=client.get('/api/v2/workspace')
 assert workspace.status_code==200 and workspace.json()['unavailable_campaigns']==1
 assert not workspace.json()['campaigns']
 assert client.get('/api/v2/campaigns/'+data['id']+'/export').status_code==409
 assert client.get('/api/v2/campaigns/'+data['id']+'/assets/pin.jpg').status_code==409
 assert store.get(data['id']) is not None

def test_old_shared_photo_campaign_cannot_be_reused(client):
 data=campaign(client);p=dict(data['product'],image_origin='image_research')
 store.update(data['id'],product=p,status='ready')
 response=client.get('/api/v2/workspace')
 assert response.status_code==200 and response.json()['unavailable_campaigns']==1
 assert client.get('/api/v2/campaigns/'+data['id']+'/export').status_code==409
