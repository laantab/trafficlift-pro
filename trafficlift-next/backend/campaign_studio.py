"""TrafficLift 5 campaign workspace. Local-only, truthful state and explicit destinations."""
import io,json,os,re,threading,time,uuid,zipfile
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from typing import Literal
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import FileResponse,StreamingResponse,Response
from pydantic import BaseModel,Field,field_validator,ValidationError
from backend import campaign_store as store
from backend.photo_video_api import guard,storage,VideoRequest,create as create_video,read_record,record_path
from backend.platform_kit import CHANNELS,kit,render_images
from local_video.trafficlift_video_bridge import _download_product_image
router=APIRouter(prefix='/api/v2')
_active=set();_mutex=threading.Lock()

def safe_url(value):
    p=urlsplit(value.strip())
    if p.scheme!='https' or not p.hostname or p.username or p.password or len(value)>2048:
        raise ValueError('Use a complete https:// product or store link.')
    import ipaddress
    try:ipaddress.ip_address(p.hostname);raise ValueError('Use your public store domain, not an IP address.')
    except ValueError as exc:
        if str(exc).startswith('Use your public'):raise
    host=p.hostname.lower()
    if '.' not in host or host in {'localhost','localhost.localdomain'} or host.endswith(('.local','.localhost','.internal','.test')):
        raise ValueError('Use your public store domain.')
    try:
        if p.port not in {None,443}:raise ValueError('Store links must use the standard HTTPS port.')
    except ValueError:raise ValueError('Use a valid HTTPS store link.')
    return value.strip()

def domain(value):return (urlsplit(value).hostname or '').lower().removeprefix('www.')
def on_domain(url,host):
    h=domain(url);return h==host or h.endswith('.'+host)

class Profile(BaseModel):
    brand:str=Field(min_length=1,max_length=80)
    store_url:str=Field(default='',max_length=2048)
    mode:Literal['own_store','affiliate']='own_store'
    research_domains:list[str]=Field(default_factory=list,max_length=5)
    product_queries:list[str]=Field(default_factory=list,max_length=8)
    amazon_tag:str=Field(default='',max_length=80,pattern=r'^(?:[A-Za-z0-9-]+-[0-9]{2})?$')
    channels:list[Literal['pinterest','tiktok','instagram','youtube','amazon','gumroad']]=Field(default_factory=lambda:CHANNELS.copy(),min_length=1,max_length=6)
    @field_validator('store_url')
    @classmethod
    def url(cls,v):
        from backend.discovery import is_evidence_site
        if v and is_evidence_site(v):raise ValueError('Use your selling website, not a review or research service.')
        return safe_url(v) if v else ''
    @field_validator('brand')
    @classmethod
    def name(cls,v):
        v=' '.join(v.split())
        if not v:raise ValueError('Enter your brand name.')
        return v
    @field_validator('product_queries')
    @classmethod
    def topics(cls,values):
        result=[]
        for value in values:
            value=' '.join(value.split())
            if not value or len(value)>100:raise ValueError('Use short product topics, up to 100 characters each.')
            if value not in result:result.append(value)
        return result
    @field_validator('research_domains')
    @classmethod
    def domains(cls,values):
        result=[]
        for value in values:
            h=domain(safe_url('https://'+value.strip().lower().removeprefix('https://').removeprefix('http://').rstrip('/')))
            if not re.fullmatch(r'[a-z0-9.-]+',h):raise ValueError('Use website domains such as amazon.com.')
            if h not in result:result.append(h)
        return result

