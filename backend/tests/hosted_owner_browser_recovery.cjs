// Browser regression for the actual hosted Video Studio reauthentication UI.
// Routes are mocked: this does NOT prove a live owner session or customer export.
// No paid render, owner token, external network request or new background job.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');

const HOST = 'https://trafficlift-pro.onrender.com';
const ID = 'd'.repeat(32);
const SECRET = 'ephemeral-test-code-not-a-production-secret';
const job = {
  id: ID, name: 'Desk Organizer', created_at: 1793200000,
  status: 'succeeded', message: 'Video ready',
  product_url: 'https://seller.example/product',
  video_url: '/api/v1/photo-videos/' + ID + '/video',
  sales_plan: {phrases: ['Desk Organizer: visible teal body and orange panel.'],
               review: {evidence_status: 'Owned synthetic fixture only'}},
  marketing_review: {message: 'Fixture video needs human creative review.'}
};

(async () => {
  const html = fs.readFileSync('index.html', 'utf8')
    .replace(/<script[^>]*src=[^>]*><\/script>/g, '')
    .replace(/<script>\s*tailwind.config[\s\S]*?<\/script>/, '');
  let loggedIn = false, logins = 0, renderPosts = 0, jobReads = 0, historyReads = 0;
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    await page.addInitScript(id => {
      sessionStorage.setItem('tlp_pending_hosted_video_job', id);
      window.lucide = {createIcons() {}};
    }, ID);
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== HOST) return route.abort();
      if (url.pathname === '/studio') {
        return route.fulfill({status: 200, contentType: 'text/html', body: html});
      }
      const respond = (status, payload) => route.fulfill({
        status, contentType: 'application/json', body: JSON.stringify(payload)
      });
      if (url.pathname === '/api/v1/video-owner-login' && request.method() === 'POST') {
        logins++;
        const submitted = JSON.parse(request.postData() || '{}');
        loggedIn = submitted.access_code === SECRET;
        return respond(loggedIn ? 200 : 401, loggedIn ? {ok: true} : {detail: 'Denied'});
      }
      if (url.pathname.startsWith('/api/v1/photo-videos') && request.method() === 'POST') {
        renderPosts++;
        return respond(500, {detail: 'Unexpected duplicate render'});
      }
      if (url.pathname === '/api/v1/video-owner-acceptance') {
        return respond(loggedIn ? 200 : 401, loggedIn
          ? {state: 'none', sample_only: false} : {detail: 'Owner session expired'});
      }
      if (url.pathname === '/api/v1/photo-videos') {
        if (!loggedIn) return respond(401, {detail: 'Owner session expired'});
        historyReads++;
        return respond(200, {videos: [job]});
      }
      if (url.pathname === '/api/v1/photo-videos/' + ID) {
        if (!loggedIn) return respond(401, {detail: 'Owner session expired'});
        jobReads++;
        return respond(200, {video: job});
      }
      return respond(404, {detail: 'Unmocked endpoint'});
    });
    await page.goto(HOST + '/studio', {waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() =>
      document.getElementById('videoOwnerAccessStatus')?.textContent
        .includes('owner session has expired'), null, {timeout: 15000});
    assert.equal(renderPosts, 0, 'Page reload must not POST another render');
    await page.locator('#videoOwnerAccessCode').fill(SECRET);
    await page.locator('button[onclick="unlockHostedOwnerVideo()"]').click();
    await page.waitForFunction(() =>
      document.getElementById('videoOwnerAccessStatus')?.textContent
        .includes('Your saved video is ready'), null, {timeout: 15000});
    const ui = await page.evaluate(() => ({
      pendingId: sessionStorage.getItem('tlp_pending_hosted_video_job'),
      inputValue: document.getElementById('videoOwnerAccessCode').value,
      videoVisible: !document.getElementById('videoResult').classList.contains('hidden'),
      hasMp4Link: [...document.querySelectorAll('#videoResult a')]
        .some(a => a.textContent.includes('Download draft MP4')),
      history: document.getElementById('freeVideoHistory').textContent,
      ownerStatus: document.getElementById('videoOwnerAccessStatus').textContent
    }));
    assert.equal(logins, 1, 'Exactly one secure reauthentication');
    assert.equal(renderPosts, 0, 'Reauthentication must never start a new render');
    assert(jobReads >= 1 && historyReads >= 1, 'Rejoined same saved job and history');
    assert.equal(ui.pendingId, null, 'Pending job ID clears only after success');
    assert.equal(ui.inputValue, '', 'Owner code cleared from form after login');
    assert(ui.videoVisible && ui.hasMp4Link, 'Existing MP4 becomes available');
    assert(ui.history.includes('Desk Organizer'), 'Existing video remains in history');
    assert(!JSON.stringify(ui).includes(SECRET), 'Owner code must not leak into UI');
    console.log('PASS: mocked hosted browser expired session -> re-login -> same saved job and MP4; zero new render POSTs');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
