"""Server adapters with fixed API origins and no automatic retries of writes."""
import base64
import hashlib
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
import requests
from fastapi import HTTPException
from backend import pipeline_store as store
from backend import publishing_store as youtube_store
from backend import youtube_publisher as youtube
from backend.photo_video_api import storage


def capabilities():
    yt = youtube_store.get('youtube', 'owner') if youtube.configured() else None
    return {
        'pinterest': {'configured': all(os.getenv(k) for k in ('PINTEREST_ACCESS_TOKEN', 'PINTEREST_USERNAME', 'PINTEREST_BOARD_ID')),
                      'account': os.getenv('PINTEREST_USERNAME', ''),
                      'reason': 'Configure the server Pinterest token, username and board ID.'},
        'youtube': {'configured': bool(yt and yt.get('channel_id')), 'account': (yt or {}).get('channel_id', ''),
                    'reason': 'Connect YouTube through Launch Studio.'},
        'tiktok': {'configured': all(os.getenv(k) for k in ('TIKTOK_ACCESS_TOKEN', 'TIKTOK_USERNAME')),
                   'account': os.getenv('TIKTOK_USERNAME', ''),
                   'reason': 'Configure an audited TikTok Direct Post API connection and account.'},
        'instagram': {'configured': False, 'account': '', 'reason': 'Instagram remains deferred by the owner.'}}


def asset_path(asset):
    identifier = asset['id']
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise HTTPException(422, 'Invalid asset.')
    return storage() / 'pipeline' / 'assets' / identifier


def asset_hash(asset):
    return hashlib.sha256(asset_path(asset).read_bytes()).hexdigest()


def request(method, url, token=None, **kwargs):
    headers = kwargs.pop('headers', {})
    if token:
        headers['Authorization'] = 'Bearer ' + token
    reply = requests.request(method, url, headers=headers, timeout=(10, 45), allow_redirects=False, **kwargs)
    if reply.status_code not in {200, 201, 206}:
        raise HTTPException(502, 'Platform request failed. Inspect the publication before retrying.')
    result = reply.json() if reply.content else {}
    if result.get('error', {}).get('code') not in (None, 'ok'):
        raise HTTPException(409, 'Platform permission or posting eligibility requires attention.')
    return result


def pin(method, path, **kwargs):
    return request(method, 'https://api.pinterest.com/v5/' + path, os.environ['PINTEREST_ACCESS_TOKEN'], **kwargs)


def tik(path, **kwargs):
    return request('POST', 'https://open.tiktokapis.com/v2/' + path, os.environ['TIKTOK_ACCESS_TOKEN'], **kwargs).get('data', {})