class FindRequest(BaseModel):
    client_id:str=Field(min_length=16,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
class ImportRequest(BaseModel):
    url:str
    use_as_destination:bool=False
    @field_validator('url')
    @classmethod
    def url_valid(cls,v):
        from backend.discovery import _is_listing_url
        v=safe_url(v)
        if not _is_listing_url(v):raise ValueError('Use the exact product purchase page, not a homepage, review website, or directory.')
        return v
class DestinationRequest(BaseModel):
    url:str
    same_product:bool=False
    mode:Literal['own_store','affiliate']|None=None
    @field_validator('url')
    @classmethod
    def url_valid(cls,v):
        from backend.discovery import _is_listing_url
        v=safe_url(v)
        if not _is_listing_url(v):raise ValueError('Use your product purchase page or affiliate listing. A review website or directory is not a purchase page.')
        return v
class BuildRequest(BaseModel):
    seconds:Literal[15,30,60]=15
    style:Literal['warm','clean','bold']='clean'
class PostRequest(BaseModel):
    channel:Literal['pinterest','tiktok','instagram','youtube','amazon','gumroad']
    caption:str=Field(default='',max_length=5000)
    scheduled_at:str|None=None
    published_url:str=Field(default='',max_length=2048)
    state:Literal['not_posted','planned','posted']='not_posted'
    views:int=Field(default=0,ge=0,le=1_000_000_000)
    clicks:int=Field(default=0,ge=0,le=1_000_000_000)
    sales:int=Field(default=0,ge=0,le=1_000_000_000)
    @field_validator('published_url')
    @classmethod
    def url_valid(cls,v):return safe_url(v) if v else ''
    @field_validator('scheduled_at')
    @classmethod
    def date_valid(cls,v):
        if v:
            d=datetime.fromisoformat(v.replace('Z','+00:00'))
            if not d.tzinfo:raise ValueError('Choose a date with your time zone.')
        return v

def discovery_profile():
    # Selling preferences never constrain the winning-product search. Older
    # topic/domain fields are retained for compatibility, ignored by this flow.
    p=dict(store.settings() or dict(brand='TrafficLift Pro',store_url='',mode='own_store',amazon_tag='',channels=CHANNELS.copy()))
    p.update(research_domains=[],product_queries=[])
    return p

def get_campaign(cid):
    if not re.fullmatch(r'[0-9a-f]{32}',cid):raise HTTPException(404,'Campaign not found.')
    data=store.get(cid)
    if not data:raise HTTPException(404,'Campaign not found.')
    from backend.discovery import _is_listing_url
    if not _is_listing_url(data['product'].get('url','')):raise HTTPException(409,'This saved campaign used a review page, homepage, or directory rather than a product listing. Find a new product; no files were deleted.')
    if data['product'].get('image_origin')=='image_research':raise HTTPException(409,'This older campaign used a general search photo without exact-product provenance. Find a new product; no files were deleted.')
    # Recover finished video after a restart, or expose an interrupted build.
    if data['status']=='building' and cid not in _active:
        job=read_record(data['video_id']) if data.get('video_id') else None
        if job and job['status']=='succeeded' and all((campaign_dir(cid)/n).is_file() for n in ['pin.jpg','square.jpg','story.jpg']):
            data=store.update(cid,status='ready',message='Your marketing kit is ready.',video_url=job['video_url'],script=job.get('sales_plan',{}))
        elif not job or job['status']=='failed':data=store.update(cid,status='failed',message='The build was interrupted. Your product and purchase link are saved; try creating the kit again.')
    return data

def campaign_dir(cid):return storage().parent/'campaign-assets'/cid

def public_campaign(data):
    result=dict(data)
    result['assets']={n:f"/api/v2/campaigns/{data['id']}/assets/{n}" for n in ['pin.jpg','square.jpg','story.jpg','product-photo.jpg']} if data['status']=='ready' else {}
    result['export_url']=f"/api/v2/campaigns/{data['id']}/export" if data['status']=='ready' else None
    return result

def new_campaign(product,profile):
    source=product.get('url','')
    if not source or (profile.get('research_domains') and not any(on_domain(source,d) for d in profile['research_domains'])):
        raise HTTPException(422,'This product is outside your approved research websites. No campaign was created.')
    from backend.discovery import _is_listing_url
    if not _is_listing_url(source):raise HTTPException(422,'This research result is not an individual product listing. No campaign was created.')
    try:safe_url(source)
    except ValueError as exc:raise HTTPException(422,str(exc))
    if not product.get('name') or not product.get('image_url'):raise HTTPException(422,'This product needs an exact name and usable photo.')
    data=dict(id=uuid.uuid4().hex,created_at=time.time(),profile=profile,product=product,
              destination_url='',status='needs_destination',message='Add the page where customers can buy this exact product.',posts={},script={})
    # Your own exact listing, or an Amazon link with your explicitly saved tag,
    # can proceed without asking for the same destination on every click.
    from backend.exact_product import amazon_asin
    shared_marketplaces={'amazon.com','gumroad.com','etsy.com','ebay.com','target.com','walmart.com'}
    purchase = source if profile['store_url'] and profile['mode']=='own_store' and domain(profile['store_url']) not in shared_marketplaces and on_domain(source,domain(profile['store_url'])) else ''
    if profile['mode']=='affiliate' and profile.get('amazon_tag') and amazon_asin(source) and on_domain(profile['store_url'],'amazon.com') and on_domain(source,domain(profile['store_url'])):
        parsed=urlsplit(source);query=dict(parse_qsl(parsed.query,keep_blank_values=True));query['tag']=profile['amazon_tag']
        purchase=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(query),parsed.fragment))
    if purchase:
        data.update(destination_url=purchase,status='draft',message='Your configured purchase link is ready.')
        data['posts']=kit(data)
    return public_campaign(store.save(data))

