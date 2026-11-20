"""YouTube adapter. Only approved durable jobs can transfer existing media."""
import hashlib
import os
import re
import time
from urllib.parse import urlsplit

import requests
from fastapi import HTTPException

from backend import publishing_store as store
from backend.photo_video_api import storage

API = 'https://www.googleapis.com/youtube/v3/'
TOKEN = 'https://oauth2.googleapis.com/token'
SCOPES = {'https://www.googleapis.com/auth/youtube.upload',
          'https://www.googleapis.com/auth/youtube.readonly'}
CHUNK = 8 * 1024 * 1024  # Multiple of Google's required 256 KiB chunk size.


def configured():
    return os.getenv('TRAFFICLIFT_YOUTUBE_ENABLED') == '1' and all(os.getenv(name) for name in (
        'YOUTUBE_CLIENT_ID', 'YOUTUBE_CLIENT_SECRET', 'YOUTUBE_REDIRECT_URI', 'TRAFFICLIFT_PUBLISHING_KEY'))


def oauth_config():
    if not configured():
        raise HTTPException(503, 'YouTube connection is awaiting server setup.')
    store.cipher()
    uri = urlsplit(os.environ['YOUTUBE_REDIRECT_URI'])
    if (uri.scheme != 'https' or uri.path != '/api/v1/publishing/youtube/callback'
            or uri.query or uri.fragment or uri.username):
        raise HTTPException(503, 'YouTube callback must be configured with the official HTTPS address.')
    return {'client_id': os.environ['YOUTUBE_CLIENT_ID'],
            'client_secret': os.environ['YOUTUBE_CLIENT_SECRET'],
            'redirect_uri': os.environ['YOUTUBE_REDIRECT_URI']}


def call(method, url, **kwargs):
    """No HTTP retries, redirects, response-body logging or raw provider errors."""
    try:
        return requests.request(method, url, timeout=(10, 45), allow_redirects=False, **kwargs)
    except requests.RequestException as exc:
        raise HTTPException(502, 'YouTube could not be reached. Check upload status before retrying.') from exc


def data(response):
    if response.status_code not in (200, 201):
        if response.status_code in (401, 403):
            raise HTTPException(409, 'YouTube authorization or permission requires attention. Reconnect the channel.')
        raise HTTPException(502, 'YouTube rejected the request. Check account eligibility and API access.')
    try:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except ValueError as exc:
        raise HTTPException(502, 'YouTube returned an unreadable response.') from exc


def credentials(grant_id=None):
    # Serialize refresh and disconnect so a late refresh cannot restore a removed grant.
    with store.database() as db:
        row = db.execute("SELECT payload FROM records WHERE kind='youtube' AND id='owner'").fetchone()
        connection = store.unseal(row['payload']) if row else None
        if not connection or (grant_id and connection['grant_id'] != grant_id):
            raise HTTPException(409, 'Reconnect and review the intended YouTube channel.')
        if connection['expires_at'] < time.time() + 90:
            result = data(call('POST', TOKEN, data={**oauth_config(), 'grant_type': 'refresh_token',
                                                   'refresh_token': connection['refresh_token']}))
            if not result.get('access_token'):
                raise HTTPException(409, 'Reconnect YouTube to renew access.')
            if 'scope' in result and not SCOPES.issubset(set(result['scope'].split())):
                raise HTTPException(409, 'YouTube permissions were reduced. Reconnect the channel.')
            connection.update(access_token=result['access_token'],
                              expires_at=time.time() + int(result.get('expires_in', 3600)))
            if result.get('refresh_token'):
                connection['refresh_token'] = result['refresh_token']
            db.execute("UPDATE records SET payload=? WHERE kind='youtube' AND id='owner'", (store.seal(connection),))
    return connection


def api_get(resource, connection, **params):
    return data(call('GET', API + resource, params=params,
                     headers={'Authorization': 'Bearer ' + connection['access_token']}))


def channels(connection):
    result = api_get('channels', connection, part='snippet,status', mine='true', maxResults=50)
    return [{'id': item['id'], 'title': item['snippet']['title'],
             'uploads_status': item.get('status', {}).get('longUploadsStatus', 'unknown')}
            for item in result.get('items', [])]


