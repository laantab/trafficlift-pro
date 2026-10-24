"""Opt-in loopback studio, persistent job records and a single CPU worker."""
from pathlib import Path
import json, os, re, threading, time, uuid
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, field_validator, SecretStr
from typing import Literal
from backend.photo_video import dependencies, render
from backend.sales_script import build_sales_plan
from backend.product_facts import resolve_video_facts
from local_video.trafficlift_video_bridge import _download_product_image, _validate_public_https_url

router=APIRouter()
lock=threading.Lock()
active_job_id = None  # In-process owner of the single render lock.
ROOT=Path(__file__).resolve().parents[1]

def storage():
    """Use persistent storage for hosted jobs, even during bootstrap startup.

    Provisioning runs in a background thread. Hosted requests must not race
    its environment setup and accidentally write jobs to an ephemeral folder.
    Local desktop studio keeps its existing project-relative default.
    """
    configured = os.environ.get('TRAFFICLIFT_VIDEO_DIR')
    if configured:
        return Path(configured).resolve()
    if os.environ.get('TRAFFICLIFT_HOSTED_VIDEO') == '1':
        return Path('/var/data/trafficlift-videos')
    return (ROOT / 'video/studio').resolve()

def guard(request):
    if os.environ.get('TRAFFICLIFT_FREE_VIDEO')!='1':raise HTTPException(503,'Free video studio is not enabled here. Open Start_Free_Video_Studio.bat on your PC.')
    if not request.client or request.client.host not in {'127.0.0.1','::1','localhost','testclient'}:raise HTTPException(403,'Free studio is available only on this computer.')
    if request.url.hostname not in {'127.0.0.1','localhost','testserver'}:raise HTTPException(403,'Open the studio at its localhost address.')
    origin=request.headers.get('origin')
    if origin and origin!=f'{request.url.scheme}://{request.url.netloc}':raise HTTPException(403,'Open the free studio on this computer to render videos.')

def video_guard(request):
    """Keep the loopback mode intact; cloud rendering must be explicitly enabled.

    Cloud video creation uses a separate quota on persistent storage. Sensitive
    research-connection endpoints continue using the loopback-only guard.
    """
    if os.environ.get('TRAFFICLIFT_HOSTED_VIDEO') != '1':
        return guard(request)
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Open TrafficLift at its official website to render.')
    # Same-origin headers are not authentication: direct HTTP clients can omit or
    # forge Origin. A hosted renderer must not become a public compute endpoint.
    import secrets
    configured = os.getenv('TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN', '')
    if len(configured) < 32:
        raise HTTPException(503, 'Hosted video access is not configured.')
    supplied = request.headers.get('x-trafficlift-video-token', '')
    cookie = getattr(request, 'cookies', {}).get('trafficlift_video_session', '')
    if not secrets.compare_digest(supplied, configured) and not valid_video_session(cookie, configured):
        raise HTTPException(401, 'Video access requires authorization.')
    # An eventual account/session system can replace this temporary operator-only
    # gate. Never embed this secret in index.html or distribute it to customers.



def valid_video_session(cookie: str, secret: str) -> bool:
    """Stateless, expiring, signed operator session. Never expose the secret."""
    import secrets, hmac, hashlib
    try:
        timestamp, signature = cookie.split('.', 1)
        issued = int(timestamp)
        if not 0 <= time.time() - issued < 8 * 60 * 60:
            return False
        expected = hmac.new(secret.encode(), ('video-session:' + timestamp).encode(), hashlib.sha256).hexdigest()
        return secrets.compare_digest(signature, expected)
    except (TypeError, ValueError, AttributeError):
        return False


class OwnerVideoLogin(BaseModel):
    access_code: SecretStr


@router.post('/api/v1/video-owner-login')
def owner_video_login(payload: OwnerVideoLogin, request: Request):
    """Temporary owner-only beta, not public customer accounts."""
    from fastapi.responses import JSONResponse
    import hmac, hashlib, secrets
    if os.getenv('TRAFFICLIFT_HOSTED_VIDEO') != '1':
        raise HTTPException(503, 'Hosted video is not yet enabled.')
    origin = request.headers.get('origin', '')
    if origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Open the official TrafficLift website.')
    configured = os.getenv('TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN', '')
    if len(configured) < 32 or not secrets.compare_digest(payload.access_code.get_secret_value(), configured):
        raise HTTPException(401, 'Invalid access code.')
    issued = str(int(time.time()))
    sig = hmac.new(configured.encode(), ('video-session:' + issued).encode(), hashlib.sha256).hexdigest()
    response = JSONResponse({'authorized': True, 'expires_in_seconds': 28800})
    response.set_cookie('trafficlift_video_session', issued + '.' + sig,
                        max_age=28800, httponly=True, secure=True, samesite='strict', path='/api/v1')
    return response


