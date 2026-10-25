// Real Chromium with intercepted local fixtures: no network, owner code or render quota.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {optionsFromEnv, runBrowserAcceptance} = require('../../scripts/owner_video_browser_acceptance.cjs');

const ID = 'b'.repeat(32);
const HOST = 'https://trafficlift-pro.onrender.com';
// The success fixture must contain real decodable video AND audio, not merely
// an ftyp signature. The corrupt fixture below preserves the former false pass.
function createFixtureMp4() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'trafficlift-valid-mp4-'));
  try {
    const file = path.join(dir, 'fixture.mp4');
    execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
      '-f', 'lavfi', '-i', 'color=c=blue:s=64x96:r=5:d=1',
      '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono',
      '-t', '1', '-c:v', 'mpeg4', '-q:v', '5', '-c:a', 'aac',
      '-b:a', '32k', '-shortest', '-movflags', '+faststart', file],
    {timeout: 20000, stdio: 'ignore'});
    return fs.readFileSync(file);
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
}
const MP4 = createFixtureMp4();
const html = fs.readFileSync('index.html', 'utf8')
  .replace(/<script[^>]*src=[^>]*><\/script>/g, '')
  .replace(/<script>\s*tailwind.config[\s\S]*?<\/script>/, '');

function fixture({mp4CacheControl = 'private, no-store', creationCacheControl = 'private, no-store', videoStatus = 'succeeded', mp4Body = MP4,
  readinessState = 'ready', hostedEnabled = true, readinessHttpStatus = 200} = {}) {
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
      if (p === '/api/v1/video-readiness') return json(readinessHttpStatus, {
        state: readinessState, customer_rendering_enabled: hostedEnabled,
      });
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
        return json(202, {video: {id: ID}}, {...privateHeaders, 'Cache-Control': creationCacheControl});
      }
      if (p === '/api/v1/photo-videos/' + ID) {
        return hasCookie ? json(200, {video: {
          id: ID, name: 'Licensed Product', status: videoStatus,
          message: videoStatus === 'failed' ? 'Fixture render failed' : 'Ready',
          video_url: '/api/v1/photo-videos/' + ID + '/video',
          product_url: 'https://seller.example/item', sales_plan: {phrases: []},
        }}, privateHeaders) : json(401, {detail: 'owner only'}, privateHeaders);
      }
      if (p === '/api/v1/photo-videos/' + ID + '/video') {
        if (!hasCookie) return json(401, {detail: 'owner only'}, privateHeaders);
        calls.downloaded++;
        return route.fulfill({status: 200, headers: {...privateHeaders, 'Cache-Control': mp4CacheControl,
          'Content-Type': 'video/mp4', 'Content-Disposition': 'attachment; filename="owner.mp4"'},
          body: mp4Body});
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

    // Browser acceptance must not approve an owner creation response that
    // a shared cache may store, or a fake directive containing "no-store".
    // Previously /no-store/i accepted both without checking exact directives.
    for (const [label, policy] of [
      ['public', 'public, private, no-store'],
      ['fake', 'private, x-no-store'],
      ['contradictory', 'public, no-store'],
    ]) {
      const creationOpts = optionsFromEnv(['--run'], {
        ...env,
        TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'creation-' + label + '.mp4'),
      });
      const badCreation = fixture({creationCacheControl: policy});
      await assert.rejects(
        runBrowserAcceptance({...creationOpts, timeoutMs: 20000}, badCreation),
        /Video creation.*private no-store/,
        'owner creation must reject unsafe cache policy: ' + policy,
      );
      assert.equal(badCreation.calls.created, 1, 'only one fixture render POST');
      assert.equal(badCreation.calls.downloaded, 0, 'reject unsafe creation before downloading MP4');
      assert.equal(fs.existsSync(creationOpts.output), false, 'never publish an MP4 after unsafe creation');
    }

    // A live browser --run must not consume a scarce hosted render slot when
    // readiness is unavailable, still preparing, or owner rendering is off.
    // The standalone HTTP preflight is optional; this guard must run itself.
    for (const [label, state, enabled, httpStatus] of [
      ['preparing', 'preparing', true, 200],
      ['disabled', 'ready', false, 200],
      ['unavailable', 'ready', true, 503],
    ]) {
      const readinessOpts = optionsFromEnv(['--run'], {
        ...env, TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'readiness-' + label + '.mp4'),
      });
      const unready = fixture({readinessState: state, hostedEnabled: enabled,
        readinessHttpStatus: httpStatus});
      await assert.rejects(
        runBrowserAcceptance({...readinessOpts, timeoutMs: 20000}, unready),
        /Hosted video.*not ready/,
        'browser --run must stop before POST when readiness is ' + label,
      );
      assert.equal(unready.calls.created, 0, 'unready host must not create an owner render');
      assert.equal(unready.calls.loggedIn, 0, 'unready host must fail before owner login');
      assert.equal(fs.existsSync(readinessOpts.output), false, 'unready host must not publish an MP4');
    }

    // A forged ftyp header plus arbitrary bytes is not a playable MP4.
    // The acceptance probe must not certify or publish this corrupt download.
    const corruptOpts = optionsFromEnv(['--resume', ID], {
      TRAFFICLIFT_OWNER_ACCESS_CODE: 'fixture-owner-code',
      TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'corrupt.mp4'),
    });
    const corrupt = fixture({mp4Body: Buffer.concat([
      Buffer.from([0, 0, 0, 24]), Buffer.from('ftypisom'), Buffer.alloc(4096, 88),
    ])});
    await assert.rejects(
      runBrowserAcceptance({...corruptOpts, timeoutMs: 20000}, corrupt),
      /MP4.*(decode|invalid|playable)/i,
      'an ftyp header with arbitrary bytes must never pass real browser MP4 acceptance',
    );
    assert.equal(corrupt.calls.created, 0, 'corrupt resume must not start a render');
    assert.equal(fs.existsSync(corruptOpts.output), false, 'corrupt MP4 must not be published');

    const resumed = optionsFromEnv(['--resume', ID], {
      TRAFFICLIFT_OWNER_ACCESS_CODE: 'fixture-owner-code',
      TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'resumed.mp4'),
    });
    const two = fixture();
    const result = await runBrowserAcceptance({...resumed, timeoutMs: 20000}, two);
    assert.equal(result.jobId, ID);
    assert.equal(two.calls.created, 0, 'resume must never create another render');
    assert.deepEqual(fs.readFileSync(result.output), MP4);
    // A terminal failure is already visible in the authenticated status API.
    // Do not wait for the full MP4-link timeout before reporting the failed job.
    const failedOpts = optionsFromEnv(['--resume', ID], {
      TRAFFICLIFT_OWNER_ACCESS_CODE: 'fixture-owner-code',
      TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'failed.mp4'),
    });
    const failed = fixture({videoStatus: 'failed'});
    await assert.rejects(
      runBrowserAcceptance({...failedOpts, timeoutMs: 7000}, failed),
      /Owner video render failed/,
      'a failed owner job must be reported promptly instead of timing out waiting for an MP4',
    );
    // A malformed or unsupported job state must fail promptly rather than
    // leave the operator waiting for a download link that will never appear.
    const unknownOpts = optionsFromEnv(['--resume', ID], {
      TRAFFICLIFT_OWNER_ACCESS_CODE: 'fixture-owner-code',
      TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'unknown.mp4'),
    });
    const unknown = fixture({videoStatus: 'canceled'});
    await assert.rejects(
      runBrowserAcceptance({...unknownOpts, timeoutMs: 7000}, unknown),
      /Unexpected owner video state/,
      'unsupported status must fail fast without waiting for an MP4 link',
    );
    assert.equal(unknown.calls.created, 0, 'unknown status must not start another render');
    assert.equal(fs.existsSync(unknownOpts.output), false, 'unknown status must not publish an MP4');
    assert.equal(failed.calls.created, 0, 'failed resume must never start another render');
    assert.equal(fs.existsSync(failedOpts.output), false, 'failed render must not publish an MP4');
    const unsafe = fixture({mp4CacheControl: 'public, max-age=3600'});
    const unsafeOpts = optionsFromEnv(['--resume', ID], {
      TRAFFICLIFT_OWNER_ACCESS_CODE: 'fixture-owner-code',
      TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'unsafe.mp4'),
    });
    await assert.rejects(
      runBrowserAcceptance({...unsafeOpts, timeoutMs: 20000}, unsafe),
      /MP4 response.*no-store/,
      'a publicly cacheable owner-only MP4 must fail browser acceptance',
    );
    assert.equal(unsafe.calls.created, 0, 'security regression must not start a render');
    assert.equal(fs.existsSync(unsafeOpts.output), false, 'never save an insecure MP4');
    // Public cache permission must never pass even when private/no-store are also present.
    // This deliberately contradictory header was previously accepted by the browser probe.
    const contradictory = fixture({mp4CacheControl: 'public, private, no-store'});
    const contradictoryOpts = optionsFromEnv(['--resume', ID], {
      TRAFFICLIFT_OWNER_ACCESS_CODE: 'fixture-owner-code',
      TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'contradictory.mp4'),
    });
    await assert.rejects(
      runBrowserAcceptance({...contradictoryOpts, timeoutMs: 20000}, contradictory),
      /MP4 response.*no-store/,
      'a public cache directive must fail even alongside private no-store',
    );
    assert.equal(contradictory.calls.created, 0, 'security regression must not start a render');
    assert.equal(fs.existsSync(contradictoryOpts.output), false, 'never save a publicly cacheable MP4');
    // A second operator can create the destination after the initial
    // existsSync guard while Chromium streams the MP4. Neither renameSync
    // nor the replacement publish operation may overwrite that file.
    const raceOpts = optionsFromEnv(['--resume', ID], {
      TRAFFICLIFT_OWNER_ACCESS_CODE: 'fixture-owner-code',
      TRAFFICLIFT_ACCEPTANCE_OUTPUT: path.join(dir, 'concurrent.mp4'),
    });
    const competingBytes = Buffer.from('another operator\'s MP4 must remain intact');
    const originalRename = fs.renameSync;
    const originalLink = fs.linkSync;
    let raceInjected = false;
    function injectRace(destination) {
      if (destination === raceOpts.output && !raceInjected) {
        raceInjected = true;
        fs.writeFileSync(raceOpts.output, competingBytes);
      }
    }
    fs.renameSync = function(source, destination) {
      injectRace(destination);
      return originalRename.call(fs, source, destination);
    };
    fs.linkSync = function(source, destination) {
      injectRace(destination);
      return originalLink.call(fs, source, destination);
    };
    try {
      const race = fixture();
      await assert.rejects(
        runBrowserAcceptance({...raceOpts, timeoutMs: 20000}, race),
        /Output (already exists|appeared)/,
        'a concurrent MP4 must not be overwritten during browser acceptance',
      );
      assert(raceInjected, 'race must be injected at the final publish operation');
      assert.equal(race.calls.created, 0, 'resume must not start a second render');
      assert.deepEqual(fs.readFileSync(raceOpts.output), competingBytes);
      assert.equal(fs.readdirSync(dir).filter(name => name.includes('.partial-')).length, 0,
        'failed publish must remove the private partial MP4');
    } finally {
      fs.renameSync = originalRename;
      fs.linkSync = originalLink;
    }
    console.log('PASS: real Chromium owner UI login, one render, status, protected MP4 download, zero-quota resume, and private download cache policy');
  } finally {
    fs.rmSync(dir, {recursive: true, force: true});
  }
})().catch(e => {console.error(e); process.exitCode = 1;});