def publish(post, campaign, asset):
    platform = post['platform']
    if platform == 'pinterest':
        profile = pin('GET', 'user_account')
        if profile.get('username') != post['account']:
            raise HTTPException(409, 'Pinterest account does not match the configured destination.')
        board = pin('GET', 'boards/' + os.environ['PINTEREST_BOARD_ID'])
        if board.get('owner', {}).get('username') != post['account'] or board.get('privacy') != 'PUBLIC':
            raise HTTPException(409, 'Select a public Pinterest board owned by this account.')
        result = pin('POST', 'pins', json={'board_id': board['id'], 'title': post['snapshot']['title'],
                     'description': post['snapshot']['description'][:800], 'link': campaign['marketing_url'],
                     'alt_text': campaign['title'], 'media_source': {'source_type': 'image_base64',
                     'content_type': asset['content_type'], 'data': base64.b64encode(asset_path(asset).read_bytes()).decode()}})
        post.update(remote_id=result['id'], state='submitted')
        store.save_post(post)
        return reconcile(post)
    if platform == 'youtube':
        from backend.pipeline import copy_for
        connection = youtube.credentials()
        if connection.get('channel_id') != post['account']:
            raise HTTPException(409, 'YouTube account changed.')
        root = storage() / 'publishing' / 'media'
        root.mkdir(parents=True, exist_ok=True)
        target = root / (asset['sha256'] + '.mp4')
        if not target.exists():
            target.write_bytes(asset_path(asset).read_bytes())
        product = {'id': campaign['id'], 'title': campaign['title'], 'product_url': campaign['product_url']}
        snapshot = {**copy_for(campaign, post['variant']), 'campaign_id': campaign['id'],
                    'channel_id': post['account'], 'channel_title': next(c['title'] for c in connection['channels'] if c['id'] == post['account']),
                    'product': product, 'media_hash': asset['sha256'], 'technical': asset['technical'],
                    'marketing_url': campaign['marketing_url'], 'visibility': 'public',
                    'made_for_kids': False, 'synthetic_media': asset.get('synthetic_media', False), 'grant_id': connection['grant_id']}
        review_hash = hashlib.sha256(__import__('json').dumps(snapshot, sort_keys=True).encode()).hexdigest()
        value = {'id': post['id'], 'platform': 'youtube', 'state': 'review', 'snapshot': snapshot,
                 'review_hash': review_hash, 'media_id': asset['sha256'], 'events': []}
        dedupe = hashlib.sha256((asset['sha256'] + ':' + post['account']).encode()).hexdigest()
        job = youtube_store.create_job(value, dedupe)
        # Persist correlation before arming the existing resumable worker.
        post.update(remote_id=job['id'], state='submitted')
        store.save_post(post)
        youtube_store.approve(job['id'], review_hash)
        return {'state': 'processing', 'remote_id': job['id']}
    if platform == 'tiktok':
        creator = tik('post/publish/creator_info/query/', json={})
        if creator.get('creator_username') != post['account'] or 'PUBLIC_TO_EVERYONE' not in creator.get('privacy_level_options', []):
            raise HTTPException(409, 'TikTok public posting is unavailable for the selected account/app.')
        if asset['technical']['duration'] > creator.get('max_video_post_duration_sec', 0):
            raise HTTPException(422, 'Video exceeds creator duration limit.')
        size = asset_path(asset).stat().st_size
        result = tik('post/publish/video/init/', json={'post_info': {'title': post['snapshot']['description'][:2200],
                     'privacy_level': 'PUBLIC_TO_EVERYONE', 'disable_comment': bool(creator.get('comment_disabled')),
                     'disable_duet': True, 'disable_stitch': True, 'brand_content_toggle': True,
                     'brand_organic_toggle': False, 'is_aigc': asset.get('synthetic_media', False)},
                     'source_info': {'source': 'FILE_UPLOAD', 'video_size': size, 'chunk_size': size, 'total_chunk_count': 1}})
        upload = result['upload_url']
        parsed = urlsplit(upload)
        if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.tiktokapis.com') or parsed.username or parsed.port not in (None, 443):
            raise HTTPException(502, 'Unexpected TikTok upload address.')
        post.update(remote_id=result['publish_id'], state='needs_attention',
                    message='Upload initiated; if interrupted, check remote status before further action.')
        store.save_post(post)
        # At most 60 MB, one chunk. Never retry a partially completed upload.
        reply = requests.put(upload, data=asset_path(asset).read_bytes(), headers={'Content-Type': 'video/mp4',
                             'Content-Length': str(size), 'Content-Range': f'bytes 0-{size-1}/{size}'},
                             timeout=(10, 90), allow_redirects=False)
        if reply.status_code not in (200, 201):
            raise HTTPException(502, 'TikTok upload requires reconciliation.')
        return {'remote_id': result['publish_id'], 'state': 'processing'}
    raise HTTPException(409, 'Platform is not enabled.')


