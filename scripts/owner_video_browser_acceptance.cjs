// Opt-in, real Chromium owner website -> render status -> MP4 download acceptance.
// Never run a new hosted render unless the operator explicitly passes --run.
// The access code is read only from the environment; no traces, screenshots or HAR.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const { chromium } = require('playwright');

const BASE = 'https://trafficlift-pro.onrender.com';
const JOB_ID = /^[0-9a-f]{32}$/;
const MAX_BYTES = 500 * 1024 * 1024;

function optionsFromEnv(args, env = process.env) {
  const mode = args[0];
  if ((mode !== '--run' && mode !== '--resume') ||
      (mode === '--run' && args.length !== 1) ||
      (mode === '--resume' && (args.length !== 2 || !JOB_ID.test(args[1])))) {
    throw new Error('Use --run to create ONE owner video or --resume <32-character-job-id> without a new render');
  }
  if (!env.TRAFFICLIFT_OWNER_ACCESS_CODE) {
    throw new Error('TRAFFICLIFT_OWNER_ACCESS_CODE must be set privately in the environment');
  }
  const output = path.resolve(env.TRAFFICLIFT_ACCEPTANCE_OUTPUT || 'trafficlift-owner-browser-verified.mp4');
  if (fs.existsSync(output)) throw new Error('Output exists; choose another TRAFFICLIFT_ACCEPTANCE_OUTPUT');
  const opts = {mode, jobId: mode === '--resume' ? args[1] : null,
    code: env.TRAFFICLIFT_OWNER_ACCESS_CODE, output, timeoutMs: 25 * 60 * 1000};
  if (mode === '--run') {
    const name = env.TRAFFICLIFT_ACCEPTANCE_PRODUCT_NAME;
    const image_url = env.TRAFFICLIFT_ACCEPTANCE_IMAGE_URL;
    const url = env.TRAFFICLIFT_ACCEPTANCE_PRODUCT_URL;
    if (!name || !image_url || !url) {
      throw new Error('A product name, licensed HTTPS image URL and HTTPS seller URL are required');
    }
    for (const value of [image_url, url]) {
      const parsed = new URL(value);
      if (parsed.protocol !== 'https:' || parsed.username || parsed.password) {
        throw new Error('Product and image URLs must be credential-free HTTPS URLs');
      }
    }
    opts.winner = {name, image_url, url};
    opts.benefit = env.TRAFFICLIFT_ACCEPTANCE_BENEFIT || '';
  }
  return opts;
}