def reserve_hosted_render():
    """Atomically bound daily hosted costs across threads and restarts."""
    if os.environ.get('TRAFFICLIFT_HOSTED_VIDEO') != '1':
        return
    import sqlite3
    path = storage() / 'hosted_quota.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    limit = min(20, max(1, int(os.getenv('TRAFFICLIFT_HOSTED_DAILY_LIMIT', '3'))))
    day = time.strftime('%Y-%m-%d', time.gmtime())
    try:
        with sqlite3.connect(path, timeout=5) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS daily_renders (day TEXT PRIMARY KEY, count INTEGER NOT NULL)')
            row = db.execute('SELECT count FROM daily_renders WHERE day=?', (day,)).fetchone()
            if row and row[0] >= limit:
                raise HTTPException(429, 'Today’s video capacity has been reached. Please return tomorrow.')
            db.execute('INSERT INTO daily_renders(day,count) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1', (day,))
            db.execute('DELETE FROM daily_renders WHERE day<?', (day,))
    except sqlite3.Error as exc:
        raise HTTPException(503, 'Video capacity accounting is temporarily unavailable.') from exc


class VideoRequest(BaseModel):
    name:str=Field(min_length=1,max_length=90)
    image_url:str=Field(max_length=2048)
    product_url:str=Field(max_length=2048)
    benefit:str=Field(default='',max_length=160)
    fact_sources:list[dict]=Field(default_factory=list,max_length=4)
    buyer_need:str=Field(default='',max_length=100)
    consideration:str=Field(default='',max_length=120)
    seconds:Literal[15,30,60]=15
    style:Literal['warm','clean','bold']='warm'
    @field_validator('name','benefit','buyer_need','consideration')
    @classmethod
    def plain_text(cls,value):
        value=' '.join(value.split())
        if any(c in value for c in '{}\\'):raise ValueError('Use plain text without formatting codes.')
        return value
    @field_validator('image_url','product_url')
    @classmethod
    def public_url(cls,value):
        _validate_public_https_url(value)
        return value

def record_path(job_id):
    if not re.fullmatch(r'[0-9a-f]{32}',job_id):raise HTTPException(404,'Video not found.')
    current=storage()/job_id/'job.json'
    if current.is_file():return current
    legacy=os.environ.get('TRAFFICLIFT_LEGACY_VIDEO_DIR')
    old=Path(legacy)/job_id/'job.json' if legacy else None
    return old if old and old.is_file() else current

def read_record(job_id):
    path=record_path(job_id)
    if not path.is_file():raise HTTPException(404,'Video not found.')
    try:data=json.loads(path.read_text())
    except (OSError,ValueError):raise HTTPException(503,'Video record temporarily unavailable.')
    # Interrupted jobs never appear finished after a restart.
    if data['status'] not in {'succeeded','failed'} and (not lock.locked() or active_job_id != job_id):
        # Another render holding the lock does not make an interrupted job active.
        data.update(status='failed',message='Render was interrupted. Start a new video.',video_url=None)
        save(data,path.parent)
    return data

def save(record,directory):
    tmp=directory/('job-'+uuid.uuid4().hex+'.tmp')
    try:
        tmp.write_text(json.dumps(record,indent=2),encoding='utf-8')
        for attempt in range(8):
            try:
                tmp.replace(directory/'job.json')
                return
            except PermissionError:
                if attempt==7:raise
                time.sleep(min(.8,.05*2**attempt))
    finally:
        try:tmp.unlink(missing_ok=True)
        except OSError:pass

