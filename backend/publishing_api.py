"""Owner review-first publishing API. No generation-provider calls."""
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from typing import Literal
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from backend import publishing_store as store
from backend import youtube_publisher as youtube
from backend.photo_video_api import storage, video_guard
from backend.social_exports_api import _ffmpeg, _marketing_url

router = APIRouter(prefix='/api/v1/publishing')
MAX_MEDIA = 90 * 1024 * 1024
_stop = threading.Event()
_worker = None


def guard(request, mutation=False):
    video_guard(request)
    if mutation and request.headers.get('origin', '').rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Open publishing from the TrafficLift website.')
    if not youtube.configured():
        raise HTTPException(503, 'YouTube publishing is awaiting server setup.')
    youtube.oauth_config()


@router.on_event('startup')
def start_worker():
    global _worker
    if youtube.configured():
        _stop.clear()
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=queue_loop, daemon=True, name='publishing-queue')
            _worker.start()


@router.on_event('shutdown')
def stop_worker():
    _stop.set()


def queue_loop():
    while not _stop.wait(5):
        try:
            revoke_pending()
            for job in store.pending_jobs():
                if _stop.is_set():
                    return
                if (job['state'] in {'queued', 'uploading', 'reconciling', 'uploaded', 'processing'}
                        and job.get('next_attempt', 0) <= time.time()):
                    youtube.process_job(job['id'])
        except Exception:
            # Credentials and raw HTTP response bodies must never enter logs.
            continue


@router.get('/status')
def status(request: Request):
    video_guard(request)
    ready = youtube.configured()
    connection = store.get('youtube', 'owner') if ready else None
    return JSONResponse({'configured': ready, 'connected': bool(connection),
        'channel_id': connection.get('channel_id') if connection else None,
        'channels': connection.get('channels', []) if connection else []},
        headers={'Cache-Control': 'private, no-store'})


@router.post('/youtube/connect')
def connect(request: Request):
    guard(request, True)
    config = youtube.oauth_config()
    if str(request.base_url).rstrip('/') != config['redirect_uri'].split('/api/')[0]:
        raise HTTPException(503, 'Open TrafficLift at its configured YouTube address.')
    state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    # Expiring states are removed without ever retaining plaintext state or browser cookies.
    with store.database() as db:
        for row in db.execute("SELECT id,payload FROM records WHERE kind='oauth'").fetchall():
            if store.unseal(row['payload'])['expires_at'] < time.time():
                db.execute("DELETE FROM records WHERE kind='oauth' AND id=?", (row['id'],))
    with store.database() as db:
        db.execute("INSERT INTO records VALUES('settings','oauth_generation',?) "
                   'ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload', (store.seal(state_hash),))
        db.execute("INSERT INTO records VALUES('oauth',?,?)", (state_hash, store.seal({
            'browser': hashlib.sha256(browser.encode()).hexdigest(), 'verifier': verifier,
            'generation': state_hash, 'expires_at': time.time() + 600})))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': config['client_id'], 'redirect_uri': config['redirect_uri'],
        'response_type': 'code', 'scope': ' '.join(sorted(youtube.SCOPES)),
        'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
        'access_type': 'offline', 'prompt': 'consent', 'include_granted_scopes': 'true'})
    response = JSONResponse({'authorization_url': url}, headers={'Cache-Control': 'no-store'})
    response.set_cookie('trafficlift_youtube_oauth', browser, httponly=True, secure=True,
                        samesite='lax', max_age=600, path='/api/v1/publishing/youtube/callback')
    return response