def session_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname != 'www.googleapis.com'
            or parsed.port not in (None, 443) or parsed.username or parsed.fragment
            or not parsed.path.startswith('/upload/youtube/')):
        raise HTTPException(502, 'YouTube returned an invalid upload session.')
    return value


def event(job, state, message=''):
    job.update(state=state, message=message)
    job['events'] = (job['events'] + [{'at': time.time(), 'state': state, 'message': message}])[-100:]
    store.save_job(job)


def metadata(snapshot):
    return {'snippet': {'title': snapshot['title'], 'description': snapshot['description'], 'categoryId': '22'},
            'status': {'privacyStatus': snapshot['visibility'],
                       'selfDeclaredMadeForKids': snapshot['made_for_kids'],
                       'containsSyntheticMedia': snapshot['synthetic_media']}}


def verify(job, connection):
    result = api_get('videos', connection, part='snippet,status,processingDetails', id=job['video_id'])
    items = result.get('items', [])
    if len(items) != 1:
        event(job, 'uploaded', 'Upload ID saved. YouTube verification is pending.')
        return
    item = items[0]
    status = item.get('status', {})
    snippet = item.get('snippet', {})
    actual = status.get('privacyStatus')
    job['actual_visibility'] = actual
    job['processing_status'] = item.get('processingDetails', {}).get('processingStatus', 'unknown')
    job['verified_at'] = time.time()
    expected = job['snapshot']
    if (item.get('id') != job['video_id'] or snippet.get('channelId') != expected['channel_id']
            or snippet.get('title') != expected['title'] or snippet.get('description') != expected['description']
            or status.get('selfDeclaredMadeForKids') is not expected['made_for_kids']
            or status.get('containsSyntheticMedia', False) is not expected['synthetic_media']):
        event(job, 'needs_attention', 'Uploaded video metadata differs from the approved review. Inspect YouTube Studio.')
    elif status.get('uploadStatus') in {'failed', 'rejected', 'deleted'} or job['processing_status'] in {'failed', 'terminated'}:
        event(job, 'failed', 'YouTube could not process this upload. Inspect YouTube Studio.')
    elif job['processing_status'] != 'succeeded' or status.get('uploadStatus') != 'processed':
        event(job, 'processing', 'YouTube is processing the uploaded video.')
    elif actual in {'private', 'unlisted', 'public'}:
        message = '' if actual == expected['visibility'] else 'YouTube applied a different visibility. Inspect account restrictions in YouTube Studio.'
        event(job, actual, message)
    else:
        event(job, 'uploaded', 'Upload saved. Actual visibility is awaiting verification.')