def reconcile(post):
    if post['platform'] == 'youtube':
        job = youtube_store.job(post['remote_id'])
        if job['state'] == 'review':
            # A crash between correlation persistence and local queue approval
            # has provably not started a remote upload. Arm that same job only.
            youtube_store.approve(job['id'], job['review_hash'])
            return {'state': 'processing'}
        if job['state'] == 'public' and job.get('actual_visibility') == 'public':
            return {'state': 'published', 'remote_video_id': job['video_id'], 'url': youtube.public_job(job)['public_url'],
                    'published_at': time.time(), 'visibility': 'public'}
        if job['state'] in {'needs_attention', 'failed', 'private', 'unlisted'}:
            return {'state': 'needs_attention', 'message': 'YouTube upload requires review; inspect Launch Studio.'}
        return {'state': 'processing'}
    if post['platform'] == 'pinterest':
        result = pin('GET', 'pins/' + post['remote_id'])
        if result.get('board_owner', {}).get('username') != post['account'] or result.get('link') != post['snapshot']['marketing_url']:
            raise HTTPException(409, 'Published pin account or destination differs from the prepared campaign.')
        if result.get('title') != post['snapshot']['title'] or result.get('description') != post['snapshot']['description'][:800]:
            raise HTTPException(409, 'Published pin copy differs from the prepared campaign.')
        board = pin('GET', 'boards/' + result['board_id'])
        if board.get('privacy') != 'PUBLIC' or board.get('owner', {}).get('username') != post['account']:
            raise HTTPException(409, 'Pinterest board is not public for this account.')
        return {'state': 'published', 'url': 'https://www.pinterest.com/pin/' + post['remote_id'] + '/',
                'visibility': 'public', 'published_at': time.time()}
    result = tik('post/publish/status/fetch/', json={'publish_id': post['remote_id']})
    if result.get('status') == 'FAILED':
        return {'state': 'needs_attention', 'message': 'TikTok processing failed; inspect remote status.'}
    ids = result.get('publicly_available_post_id', [])
    if result.get('status') == 'PUBLISH_COMPLETE' and ids:
        return {'state': 'published', 'remote_video_id': str(ids[0]), 'published_at': time.time(), 'visibility': 'public',
                'url': f'https://www.tiktok.com/@{post["account"]}/video/{ids[0]}'}
    return {'state': 'processing'}


def metrics(post):
    observed = time.time()
    start = datetime.fromtimestamp(post.get('published_at', observed), timezone.utc).date().isoformat()
    end = datetime.fromtimestamp(observed, timezone.utc).date().isoformat()
    if post['platform'] == 'pinterest':
        value = pin('GET', 'pins/' + post['remote_id'] + '/analytics', params={'start_date': start, 'end_date': end,
                    'metric_types': 'IMPRESSION,OUTBOUND_CLICK,PIN_CLICK,SAVE'})
        all_data = value.get('all', {})
        daily = all_data.get('daily_metrics', [])
        pending = not daily or any(d.get('data_status') != 'READY' for d in daily)
        summary = all_data.get('summary_metrics', {})
        measured = {target: summary.get(key) for key, target in [('IMPRESSION', 'impressions'),
                    ('OUTBOUND_CLICK', 'outbound_clicks'), ('PIN_CLICK', 'pin_clicks'), ('SAVE', 'saves')]}
        return {'source': 'pinterest', 'start': start, 'end': end, 'observed_at': observed,
                'status': 'processing' if pending else 'ready',
                'metrics': {k: None for k in measured} if pending else measured}
    if post['platform'] == 'youtube':
        value = youtube.api_get('videos', youtube.credentials(), part='statistics', id=post['remote_video_id'])
        stats = next(iter(value.get('items', [])), {}).get('statistics', {})
        return {'source': 'youtube', 'start': 'lifetime', 'end': end, 'observed_at': observed,
                'status': 'ready' if stats else 'unavailable', 'metrics': {
                    target: int(stats[key]) if key in stats else None for key, target in
                    [('viewCount', 'views'), ('likeCount', 'likes'), ('commentCount', 'comments')]}}
    result = request('POST', 'https://open.tiktokapis.com/v2/video/query/', os.environ['TIKTOK_ACCESS_TOKEN'],
                     params={'fields': 'id,view_count,like_count,comment_count,share_count'},
                     json={'filters': {'video_ids': [post['remote_video_id']]}})
    stats = next(iter(result.get('data', {}).get('videos', [])), {})
    return {'source': 'tiktok', 'start': 'lifetime', 'end': end, 'observed_at': observed,
            'status': 'ready' if stats else 'unavailable', 'metrics': {
                target: stats.get(key) for key, target in [('view_count', 'views'), ('like_count', 'likes'),
                ('comment_count', 'comments'), ('share_count', 'shares')]}}