def worker(record,payload,directory):
    global active_job_id
    def update(message):
        record.update(status='running',message=message);save(record,directory)
    try:
        update('Reading product facts and writing the complete sales script')
        from backend.product_facts import facts_from_sources
        facts = facts_from_sources(payload.name, payload.product_url, payload.fact_sources)
        # Named manufacturer briefs include routine and sizing information,
        # even when a single benefit was supplied in the input field.
        if not facts or 'oura ring 4' in payload.name.lower():
            fetched = resolve_video_facts(payload.name, payload.product_url)
            if fetched: facts = fetched
        photos = facts.pop('product_images',[]) if facts else []
        if payload.benefit:
            facts.update(benefit=payload.benefit)
        if payload.buyer_need: facts['buyer_need']=payload.buyer_need
        if payload.consideration: facts['consideration']=payload.consideration
        benefit=facts.pop('benefit','')
        record['sales_plan']=build_sales_plan(payload.name,benefit,payload.product_url,120,**facts)
        record['sales_plan']['requested_seconds']=payload.seconds
        record['seconds']=payload.seconds
        save(record,directory)
        update('Choosing the clearest photo from this product listing')
        from backend.photo_quality import resolution_variants,choose_photos
        candidates=[];urls=[];last_photo_error=None
        for original in [payload.image_url]+photos[:3]:
            for url in resolution_variants(original):
                if url not in urls:urls.append(url)
        for url in urls[:6]:
            try:
                photo_dir=directory/('source-'+str(len(candidates)));photo_dir.mkdir(exist_ok=True)
                candidates.append(_download_product_image(url,photo_dir))
            except Exception as exc:last_photo_error=exc;continue
        if not candidates and last_photo_error:raise last_photo_error
        chosen=choose_photos(candidates)
        image=chosen[0][0];extra_photos=[item[0] for item in chosen[1:4]]
        record['photo_quality']=chosen[0][1]
        record['marketing_review']={'status':'NEEDS_VISUAL_REVIEW',
                                   'message':'Render checks do not establish marketing approval. Check product identity, edge markings, focus, composition and the complete video.'}
        save(record,directory)
        out=render(image,directory,payload.name,payload.benefit,payload.product_url,record['seconds'],payload.style,update,sales_plan=record['sales_plan'],extra_images=extra_photos)
        quality_data=json.loads((directory/'quality.json').read_text()) if (directory/'quality.json').is_file() else {}
        record['seconds']=quality_data.get('duration',record['seconds'])
        record['requested_seconds']=payload.seconds
        record['duration_extended']=record['seconds']>payload.seconds
        record['narration_qa']=quality_data.get('narration_audio',{})
        record['scene_count']=quality_data.get('scene_count')
        record['marketing_review']['status']='NEEDS_VISUAL_REVIEW'
        record.update(status='succeeded',message=(('Video length extended to '+str(record['seconds'])+' seconds to keep narration complete. ' if record['duration_extended'] else '')+('Video rendered — visual marketing review required' if record['sales_plan'].get('mode') == 'benefit_led' else 'Product overview rendered — benefit details unavailable; visual review required')),video_url=f"/api/v1/photo-videos/{record['id']}/video")
    except Exception as exc:
        record.update(status='failed',message=str(exc)[-500:],video_url=None)
    finally:
        try:
            save(record,directory)
        finally:
            active_job_id = None
            lock.release()

@router.get('/studio',response_class=HTMLResponse)
def studio(request:Request):
    guard(request)
    html=(ROOT/'index.html').read_text(encoding='utf-8')
    html=re.sub(r'<meta name="api-base" content="[^"]*">','<meta name="api-base" content="">',html)
    return HTMLResponse(html,headers={'Cache-Control':'no-store'})

