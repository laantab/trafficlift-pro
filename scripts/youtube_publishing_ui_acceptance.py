import json
import tempfile
import subprocess
import shutil
from static_ffmpeg import add_paths
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
html = (root / 'static/launch-studio.html').read_text(encoding='utf-8')
channel = 'UC' + 'a' * 22
product = {'id': 'campaign-one', 'title': 'A Season for Everyone', 'product_url': 'https://www.amazon.com/dp/B0HQ84DS7S'}
state = {'job': None, 'approved': 0, 'imported': 0}
errors = []
temporary = tempfile.TemporaryDirectory()
add_paths()
fixture = Path(temporary.name) / 'owned.mp4'
subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=navy:s=90x160:r=24',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '0.5',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(fixture)], check=True, timeout=30)
media_bytes = fixture.read_bytes()


def route(request):
    path = request.request.url.split('https://trafficlift.test')[-1]
    if path == '/launch-studio':
        request.fulfill(status=200, content_type='text/html', body=html)
        return
    if path == '/api/v1/publishing/status':
        result = {'configured': True, 'connected': True, 'channel_id': channel,
                  'channels': [{'id': channel, 'title': 'Owner channel'}]}
    elif path == '/api/v1/publishing/products':
        result = {'products': [product]}
    elif path.startswith('/api/v1/publishing/media?'):
        state['imported'] += 1
        result = {'id': 'm' * 64}
    elif path == '/api/v1/publishing/jobs' and request.request.method == 'POST':
        payload = request.request.post_data_json
        state['job'] = {'id': 'job-one', 'state': 'review', 'review_hash': 'h'*64,
                        'media_id': payload['media_id'], 'snapshot': {**payload, 'product': product,
                        'channel_title': 'Owner channel', 'technical': {'width': 720, 'height': 1280, 'duration': 15}},
                        'events': [{'at': 1, 'state': 'review'}]}
        result = state['job']
    elif path == '/api/v1/publishing/jobs/job-one/approve':
        state['approved'] += 1
        state['job'].update(state='private', actual_visibility='private', approved_at=2,
            video_id='abc12345678', public_url=None, studio_url='https://studio.youtube.com/video/abc12345678/edit')
        result = state['job']
    elif path == '/api/v1/publishing/jobs':
        result = {'jobs': [state['job']] if state['job'] else []}
    elif path.startswith('/api/v1/publishing/media/'):
        request.fulfill(status=200, content_type='video/mp4', body=media_bytes)
        return
    else:
        raise AssertionError('Unexpected browser request ' + path)
    request.fulfill(status=200, content_type='application/json', body=json.dumps(result))


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1200, 'height': 900})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('https://trafficlift.test/**', route)
    page.goto('https://trafficlift.test/launch-studio')
    expect(page.locator('#composeSection')).to_be_visible()
    page.locator('#file').set_input_files({'name': 'approved.mp4', 'mimeType': 'video/mp4', 'buffer': b'existing-video'})
    page.locator('#marketing').fill(product['product_url'])
    page.locator('#title').fill('A Season for Everyone')
    page.locator('#description').fill('Find the book through our channel profile.\n' + product['product_url'])
    page.locator('#visibility').select_option('public')
    page.locator('#kids').select_option('false')
    page.locator('#synthetic').select_option('true')
    for identifier in ('rights', 'copy', 'profile', 'duplicate'):
        page.locator('#' + identifier).check()
    page.get_by_role('button', name='Review YouTube preview').click()
    expect(page.locator('#reviewSection')).to_be_visible()
    assert state['approved'] == 0 and state['imported'] == 1
    expect(page.locator('#reviewDetails')).to_contain_text(channel)
    expect(page.locator('#reviewDetails')).to_contain_text('Requested visibility · public')
    # FFmpeg decodes the original MP4 in the API test. Headless Chromium builds
    # can omit H.264 playback support, so this smoke test checks the preview binding.
    assert page.locator('#preview').get_attribute('src') == '/api/v1/publishing/media/' + 'm' * 64
    page.screenshot(path=str(Path(temporary.name) / 'desktop.png'), full_page=True)
    page.get_by_role('button', name='Approve & Publish').click()
    expect(page.locator('#history')).to_contain_text('Verified visibility · private')
    expect(page.locator('#approve')).to_be_disabled()
    assert state['approved'] == 1
    assert page.get_by_role('link', name='View verified public video').count() == 0
    expect(page.get_by_role('link', name='Open in YouTube Studio')).to_be_visible()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=str(Path(temporary.name) / 'mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors, errors
    browser.close()
temporary.cleanup()
print('PASS: browser review gate, explicit single approval, exact channel/copy, actual Private visibility, no public URL, mobile layout, no JS errors')