@router.get('/youtube/callback')
def callback(request: Request, state: str = '', code: str = '', error: str = ''):
    # Avoid logging the authorization code in the ASGI access log query string.
    request.scope['query_string'] = b''
    request.scope['raw_path'] = request.url.path.encode()
    config = youtube.oauth_config()
    if not state or len(state) > 128:
        raise HTTPException(400, 'YouTube connection request expired. Connect again.')
    pending = store.pop('oauth', hashlib.sha256(state.encode()).hexdigest())
    browser = request.cookies.get('trafficlift_youtube_oauth', '')
    if (not pending or pending['expires_at'] < time.time() or not browser
            or not secrets.compare_digest(pending['browser'], hashlib.sha256(browser.encode()).hexdigest())):
        raise HTTPException(400, 'YouTube connection request expired or belongs to another browser.')
    if store.get('settings', 'oauth_generation') != pending['generation']:
        raise HTTPException(409, 'YouTube connection was superseded or disconnected. Connect again.')
    if error or not code or len(code) > 4096:
        raise HTTPException(400, 'YouTube connection was declined. Connect again when ready.')
    tokens = youtube.data(youtube.call('POST', youtube.TOKEN, data={**config, 'grant_type': 'authorization_code',
                           'code': code, 'code_verifier': pending['verifier']}))
    if (not youtube.SCOPES.issubset(set(tokens.get('scope', '').split()))
            or not tokens.get('refresh_token') or not tokens.get('access_token')):
        raise HTTPException(409, 'YouTube upload and channel-read permissions are required. Reconnect and grant both.')
    connection = {'grant_id': uuid.uuid4().hex, 'access_token': tokens['access_token'],
                  'refresh_token': tokens['refresh_token'], 'expires_at': time.time() + int(tokens.get('expires_in', 3600))}
    available = youtube.channels(connection)
    if not available:
        youtube.call('POST', 'https://oauth2.googleapis.com/revoke', data={'token': tokens['refresh_token']})
        raise HTTPException(409, 'No YouTube channel was found for this account.')
    connection['channels'] = available
    connection['channel_id'] = None  # Owner must explicitly select the verified channel.
    with store.database() as db:
        row = db.execute("SELECT payload FROM records WHERE kind='settings' AND id='oauth_generation'").fetchone()
        current = store.unseal(row['payload']) if row else None
        if current == pending['generation']:
            db.execute("INSERT INTO records VALUES('youtube','owner',?) "
                       'ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload', (store.seal(connection),))
        else:
            db.execute("INSERT INTO records VALUES('revocation',?,?)", (connection['grant_id'],
                       store.seal({'refresh_token': connection['refresh_token']})))
    if current != pending['generation']:
        raise HTTPException(409, 'YouTube connection was canceled. Connect again when ready.')
    response = RedirectResponse('/launch-studio?youtube=connected', status_code=303)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.delete_cookie('trafficlift_youtube_oauth', path='/api/v1/publishing/youtube/callback')
    return response


class ChannelSelection(BaseModel):
    channel_id: str = Field(min_length=1, max_length=128)


@router.post('/youtube/channel')
def select_channel(payload: ChannelSelection, request: Request):
    guard(request, True)
    connection = youtube.credentials()
    available = youtube.channels(connection)
    if payload.channel_id not in {channel['id'] for channel in available}:
        raise HTTPException(409, 'Choose a channel returned by your YouTube account.')
    connection.update(channel_id=payload.channel_id, channels=available)
    # Serialize selection with disconnect, refusing to resurrect a removed grant.
    with store.database() as db:
        row = db.execute("SELECT payload FROM records WHERE kind='youtube' AND id='owner'").fetchone()
        if not row or store.unseal(row['payload'])['grant_id'] != connection['grant_id']:
            raise HTTPException(409, 'Connection changed. Connect again.')
        current = store.unseal(row['payload'])
        current.update(channel_id=payload.channel_id, channels=available)
        db.execute("UPDATE records SET payload=? WHERE kind='youtube' AND id='owner'", (store.seal(current),))
    return {'channel_id': payload.channel_id}


@router.post('/youtube/disconnect')
def disconnect(request: Request):
    guard(request, True)
    with store.database() as db:
        row = db.execute("SELECT payload FROM records WHERE kind='youtube' AND id='owner'").fetchone()
        connection = store.unseal(row['payload']) if row else None
        if connection:
            # Remove the grant and retain revocation work in one durable transaction.
            db.execute("INSERT INTO records VALUES('revocation',?,?)", (connection['grant_id'],
                       store.seal({'refresh_token': connection['refresh_token']})))
        db.execute("DELETE FROM records WHERE kind IN ('youtube','oauth')")
        db.execute("INSERT INTO records VALUES('settings','oauth_generation',?) "
                   'ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload', (store.seal(uuid.uuid4().hex),))
    complete = revoke_pending()
    return {'connected': False, 'revoked': complete,
            'message': '' if complete else 'Disconnected locally. Retry disconnect to finish Google revocation, or remove TrafficLift in Google account permissions.'}


def revoke_pending():
    complete = True
    with store.database() as db:
        rows = db.execute("SELECT id,payload FROM records WHERE kind='revocation'").fetchall()
        for row in rows:
            try:
                response = youtube.call('POST', 'https://oauth2.googleapis.com/revoke',
                                        data={'token': store.unseal(row['payload'])['refresh_token']})
                if response.status_code == 200:
                    db.execute("DELETE FROM records WHERE kind='revocation' AND id=?", (row['id'],))
                else:
                    complete = False
            except HTTPException:
                complete = False
    return complete


def campaign(identifier):
    from trafficlift_pro import db
    value = db.get_by_id(identifier)
    if not value:
        raise HTTPException(404, 'Select a saved product campaign first.')
    return {'id': value['id'], 'title': value.get('product_title') or '', 'product_url': value['input_url']}