@router.get('/workspace')
def workspace(request:Request):
    guard(request)
    from backend.research_config import research_status
    campaigns=[];unavailable=0
    for c in store.all_campaigns():
        try:campaigns.append(public_campaign(get_campaign(c['id'])))
        except HTTPException:unavailable+=1
    return dict(profile=store.settings(),campaigns=campaigns,unavailable_campaigns=unavailable,research=research_status(),
                publishing={c:{'mode':'manual_upload','connected':False} for c in CHANNELS},version='5.2.2')

@router.put('/settings')
def settings(payload:Profile,request:Request):
    guard(request);return {'profile':store.put_settings(payload.model_dump())}

@router.post('/discover')
def discover(payload:FindRequest,request:Request):
    guard(request);profile=discovery_profile()
    from backend.discovery_engine import discover
    product=discover(client_id=payload.client_id,require_signals=True,broad_market=True)
    return {'campaign':new_campaign(product,profile)}

@router.post('/import')
def import_product(payload:ImportRequest,request:Request):
    guard(request);profile=discovery_profile()
    from backend.exact_product import analyze_exact_product
    product=analyze_exact_product(payload.url)
    data=new_campaign(product,profile)
    # The inline entry explicitly asks for the user's purchase page. Preserve
    # a saved selling domain and any automatically tagged affiliate link.
    if payload.use_as_destination and not data['destination_url'] and (not profile.get('store_url') or on_domain(product['url'],domain(profile['store_url']))):
        return destination(data['id'],DestinationRequest(url=product['url'],same_product=True),request)
    return {'campaign':data}

@router.get('/campaigns/{cid}')
def campaign(cid:str,request:Request):
    guard(request);return {'campaign':public_campaign(get_campaign(cid))}

@router.put('/campaigns/{cid}/destination')
def destination(cid:str,payload:DestinationRequest,request:Request):
    guard(request);data=get_campaign(cid)
    if data['status'] in {'building','ready'}:raise HTTPException(409,'This kit already uses its saved purchase link. Create a new campaign to use a different destination.')
    profile=data['profile']
    if profile.get('store_url') and not on_domain(payload.url,domain(profile['store_url'])):raise HTTPException(422,'The purchase link must be on your saved selling website. A research retailer was not substituted.')
    if urlsplit(payload.url).path in {'','/'}:raise HTTPException(422,'Use the exact product page, not a store homepage.')
    if not payload.same_product:raise HTTPException(422,'Confirm that this purchase page sells the selected product.')
    from backend.exact_product import amazon_asin
    a,b=amazon_asin(data['product']['url']),amazon_asin(payload.url)
    if a and b and a!=b:raise HTTPException(422,'This Amazon purchase link identifies a different product.')
    profile=dict(profile)
    if not profile.get('store_url'):
        parsed=urlsplit(payload.url);profile['store_url']=urlunsplit((parsed.scheme,parsed.netloc,'','',''))
    if payload.mode is not None:profile['mode']=payload.mode
    data=store.update(cid,profile=profile,destination_url=payload.url,status='draft',message='Ready to create your marketing kit.')
    data=store.update(cid,posts=kit(data))
    return {'campaign':public_campaign(data)}