def accept_response(job, response, total):
    if response.status_code in (200, 201):
        value = data(response)
        video_id = value.get('id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise HTTPException(502, 'YouTube upload identity is awaiting reconciliation.')
        job['video_id'] = video_id
        job['offset'] = total
        event(job, 'uploaded')
        return total
    if response.status_code == 308:
        match = re.fullmatch(r'bytes=0-(\d+)', response.headers.get('Range', ''))
        offset = int(match.group(1)) + 1 if match else 0
        if offset > total:
            raise HTTPException(502, 'YouTube returned an invalid upload offset.')
        job['offset'] = offset
        store.save_job(job)
        return offset
    if response.status_code in (404, 410):
        event(job, 'needs_attention', 'Upload session expired. Check channel uploads before creating another publication.')
        return None
    data(response)


def process_job(identifier):
    """One bounded queue attempt. Persist the session before transferring bytes.

    A lost final response is probed using the same session on the next attempt.
    Never initiate a second upload when completion cannot be established.
    """
    job = store.claim(identifier)
    if not job:
        return
    try:
        if not configured() or not job.get('approved_at'):
            raise HTTPException(409, 'Publishing approval or configuration is unavailable.')
        snapshot = job['snapshot']
        connection = credentials(snapshot['grant_id'])
        if connection.get('channel_id') != snapshot['channel_id']:
            raise HTTPException(409, 'Connected channel changed. Review the intended account.')
        if job.get('video_id'):
            verify(job, connection)
            return
        media = storage() / 'publishing' / 'media' / (snapshot['media_hash'] + '.mp4')
        if not media.is_file():
            event(job, 'needs_attention', 'Approved media is missing or changed. Upload was stopped.')
            return
        with media.open('rb') as original:
            if hashlib.file_digest(original, 'sha256').hexdigest() != snapshot['media_hash']:
                event(job, 'needs_attention', 'Approved media is missing or changed. Upload was stopped.')
                return
        total = media.stat().st_size
        headers = {'Authorization': 'Bearer ' + connection['access_token']}
        if not job.get('session'):
            if job.get('session_started'):
                event(job, 'needs_attention', 'Upload initiation was interrupted. Reconcile the channel before trying again.')
                return
            # Verify the token still targets the explicitly selected channel.
            if snapshot['channel_id'] not in {item['id'] for item in channels(connection)}:
                raise HTTPException(409, 'Selected channel is no longer authorized.')
            job['session_started'] = True
            event(job, 'uploading')
            response = call('POST', 'https://www.googleapis.com/upload/youtube/v3/videos',
                            params={'uploadType': 'resumable', 'part': 'snippet,status'},
                            headers={**headers, 'X-Upload-Content-Length': str(total),
                                     'X-Upload-Content-Type': 'video/mp4'}, json=metadata(snapshot))
            if response.status_code not in (200, 201):
                data(response)
            job['session'] = session_url(response.headers.get('Location', ''))
            store.save_job(job)
        session = session_url(job['session'])
        # Always query the server offset, including after process restarts.
        response = call('PUT', session, headers={**headers, 'Content-Length': '0', 'Content-Range': f'bytes */{total}'}, data=b'')
        offset = accept_response(job, response, total)
        if offset is None:
            return
        with media.open('rb') as source:
            while offset < total and not job.get('video_id'):
                # Refresh between chunks if needed and stop after disconnect.
                connection = credentials(snapshot['grant_id'])
                source.seek(offset)
                chunk = source.read(CHUNK)
                response = call('PUT', session, headers={
                    'Authorization': 'Bearer ' + connection['access_token'],
                    'Content-Type': 'video/mp4', 'Content-Length': str(len(chunk)),
                    'Content-Range': f'bytes {offset}-{offset + len(chunk) - 1}/{total}'}, data=chunk)
                next_offset = accept_response(job, response, total)
                if next_offset is None:
                    return
                if next_offset <= offset:
                    raise HTTPException(502, 'Upload paused. YouTube status will be reconciled before resuming.')
                offset = next_offset
        if job.get('video_id'):
            verify(job, connection)
        else:
            event(job, 'reconciling', 'All bytes received. Waiting for the confirmed video ID.')
    except HTTPException as exc:
        # Bound automatic retries, while preserving the session and immutable approval.
        job['attempts'] = job.get('attempts', 0) + 1
        state = 'needs_attention' if exc.status_code in (409, 503) or job['attempts'] >= 5 else 'reconciling'
        event(job, state, exc.detail)
    except Exception:
        # Never expose raw requests exceptions (they can contain bearer session URLs).
        event(job, 'needs_attention', 'Publishing was interrupted. Check the saved upload before retrying.')
    finally:
        job['next_attempt'] = time.time() + min(300, 15 * 2 ** min(job.get('attempts', 0), 4))
        store.save_job(job, release=True)


def public_job(job):
    value = {key: job.get(key) for key in ('id', 'state', 'snapshot', 'media_id', 'review_hash', 'approved_at',
             'video_id', 'actual_visibility', 'processing_status', 'verified_at', 'message', 'events')}
    value['snapshot'] = {key: val for key, val in job['snapshot'].items() if key != 'grant_id'}
    value['public_url'] = ('https://www.youtube.com/watch?v=' + job['video_id']
                           if job['state'] == 'public' and job.get('actual_visibility') == 'public' else None)
    value['studio_url'] = ('https://studio.youtube.com/video/' + job['video_id'] + '/edit'
                           if job.get('video_id') else None)
    return value