def inspect_media(path):
    _ffmpeg()
    probe = shutil.which('ffprobe')
    if not probe:
        raise HTTPException(503, 'Video inspection is unavailable.')
    try:
        result = subprocess.run([probe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                                capture_output=True, text=True, timeout=30, check=True)
        value = json.loads(result.stdout)
        videos = [stream for stream in value['streams'] if stream['codec_type'] == 'video']
        audio = [stream for stream in value['streams'] if stream['codec_type'] == 'audio']
        duration = float(value['format']['duration'])
        video = videos[0]
        if (len(videos) != 1 or not audio or audio[0]['codec_name'] != 'aac' or video['codec_name'] != 'h264'
                or not 0 < duration <= 180 or abs(video['width'] / video['height'] - 9 / 16) > .02
                or 'mp4' not in value['format']['format_name'].split(',')):
            raise ValueError()
        # Decode the original file, without transcoding or changing its identity.
        subprocess.run([_ffmpeg(), '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
                       capture_output=True, timeout=180, check=True)
        return {'duration': duration, 'width': video['width'], 'height': video['height'],
                'video_codec': 'h264', 'audio_codec': 'aac'}
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        raise HTTPException(422, 'Choose a playable 9:16 H.264 MP4 with AAC audio, up to three minutes.') from exc


@router.post('/media')
async def import_media(request: Request, campaign_id: str):
    guard(request, True)
    product = campaign(campaign_id)
    if request.headers.get('content-type', '').split(';')[0] != 'video/mp4':
        raise HTTPException(415, 'Select an existing MP4 video.')
    root = storage() / 'publishing' / 'media'
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / (uuid.uuid4().hex + '.part')
    digest, size = hashlib.sha256(), 0
    try:
        with temporary.open('wb') as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_MEDIA:
                    raise HTTPException(413, 'Choose an MP4 smaller than 90 MB.')
                digest.update(chunk)
                output.write(chunk)
        # Keep blocking FFmpeg work off the request event loop.
        from starlette.concurrency import run_in_threadpool
        technical = await run_in_threadpool(inspect_media, temporary)
        media_hash = digest.hexdigest()
        from backend.reviewed_footage import rejected_master
        if rejected_master(media_hash):
            raise HTTPException(422,"This master contains a previously rejected product demonstration.")
        target = root / (media_hash + '.mp4')
        if not target.exists():
            temporary.replace(target)
        identifier = hashlib.sha256((media_hash + product['id']).encode()).hexdigest()
        value = {'id': identifier, 'media_hash': media_hash, 'size': size, 'product': product, 'technical': technical}
        store.put('media', identifier, value)
        return value
    finally:
        temporary.unlink(missing_ok=True)


@router.get('/media/{identifier}')
def preview(identifier: str, request: Request):
    guard(request)
    media = store.get('media', identifier)
    if not media:
        raise HTTPException(404, 'Video not found.')
    return FileResponse(storage() / 'publishing' / 'media' / (media['media_hash'] + '.mp4'),
                        media_type='video/mp4', headers={'Cache-Control': 'private, no-store'})


class Review(BaseModel):
    model_config = ConfigDict(extra='forbid')
    media_id: str = Field(min_length=64, max_length=64)
    campaign_id: str = Field(min_length=1, max_length=64)
    channel_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=5000)
    marketing_url: str = Field(min_length=12, max_length=2048)
    visibility: Literal['private', 'unlisted', 'public']
    made_for_kids: StrictBool
    synthetic_media: StrictBool
    confirm_product_and_rights: Literal[True]
    confirm_video_and_copy: Literal[True]
    confirm_profile_link: Literal[True]
    confirm_not_previously_posted: Literal[True]

    @field_validator('title', 'description')
    @classmethod
    def valid_copy(cls, value):
        if not value.strip() or any(char in value for char in '<>') or any(ord(char) < 32 and char not in '\n\r\t' for char in value):
            raise ValueError('Use plain, nonempty YouTube copy.')
        return value


def asin(url):
    host = urlsplit(url).hostname or ''
    if not re.fullmatch(r'(?:www\.)?amazon\.(?:com|ca|co\.uk|com\.au|de|fr|es|it|co\.jp)', host, re.I):
        return None
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)', url, re.I)
    return match.group(1).upper() if match else None


