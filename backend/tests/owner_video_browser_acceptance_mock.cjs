// Real Chromium with intercepted local fixtures: no network, owner code or render quota.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {optionsFromEnv, runBrowserAcceptance} = require('../../scripts/owner_video_browser_acceptance.cjs');

const ID = 'b'.repeat(32);
const HOST = 'https://trafficlift-pro.onrender.com';
const MP4 = Buffer.concat([Buffer.from([0, 0, 0, 24]), Buffer.from('ftypisom'), Buffer.alloc(4096, 88)]);
const html = fs.readFileSync('index.html', 'utf8')
  .replace(/<script[^>]*src=[^>]*><\/script>/g, '')
  .replace(/<script>\s*tailwind.config[\s\S]*?<\/script>/, '');

function fixture() {
  const calls = {created: 0, downloaded: 0, loggedIn: 0, privateCookieSeen: false};
  const setupPage = async page => {
    await page.addInitScript(() => { window.lucide = {createIcons() {}}; });
    await page.route('**/*', async route => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.origin !== HOST) return route.abort();
      const p = url.pathname;
      const json = (status, data, headers = {}) =>
        route.fulfill({status, contentType: 'application/json', headers, body: JSON.stringify(data)});
      const privateHeaders = {'Cache-Control': 'private, no-store'};
      if (p === '/') return route.fulfill({status: 200, contentType: 'text/html', body: html});
      if (p === '/api/v1/health') return json(200, {status: 'ok'});
      if (p === '/api/v1/video-owner-login' && req.method() === 'POST') {
        assert.equal(JSON.parse(req.postData()).access_code, 'fixture-owner-code');
        calls.loggedIn++;
        return json(200, {authorized: true}, {...privateHeaders,
          'Set-Cookie': 'trafficlift_video_session=fixture; Path=/; HttpOnly; Secure; SameSite=Strict'});
      }
      const hasCookie = (req.headers()['cookie'] || '').includes('trafficlift_video_session=fixture');
      if (hasCookie) calls.privateCookieSeen = true;
      if (p === '/api/v1/video-owner-acceptance') {
        return hasCookie ? json(200, {state: 'not_run', sample_only: true}, privateHeaders)
          : json(401, {detail: 'owner only'}, privateHeaders);
      }
      if (p === '/api/v1/photo-videos' && req.method() === 'GET') {
        return hasCookie ? json(200, {videos: []}, privateHeaders)
          : json(401, {detail: 'owner only'}, privateHeaders);
      }
      if (p === '/api/v1/photo-videos' && req.method() === 'POST') {
        if (!hasCookie) return json(401, {detail: 'owner only'}, privateHeaders);
        calls.created++;
        const body = JSON.parse(req.postData());
        assert.equal(body.name, 'Licensed Product');
        assert.equal(body.image_url, 'https://images.example/product.jpg');
        return json(202, {video: {id: ID}}, privateHeaders);
      }
      if (p === '/api/v1/photo-videos/' + ID) {
        return hasCookie ? json(200, {video: {
          id: ID, name: 'Licensed Product', status: 'succeeded', message: 'Ready',
          video_url: '/api/v1/photo-videos/' + ID + '/video',
          product_url: 'https://seller.example/item', sales_plan: {phrases: []},
        }}, privateHeaders) : json(401, {detail: 'owner only'}, privateHeaders);
      }
      if (p === '/api/v1/photo-videos/' + ID + '/video') {
        if (!hasCookie) return json(401, {detail: 'owner only'}, privateHeaders);
        calls.downloaded++;
        return route.fulfill({status: 200, headers: {...privateHeaders,
          'Content-Type': 'video/mp4', 'Content-Disposition': 'attachment; filename="owner.mp4"'},
          body: MP4});
      }
      return json(404, {detail: 'fixture not found'});
    });
  };
  return {setupPage, calls};
}

(async () => {
  assert.throws(() => optionsFromEnv([], {}), /Use --run/);
  assert.throws(() => optionsFromEnv(['--run'], {}), /OWNER_ACCESS_CODE/);
  assert.throws(() => optionsFromEnv(['--resume', '../escape'], {TRAFFICLIFT_OWNER_ACCESS_CODE: 'x'}), /Use --run/);
  assert.throws(() => optionsFromEnv(['--run'], {TRAFFICLIFT_OWNER_ACCESS_CODE: 'x'}), /licensed HTTPS/);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'trafficlift-browser-owner-'));
  try {
    const env = {
      TRAFFICLIFT_OWNER_ACCESS_CODE: 'fixture-owner-code',
      TRAFFICLIFT_ACCEPTANCE_PRODUCT_NAME: 'Licensed Product',
      TRAFFICLIFT_ACCEPTANCE_IMAGE_URL: 'https://images.example/product.jpg',
      TRAFFICLIFT_ACCEPTANCE_PRODUCT_URL: 'https://seller.example/item',
      TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'created.mp4'),
    };
    const opts = optionsFromEnv(['--run'], env);
    const one = fixture();
    const created = await runBrowserAcceptance({...opts, timeoutMs: 20000}, one);
    assert.equal(created.jobId, ID);
    assert.equal(one.calls.created, 1, 'one and only one hosted render POST');
    assert.equal(one.calls.loggedIn, 1);
    assert(one.calls.privateCookieSeen, 'browser must send the HttpOnly session cookie');
    assert(one.calls.downloaded >= 1);
    assert.deepEqual(fs.readFileSync(created.output), MP4);
    assert.throws(() => optionsFromEnv(['--run'], env), /Output exists/);

    const resumed = optionsFromEnv(['--resume', ID], {
      TRAFFICLIFT_OWNER_ACCESS_CODE: 'fixture-owner-code',
      TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'resumed.mp4'),
    });
    const two = fixture();
    const result = await runBrowserAcceptance({...resumed, timeoutMs: 20000}, two);
    assert.equal(result.jobId, ID);
    assert.equal(two.calls.created, 0, 'resume must never create another render');
    assert.deepEqual(fs.readFileSync(result.output), MP4);
    console.log('PASS: real Chromium owner UI login, one render, status, protected MP4 download and zero-quota resume');
  } finally {
    fs.rmSync(dir, {recursive: true, force: true});
  }
})().catch(e => {console.error(e); process.exitCode = 1;});