def build_worker(cid,payload,request):
    try:
        data=get_campaign(cid);directory=campaign_dir(cid);directory.mkdir(parents=True,exist_ok=True)
        store.update(cid,message='Preparing your product photo and pin…')
        image=_download_product_image(data['product']['image_url'],directory)
        render_images(image,directory,data['product']['name'],data['profile']['brand'])
        store.update(cid,message='Recording narration and creating your short video…')
        product=data['product']
        video_payload=VideoRequest(name=product['name'],image_url=product['image_url'],product_url=product['url'],
                                  seconds=payload.seconds,style=payload.style,campaign_destination=data['destination_url'],
                                  fact_sources=(product.get('research_sources') or [])[:4],campaign_cta='Visit our profile for product details.')
        job=create_video(video_payload,request)['video'];store.update(cid,video_id=job['id'])
        deadline=time.monotonic()+960
        while time.monotonic()<deadline:
            current=read_record(job['id'])
            if current['status'] in {'succeeded','failed'}:break
            time.sleep(1)
        else:raise ValueError('The video took too long. Your campaign is saved; check it again before retrying.')
        if current['status']!='succeeded':raise ValueError(current['message'])
        plan=current.get('sales_plan',{})
        # Build copy from the same facts as the actual spoken script.
        if plan.get('evidence'):
            product=dict(product,seller_benefit=plan['evidence'][0]['text'])
            store.update(cid,product=product)
        data=store.get(cid)
        store.update(cid,status='ready',message='Your marketing kit is ready.',video_url=current['video_url'],
                     script=plan,posts=kit(data,plan),seconds=current['seconds'],style=payload.style)
    except Exception as exc:
        if isinstance(exc,ValidationError):
            message=' '.join(error['msg'].removeprefix('Value error, ') for error in exc.errors())
        else:message=str(exc.detail) if isinstance(exc,HTTPException) else str(exc)
        store.update(cid,status='failed',message=message[:600])
    finally:
        with _mutex:_active.discard(cid)

@router.post('/campaigns/{cid}/build',status_code=202)
def build(cid:str,payload:BuildRequest,request:Request):
    guard(request);data=get_campaign(cid)
    if not data.get('destination_url'):raise HTTPException(409,'Add the exact purchase page before creating a kit.')
    if data['status']=='ready':return {'campaign':public_campaign(data)}
    with _mutex:
        if cid in _active:return {'campaign':public_campaign(data)}
        if _active:raise HTTPException(409,'Another kit is being created. Let it finish first.')
        from backend.photo_video import dependencies
        try:dependencies()
        except ValueError as exc:raise HTTPException(503,str(exc))
        _active.add(cid)
        try:
            data=store.update(cid,status='building',message='Starting your marketing kit…')
            threading.Thread(target=build_worker,args=(cid,payload,request),daemon=True).start()
        except Exception:_active.discard(cid);raise
    return {'campaign':public_campaign(data)}

@router.get('/campaigns/{cid}/assets/{filename}')
def asset(cid:str,filename:str,request:Request):
    guard(request);data=get_campaign(cid)
    if data['status']!='ready' or filename not in {'pin.jpg','square.jpg','story.jpg','product-photo.jpg'}:raise HTTPException(404,'This asset is not ready.')
    path=campaign_dir(cid)/filename
    if not path.is_file():raise HTTPException(404,'This asset is missing. Your campaign is still saved.')
    return FileResponse(path,media_type='image/jpeg',filename=filename,headers={'Cache-Control':'no-store'})