@router.post('/jobs')
def create_review(payload: Review, request: Request):
    guard(request, True)
    connection = youtube.credentials()
    if connection.get('channel_id') != payload.channel_id:
        raise HTTPException(409, 'Review the currently selected YouTube channel.')
    media = store.get('media', payload.media_id)
    product = campaign(payload.campaign_id)
    if not media or media['product'] != product:
        raise HTTPException(409, 'The video belongs to a different or changed product. Select it again.')
    from backend.reviewed_footage import rejected_master
    if rejected_master(media['media_hash']):
        raise HTTPException(422,"This master contains a previously rejected product demonstration.")
    link = _marketing_url(payload.marketing_url)
    source_asin, target_asin = asin(product['product_url']), asin(link)
    if source_asin and target_asin and source_asin != target_asin:
        raise HTTPException(422, 'The Amazon link refers to a different product.')
    if link != payload.marketing_url or link not in payload.description:
        raise HTTPException(422, 'Include the exact approved product link in the description.')
    if re.search(r'link\s+(?:in|below|the)\s+(?:the\s+)?(?:description|caption)|click\s+(?:the\s+)?(?:link|below)', payload.description, re.I):
        raise HTTPException(422, 'Shorts description URLs are not clickable. Use an accurate channel-profile CTA.')
    snapshot = {**payload.model_dump(exclude={'media_id'}), 'product': product,
                'media_hash': media['media_hash'], 'technical': media['technical'],
                'channel_title': next(item['title'] for item in connection['channels'] if item['id'] == payload.channel_id),
                'grant_id': connection['grant_id']}
    review_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    value = {'id': uuid.uuid4().hex, 'platform': 'youtube', 'state': 'review', 'snapshot': snapshot,
             'review_hash': review_hash, 'media_id': payload.media_id, 'events': [{'at': time.time(), 'state': 'review'}]}
    # Stronger than metadata dedupe: the same bytes cannot silently repost even with new copy.
    dedupe = hashlib.sha256((media['media_hash'] + ':' + payload.channel_id).encode()).hexdigest()
    return youtube.public_job(store.create_job(value, dedupe))


class Approval(BaseModel):
    review_hash: str = Field(min_length=64, max_length=64)
    approve: Literal[True]


@router.post('/jobs/{identifier}/approve')
def approve(identifier: str, payload: Approval, request: Request):
    guard(request, True)
    job = store.job(identifier)
    connection = youtube.credentials(job.get('active_grant_id', job['snapshot']['grant_id']))
    if connection.get('channel_id') != job['snapshot']['channel_id']:
        raise HTTPException(409, 'The selected channel changed. Review the intended account.')
    return youtube.public_job(store.approve(identifier, payload.review_hash))


@router.post('/jobs/{identifier}/reconcile')
def reconcile(identifier: str, payload: Approval, request: Request):
    guard(request, True)
    job = store.job(identifier)
    if not job.get('approved_at') or job['review_hash'] != payload.review_hash:
        raise HTTPException(409, 'No approved upload session is available to reconcile. Inspect YouTube Studio.')
    connection = youtube.credentials()
    channel = job['snapshot']['channel_id']
    if connection.get('channel_id') != channel or channel not in {item['id'] for item in youtube.channels(connection)}:
        raise HTTPException(409, 'Reconnect and select the same verified channel before resuming this upload.')
    if job.get('session_started') and not (job.get('session') or job.get('video_id')):
        raise HTTPException(409, 'Upload initiation is uncertain. Inspect YouTube Studio before any new publication.')
    # Never create a new session to recover a possibly completed upload.
    with store.database() as db:
        row = db.execute('SELECT lease,payload FROM jobs WHERE id=?', (identifier,)).fetchone()
        if row['lease'] > time.time():
            return youtube.public_job(store.unseal(row['payload']))
        job = store.unseal(row['payload'])
        # Credential renewal may resume the same approved channel/session, but
        # never changes the immutable approved media, metadata or visibility.
        job.update(state='reconciling', attempts=0, next_attempt=0, active_grant_id=connection['grant_id'])
        job['events'].append({'at': time.time(), 'state': 'reconciling',
                              'message': 'Owner requested reconciliation for the verified channel.'})
        db.execute('UPDATE jobs SET state=?,payload=? WHERE id=?', ('reconciling', store.seal(job), identifier))
    return youtube.public_job(job)


@router.get('/jobs')
def history(request: Request):
    guard(request)
    return JSONResponse({'jobs': [youtube.public_job(job) for job in store.jobs()]},
                        headers={'Cache-Control': 'private, no-store'})


@router.get('/jobs/{identifier}')
def get_job(identifier: str, request: Request):
    guard(request)
    return JSONResponse(youtube.public_job(store.job(identifier)), headers={'Cache-Control': 'private, no-store'})


@router.get('/products')
def products(request: Request):
    guard(request)
    from trafficlift_pro import db
    return JSONResponse({'products': [{'id': row['id'], 'title': row.get('product_title') or row['input_url'],
                                      'product_url': row['input_url']} for row in db.list_history(limit=100)]},
                        headers={'Cache-Control': 'private, no-store'})