// A valid ftyp marker alone does not establish a playable download. Check both
// streams and decode the complete file before accepting an owner MP4. Never
// print ffmpeg output: a malformed file may contain untrusted metadata.
function verifyDecodedMp4(filename) {
  const run = (tool, args, timeout) => spawnSync(tool, args, {
    encoding: 'utf8', timeout, maxBuffer: 256 * 1024, windowsHide: true,
  });
  const probe = run('ffprobe', ['-v', 'error', '-show_entries',
    'format=duration:stream=codec_type,width,height', '-of', 'json', filename], 30000);
  if (probe.error || probe.status !== 0) {
    throw new Error('MP4 invalid: ffprobe could not inspect the downloaded file');
  }
  let media;
  try { media = JSON.parse(probe.stdout); } catch (_) {
    throw new Error('MP4 invalid: ffprobe returned unreadable metadata');
  }
  const streams = Array.isArray(media.streams) ? media.streams : [];
  if (!streams.some(s => s.codec_type === 'video' && s.width > 0 && s.height > 0) ||
      !streams.some(s => s.codec_type === 'audio') ||
      !(Number(media.format?.duration) > 0)) {
    throw new Error('MP4 invalid: video, audio or duration is missing');
  }
  const decode = run('ffmpeg', ['-nostdin', '-v', 'error', '-xerror',
    '-i', filename, '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], 180000);
  if (decode.error || decode.status !== 0) {
    throw new Error('MP4 invalid: complete video/audio decode failed');
  }
}

async function runBrowserAcceptance(opts, {setupPage} = {}) {
  const browser = await chromium.launch({headless: true});
  let partial = null;
  try {
    const context = await browser.newContext({acceptDownloads: true, serviceWorkers: 'block'});
    const page = await context.newPage();
    if (setupPage) await setupPage(page, context); // Test-only network fixtures; never supplied by CLI.
    if (opts.winner) {
      await page.addInitScript(winner => {
        sessionStorage.setItem('tlp_current_winner', JSON.stringify(winner));
      }, opts.winner);
    }
    await page.goto(BASE + '/', {waitUntil: 'domcontentloaded', timeout: 60000});
    // Exercise the real owner browser UI, not just the HTTP API.
    if (opts.mode === '--run') {
      await page.locator('button[onclick="openVideoModal()"]').first().click();
    } else {
      // Resume does not need a fresh product discovery or another video POST.
      await page.evaluate(() => {
        document.getElementById('videoModal').classList.remove('hidden');
      });
    }
    const anonymous = await page.evaluate(async () => {
      const response = await fetch('/api/v1/photo-videos', {redirect: 'manual'});
      return response.status;
    });
    if (anonymous !== 401) throw new Error('Owner history was accessible without authentication');

    const loginResponsePromise = page.waitForResponse(
      response => response.url() === BASE + '/api/v1/video-owner-login' &&
        response.request().method() === 'POST', {timeout: 30000});
    await page.locator('#videoOwnerAccessCode').fill(opts.code);
    await page.locator('button[onclick="unlockHostedOwnerVideo()"]').click();
    const login = await loginResponsePromise;
    if (login.status() !== 200) throw new Error('Owner browser login was rejected');
    // Inspect Chromium's actual cookie jar: browsers may hide Set-Cookie from
    // response headers. The real server scopes this cookie to /api/v1.
    const cookies = await context.cookies(BASE + '/api/v1/photo-videos');
    const ownerCookie = cookies.find(cookie => cookie.name === 'trafficlift_video_session');
    if (!ownerCookie || !ownerCookie.httpOnly || !ownerCookie.secure ||
        ownerCookie.sameSite !== 'Strict') {
      throw new Error('Owner browser session cookie lacks required security flags');
    }
    await page.waitForFunction(() =>
      document.getElementById('videoOwnerAccessStatus').textContent.includes('unlocked'),
      {timeout: 30000});

    let jobId = opts.jobId;
    if (opts.mode === '--run') {
      if (opts.benefit) await page.locator('#freeProductFact').fill(opts.benefit);
      const createdPromise = page.waitForResponse(response =>
        response.url() === BASE + '/api/v1/photo-videos' &&
        response.request().method() === 'POST', {timeout: 60000});
      await page.locator('#videoSubmitBtn').click();
      const created = await createdPromise;
      if (created.status() !== 202) throw new Error('Owner video creation did not return HTTP 202');
      // Cache-Control is a directive set, not a substring. A contradictory
      // public directive or invented x-no-store must never certify a private
      // owner render creation response as safe to cache.
      const creationHeaders = await created.allHeaders();
      const creationDirectives = new Set((creationHeaders['cache-control'] || '')
        .split(',').map(directive => directive.trim().split('=', 1)[0].toLowerCase()));
      if (!creationDirectives.has('private') ||
          !creationDirectives.has('no-store') || creationDirectives.has('public')) {
        throw new Error('Video creation requires private no-store without public cache directives');
      }
      jobId = (await created.json()).video?.id;
      if (!JOB_ID.test(jobId || '')) throw new Error('Video creation returned an invalid job ID');
      // Print only the non-secret ID immediately. Resume this job after any interruption.
      process.stdout.write('Created owner video job: ' + jobId + ' (resume without a new render if interrupted)\n');
    } else {
      // Use the same production browser polling/render-display code as the UI.
      await page.evaluate(id => {
        void waitForFreeVideoJob(id,
          document.getElementById('videoStatus'), document.getElementById('videoResult'));
      }, jobId);
    }

    const link = page.locator('#videoResult a').filter({hasText: 'Download draft MP4'});
    // The browser UI polls the authenticated job status every two seconds.
    // If the render fails or owner status access is lost, fail acceptance
    // immediately instead of waiting 25 minutes for an MP4 link that cannot
    // appear. Observe only existing GET responses; never create another job.
    const statusUrl = BASE + '/api/v1/photo-videos/' + jobId;
    let rejectStatusFailure;
    const failedStatus = new Promise((_, reject) => { rejectStatusFailure = reject; });
    const onStatusResponse = async response => {
      if (response.url() !== statusUrl || response.request().method() !== 'GET') return;
      if (response.status() !== 200) {
        rejectStatusFailure(new Error('Owner video status returned HTTP ' + response.status()));
        return;
      }
      try {
        const data = await response.json();
        if (data?.video?.status === 'failed') {
          rejectStatusFailure(new Error('Owner video render failed; inspect Past videos'));
        } else if (!['queued', 'running', 'succeeded'].includes(data?.video?.status)) {
          // The UI has no MP4 link for unknown/unsupported states. Do not
          // wait the full 25-minute acceptance timeout for a dead job.
          rejectStatusFailure(new Error('Unexpected owner video state; inspect Past videos'));
        }
      } catch (_) {
        rejectStatusFailure(new Error('Owner video status response was not valid JSON'));
      }
    };
    page.on('response', onStatusResponse);
    try {
      await Promise.race([
        link.waitFor({state: 'visible', timeout: opts.timeoutMs}),
        failedStatus,
      ]);
    } finally {
      page.off('response', onStatusResponse);
    }
    const href = await link.getAttribute('href');
    if (href !== BASE + '/api/v1/photo-videos/' + jobId + '/video') {
      throw new Error('Browser download link is not the expected protected same-origin job');
    }
    // Validate the actual browser download response, not just its file bytes.
    // An owner-only MP4 must never be publicly cacheable.
    const responsePromise = page.waitForResponse(response =>
      response.url() === href && response.request().method() === 'GET',
      {timeout: 180000});
    const downloadPromise = page.waitForEvent('download', {timeout: 180000});
    await link.click();
    const mp4Response = await responsePromise;
    const mp4Headers = await mp4Response.allHeaders();
    // Cache-Control is a set of directives, not a substring match: a
    // contradictory "public, private, no-store" response must fail closed.
    const cacheDirectives = new Set((mp4Headers['cache-control'] || '')
      .split(',').map(directive => directive.trim().split('=', 1)[0].toLowerCase()));
    if (mp4Response.status() !== 200 ||
        !/^video\/mp4(?:;|$)/i.test(mp4Headers['content-type'] || '') ||
        !cacheDirectives.has('private') ||
        !cacheDirectives.has('no-store') ||
        cacheDirectives.has('public')) {
      throw new Error('MP4 response must be HTTP 200 video/mp4 with private no-store');
    }
    const download = await downloadPromise;
    if (await download.failure()) throw new Error('Chromium reported an MP4 download failure');
    fs.mkdirSync(path.dirname(opts.output), {recursive: true});
    partial = opts.output + '.partial-' + process.pid;
    if (fs.existsSync(partial)) throw new Error('Temporary download path already exists');
    await download.saveAs(partial);
    const stat = fs.statSync(partial);
    if (stat.size < 1024 || stat.size > MAX_BYTES) throw new Error('Downloaded MP4 size is invalid');
    const fd = fs.openSync(partial, 'r');
    let header;
    try {
      header = Buffer.alloc(12);
      fs.readSync(fd, header, 0, 12, 0);
    } finally { fs.closeSync(fd); }
    if (header.toString('ascii', 4, 8) !== 'ftyp') {
      throw new Error('Browser downloaded bytes without an MP4 ftyp signature');
    }
    verifyDecodedMp4(partial);
    // renameSync overwrites a file created between existsSync and publish.
    // The partial and destination share a directory/filesystem; linkSync
    // atomically fails with EEXIST instead of replacing another owner's MP4.
    try {
      fs.linkSync(partial, opts.output);
    } catch (error) {
      if (error.code === 'EEXIST') {
        throw new Error('Output appeared during download; refusing overwrite');
      }
      throw error;
    }
    fs.unlinkSync(partial);
    partial = null;
    return {jobId, bytes: stat.size, output: opts.output};
  } finally {
    if (partial) fs.rmSync(partial, {force: true});
    await browser.close();
  }
}

if (require.main === module) {
  (async () => {
    let code = '';
    try {
      const opts = optionsFromEnv(process.argv.slice(2));
      code = opts.code;
      const result = await runBrowserAcceptance(opts);
      console.log('Real Chromium owner website-to-MP4 check passed: job=' +
        result.jobId + ' bytes=' + result.bytes + ' file=' + result.output);
    } catch (error) {
      // Never print the owner code, HTTP bodies, cookies or browser traces.
      const safe = String(error.message || error).replaceAll(code || '\0', '[redacted]');
      console.error('Owner browser acceptance not verified: ' + safe.slice(0, 300));
      process.exitCode = 1;
    }
  })();
}

module.exports = {optionsFromEnv, runBrowserAcceptance, verifyDecodedMp4};