@router.get('/api/v1/photo-videos')
def history(request:Request):
    video_guard(request)
    records=[]
    paths=list(storage().glob('*/job.json'))
    legacy=os.environ.get('TRAFFICLIFT_LEGACY_VIDEO_DIR')
    if legacy:paths+=list(Path(legacy).glob('*/job.json'))
    seen=set()
    for path in sorted(paths,key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
        if path.parent.name in seen:continue
        seen.add(path.parent.name)
        records.append(read_record(path.parent.name))
    return {'videos':records}

@router.post('/api/v1/photo-videos',status_code=202)
def create(payload:VideoRequest,request:Request):
    global active_job_id
    video_guard(request)
    if not payload.name.strip():raise HTTPException(400,'Product name is required.')
    from backend.discovery import _looks_like_article
    if _looks_like_article(payload.name, ''):
        raise HTTPException(422,'This selection is a guide or roundup. Choose one specific product before making its video.')
    try:
        sales_plan=build_sales_plan(payload.name,payload.benefit,payload.product_url,120,payload.buyer_need,payload.consideration)
    except ValueError as exc:raise HTTPException(422,str(exc))
    try:dependencies()
    except ValueError as exc:raise HTTPException(503,str(exc))
    root=storage();root.mkdir(parents=True,exist_ok=True)
    if len(list(root.glob('*/job.json')))>=100:raise HTTPException(409,'Studio has 100 videos. Back up and remove older folders before creating more.')
    import shutil
    if shutil.disk_usage(root).free<250*1024*1024:raise HTTPException(409,'Not enough disk space. Free at least 250 MB before rendering.')
    if not lock.acquire(blocking=False):raise HTTPException(409,'A video is already running. Wait for it to finish.')
    try:
        reserve_hosted_render()
    except Exception:
        lock.release()
        raise
    try:
        job_id=uuid.uuid4().hex;directory=root/job_id;directory.mkdir()
        record={'id':job_id,'name':payload.name,'created_at':time.time(),'seconds':payload.seconds,'status':'queued','message':'Getting photo','video_url':None,'product_url':payload.product_url,'sales_plan':sales_plan}
        save(record,directory)
        active_job_id = job_id
        threading.Thread(target=worker,args=(record,payload,directory),daemon=True).start()
    except Exception:
        active_job_id = None
        lock.release();raise
    return {'video':record.copy()}

@router.get('/api/v1/photo-videos/{job_id}')
def get_job(job_id:str,request:Request):
    video_guard(request);return {'video':read_record(job_id)}

@router.get('/api/v1/photo-videos/{job_id}/video')
def download(job_id:str,request:Request):
    video_guard(request);record=read_record(job_id)
    if record['status']!='succeeded':raise HTTPException(409,'This video has not passed its checks.')
    path=record_path(job_id).parent/'video.mp4'
    if not path.is_file():raise HTTPException(404,'Saved video file is missing.')
    slug=re.sub(r'[^a-zA-Z0-9_-]+','-',record['name']).strip('-')[:60] or 'product'
    return FileResponse(path,media_type='video/mp4',filename=f"{slug}-{job_id[:8]}.mp4")




def owner_acceptance_record(request: Request):
    """Read a persisted real-render receipt only after owner authentication."""
    if os.getenv('TRAFFICLIFT_HOSTED_VIDEO') != '1':
        raise HTTPException(503, 'Hosted owner video is not enabled.')
    video_guard(request)
    directory = Path(os.getenv('TRAFFICLIFT_HOSTED_MODEL_DIR', '/var/data/models')) / 'owner-acceptance'
    try:
        receipt = json.loads((directory / 'receipt.json').read_text())
    except (OSError, ValueError):
        return directory, None
    if receipt.get('state') != 'passed' or not (directory / 'real-kokoro.mp4').is_file():
        return directory, None
    return directory, receipt


@router.get('/api/v1/video-owner-acceptance')
def owner_acceptance(request: Request):
    """Owner-only status of an actual on-server Kokoro/FFmpeg acceptance sample."""
    _, receipt = owner_acceptance_record(request)
    if not receipt:
        return {'state': 'not_run', 'sample_only': True, 'download_url': None}
    return {
        'state': 'passed',
        'checked_at': receipt.get('checked_at'),
        'video_size_bytes': receipt.get('video_size_bytes'),
        'sample_only': True,
        'download_url': '/api/v1/video-owner-acceptance/mp4',
    }


@router.get('/api/v1/video-owner-acceptance/mp4')
def owner_acceptance_mp4(request: Request):
    """Review a genuine hosted compositor sample; never a customer video."""
    directory, receipt = owner_acceptance_record(request)
    if not receipt:
        raise HTTPException(404, 'No verified hosted acceptance sample is available.')
    return FileResponse(directory / 'real-kokoro.mp4', media_type='video/mp4',
                        filename='trafficlift-owner-acceptance.mp4',
                        headers={'Cache-Control': 'private, no-store'})



class ResearchKeyRequest(BaseModel):
    api_key: SecretStr


@router.get('/api/v1/research-connection')
def get_research_connection(request: Request):
    guard(request)
    from backend.research_config import research_status
    return research_status()


@router.post('/api/v1/research-connection')
def set_research_connection(payload: ResearchKeyRequest, request: Request):
    guard(request)
    from backend.research_config import save_research_key
    try:
        return save_research_key(payload.api_key.get_secret_value())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except OSError:
        raise HTTPException(503, 'Could not save the research connection. Check that TrafficLift can write to its own folder.')


@router.get('/api/v1/product-image')
def product_image(url:str,request:Request):
    """Same-origin, validated product bytes for canvas pin export."""
    guard(request)
    import hashlib
    try:
        if len(url)>2048:raise ValueError('Image URL is too long.')
        _validate_public_https_url(url)
        directory=storage()/'pin-images'/hashlib.sha256(url.encode()).hexdigest()
        directory.mkdir(parents=True,exist_ok=True)
        with pin_image_lock:
            files=[directory/('product'+suffix) for suffix in ('.jpg','.png','.webp')]
            cached=next((file for file in files if file.is_file()),None)
            if cached is None:cached=_download_product_image(url,directory)
        return FileResponse(cached,headers={'Cache-Control':'private, max-age=3600'})
    except (ValueError,OSError) as exc:
        raise HTTPException(422,'The product photo could not be retrieved safely.') from exc

pin_image_lock=threading.Lock()