@router.put('/campaigns/{cid}/post')
def post(cid:str,payload:PostRequest,request:Request):
    guard(request);data=get_campaign(cid)
    if data['status']!='ready':raise HTTPException(409,'Finish creating this kit before planning or recording a post.')
    if payload.state=='planned' and not payload.scheduled_at:raise HTTPException(422,'Choose when you intend to post.')
    if payload.state=='posted' and not payload.published_url:raise HTTPException(422,'Paste the published post URL to record it as posted.')
    def change(d):
        entry=dict(d['posts'][payload.channel]);entry.update(payload.model_dump(exclude={'channel'}),metrics_origin='user_reported',automatic_publish=False)
        if not payload.caption:entry['caption']=d['posts'][payload.channel]['caption']
        d['posts'][payload.channel]=entry
    data=store.mutate(cid,change)
    return {'campaign':public_campaign(data)}

@router.get('/campaigns/{cid}/calendar')
def calendar(cid:str,request:Request):
    guard(request);data=get_campaign(cid)
    def esc(s):return str(s).replace('\\','\\\\').replace('\n','\\n').replace(',','\\,').replace(';','\\;')
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//TrafficLift//Posting reminders//EN']
    for channel,post in data.get('posts',{}).items():
        if post.get('state')!='planned' or not post.get('scheduled_at'):continue
        dt=datetime.fromisoformat(post['scheduled_at'].replace('Z','+00:00')).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        lines+=['BEGIN:VEVENT',f'UID:{cid}-{channel}@trafficlift.local',f'DTSTAMP:{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}',f'DTSTART:{dt}',f'SUMMARY:{esc("Post to "+channel+": "+data["product"]["name"])}',f'DESCRIPTION:{esc("Manual posting reminder. Open TrafficLift and upload your saved kit.")}','END:VEVENT']
    lines+=['END:VCALENDAR']
    return Response('\r\n'.join(lines)+'\r\n',media_type='text/calendar',headers={'Content-Disposition':'attachment; filename="posting-reminders.ics"'})

@router.get('/campaigns/{cid}/export')
def export(cid:str,request:Request):
    guard(request);data=get_campaign(cid)
    if data['status']!='ready':raise HTTPException(409,'This kit is not ready to download.')
    buffer=io.BytesIO();directory=campaign_dir(cid)
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        for filename in ['pin.jpg','square.jpg','story.jpg','product-photo.jpg']:
            path=directory/filename
            if not path.is_file():raise HTTPException(409,'A campaign image is missing. No incomplete ZIP was exported.')
            z.write(path,filename)
        path=record_path(data['video_id']).parent/'video.mp4'
        if not path.is_file():raise HTTPException(409,'The campaign video is missing. No incomplete ZIP was exported.')
        z.write(path,'short-video.mp4')
        for channel in data['profile']['channels']:
            post=data['posts'][channel]
            text=f"{post['title']}\n\n{post['caption']}\n\nPurchase page: {post['destination_url']}\n\n{post['instructions']}\n"
            z.writestr(channel+'/posting-guide.txt',text)
        z.writestr('sales-script.txt','\n\n'.join(data.get('script',{}).get('phrases',[])))
        z.writestr('research-evidence.json',json.dumps({k:data['product'].get(k) for k in ['name','url','research_sources','research_timestamp','research_provider','demand_evidence_status','selection_rationale']},indent=2))
        z.writestr('campaign.json',json.dumps(data,indent=2))
        z.writestr('READ_ME.txt','Your marketing kit contains the actual product photograph and a narrated photo-based video, not a filmed demonstration. Purchase links are your approved destination. Use each platform posting guide. Uploads are manual; planned dates are reminders, not automatic publication. Results are user-reported, not verified platform analytics. Amazon receives an unbranded photo and listing copy; the social video is not an Amazon listing-video export.\n')
    buffer.seek(0)
    return StreamingResponse(buffer,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="TrafficLift-kit-{cid[:8]}.zip"'})
