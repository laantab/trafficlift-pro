"""Opt-in loopback studio, persistent job records and a single CPU worker."""
from pathlib import Path
import json, os, re, threading, time, uuid
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from backend.photo_video import dependencies, render
from backend.sales_script import build_sales_plan
from local_video.trafficlift_video_bridge import _download_product_image, _validate_public_https_url

router=APIRouter()
lock=threading.Lock()
ROOT=Path(__file__).resolve().parents[1]

def storage():return Path(os.environ.get('TRAFFICLIFT_VIDEO_DIR', str(ROOT/'video/studio'))).resolve()

def guard(request):
    if os.environ.get('TRAFFICLIFT_FREE_VIDEO')!='1':raise HTTPException(503,'Free video studio is not enabled here. Open Start_Free_Video_Studio.bat on your PC.')
    if not request.client or request.client.host not in {'127.0.0.1','::1','localhost','testclient'}:raise HTTPException(403,'Free studio is available only on this computer.')
    if request.url.hostname not in {'127.0.0.1','localhost','testserver'}:raise HTTPException(403,'Open the studio at its localhost address.')
    origin=request.headers.get('origin')
    if origin and origin!=f'{request.url.scheme}://{request.url.netloc}':raise HTTPException(403,'Open the free studio on this computer to render videos.')

class VideoRequest(BaseModel):
    name:str=Field(min_length=1,max_length=90)
    image_url:str=Field(max_length=2048)
    product_url:str=Field(max_length=2048)
    benefit:str=Field(default='',max_length=160)
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
    return storage()/job_id/'job.json'

def read_record(job_id):
    path=record_path(job_id)
    if not path.is_file():raise HTTPException(404,'Video not found.')
    try:data=json.loads(path.read_text())
    except (OSError,ValueError):raise HTTPException(503,'Video record temporarily unavailable.')
    # Interrupted jobs never appear finished after a restart.
    if data['status'] not in {'succeeded','failed'} and not lock.locked():
        data.update(status='failed',message='Render was interrupted. Start a new video.')
    return data

def save(record,directory):
    tmp=directory/'job.tmp';tmp.write_text(json.dumps(record,indent=2));tmp.replace(directory/'job.json')

def worker(record,payload,directory):
    def update(message):
        record.update(status='running',message=message);save(record,directory)
    try:
        update('Getting photo')
        image=_download_product_image(payload.image_url,directory)
        out=render(image,directory,payload.name,payload.benefit,payload.product_url,payload.seconds,payload.style,update,sales_plan=record['sales_plan'])
        record.update(status='succeeded',message='Video checked and ready',video_url=f"/api/v1/photo-videos/{record['id']}/video")
    except Exception as exc:
        record.update(status='failed',message=str(exc)[-500:],video_url=None)
    finally:
        try:save(record,directory)
        finally:lock.release()

@router.get('/studio',response_class=HTMLResponse)
def studio(request:Request):
    guard(request)
    html=(ROOT/'index.html').read_text(encoding='utf-8')
    html=re.sub(r'<meta name="api-base" content="[^"]*">','<meta name="api-base" content="">',html)
    return HTMLResponse(html,headers={'Cache-Control':'no-store'})

@router.get('/api/v1/photo-videos')
def history(request:Request):
    guard(request)
    records=[]
    for path in sorted(storage().glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
        records.append(read_record(path.parent.name))
    return {'videos':records}

@router.post('/api/v1/photo-videos',status_code=202)
def create(payload:VideoRequest,request:Request):
    guard(request)
    if not payload.name.strip():raise HTTPException(400,'Product name is required.')
    try:
        sales_plan=build_sales_plan(payload.name,payload.benefit,payload.product_url,payload.seconds,payload.buyer_need,payload.consideration)
    except ValueError as exc:raise HTTPException(422,str(exc))
    try:dependencies()
    except ValueError as exc:raise HTTPException(503,str(exc))
    root=storage();root.mkdir(parents=True,exist_ok=True)
    if len(list(root.glob('*/job.json')))>=100:raise HTTPException(409,'Studio has 100 videos. Back up and remove older folders before creating more.')
    import shutil
    if shutil.disk_usage(root).free<250*1024*1024:raise HTTPException(409,'Not enough disk space. Free at least 250 MB before rendering.')
    if not lock.acquire(blocking=False):raise HTTPException(409,'A video is already running. Wait for it to finish.')
    try:
        job_id=uuid.uuid4().hex;directory=root/job_id;directory.mkdir()
        record={'id':job_id,'name':payload.name,'created_at':time.time(),'seconds':payload.seconds,'status':'queued','message':'Getting photo','video_url':None,'product_url':payload.product_url,'sales_plan':sales_plan}
        save(record,directory)
        threading.Thread(target=worker,args=(record,payload,directory),daemon=True).start()
    except Exception:
        lock.release();raise
    return {'video':record.copy()}

@router.get('/api/v1/photo-videos/{job_id}')
def get_job(job_id:str,request:Request):
    guard(request);return {'video':read_record(job_id)}

@router.get('/api/v1/photo-videos/{job_id}/video')
def download(job_id:str,request:Request):
    guard(request);record=read_record(job_id)
    if record['status']!='succeeded':raise HTTPException(409,'This video has not passed its checks.')
    path=record_path(job_id).parent/'video.mp4'
    if not path.is_file():raise HTTPException(404,'Saved video file is missing.')
    slug=re.sub(r'[^a-zA-Z0-9_-]+','-',record['name']).strip('-')[:60] or 'product'
    return FileResponse(path,media_type='video/mp4',filename=f"{slug}-{job_id[:8]}.mp4")

