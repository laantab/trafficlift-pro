// Browser DOM contract for the protected, real hosted MP4 acceptance sample.
// Does not render a customer video or expose the owner's access code.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync('index.html', 'utf8');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    const hidden = new Set(['hidden']);
    elements.set(id, {
      id, value: '', textContent: '',
      classList: {
        add: x => hidden.add(x), remove: x => hidden.delete(x),
        contains: x => hidden.has(x),
      },
      replaceChildren() {}, append() {},
    });
  }
  return elements.get(id);
}
const origin = 'https://trafficlift-pro.onrender.com';
let receipt = {
  state: 'passed', sample_only: true,
  download_url: '/api/v1/video-owner-acceptance/mp4',
};
let loginCount = 0;
const calls = [];
const context = {
  window: {location: {hostname: 'trafficlift-pro.onrender.com'}},
  location: {origin},
  document: {getElementById: element},
  AbortSignal,
  fetch: async (url, options = {}) => {
    calls.push({url, options});
    assert(url.startsWith(origin + '/api/v1/'), 'owner requests must be same-origin');
    if (url.endsWith('/video-owner-login')) {
      loginCount++;
      const data = JSON.parse(options.body);
      assert.strictEqual(data.access_code, 'test-owner-code');
      return {ok: true, json: async () => ({authorized: true})};
    }
    if (url.endsWith('/photo-videos')) return {ok: true, json: async () => ({videos: []})};
    if (url.endsWith('/video-owner-acceptance')) return {ok: true, json: async () => receipt};
    throw new Error('Unexpected request: ' + url);
  },
};
vm.createContext(context);
const code = source.slice(
  source.indexOf('    function syncVideoProviderOptions()'),
  source.indexOf('    function getLocalVideoBridge()')
);
assert(code.includes('async function loadHostedAcceptanceSample()'));
vm.runInContext(code, context);
(async () => {
  assert(!element('hostedVideoOwnerAccess').classList.contains('hidden'));
  assert(element('hostedVideoAcceptanceReview').classList.contains('hidden'));
  element('videoOwnerAccessCode').value = 'test-owner-code';
  await context.unlockHostedOwnerVideo();
  assert.strictEqual(loginCount, 1);
  assert.strictEqual(element('videoOwnerAccessCode').value, '');
  assert(!element('hostedVideoAcceptanceReview').classList.contains('hidden'));
  assert(!element('hostedVideoAcceptanceDownload').classList.contains('hidden'));
  assert(element('hostedVideoAcceptanceStatus').textContent.includes('technical checks'));
  assert(calls.some(c => c.url.endsWith('/video-owner-acceptance')));
  assert(!source.includes('test-owner-code'), 'test credential must not enter production HTML');
  receipt = {state: 'not_run', sample_only: true, download_url: null};
  await context.loadHostedAcceptanceSample();
  assert(element('hostedVideoAcceptanceDownload').classList.contains('hidden'));
  assert(element('hostedVideoAcceptanceStatus').textContent.includes('No verified'));
  receipt = {state: 'passed', sample_only: false, download_url: '/api/v1/video-owner-acceptance/mp4'};
  await context.loadHostedAcceptanceSample();
  assert(element('hostedVideoAcceptanceDownload').classList.contains('hidden'),
    'do not label customer or unverified output as real acceptance sample');
  console.log('PASS: owner unlock, same-origin real MP4 receipt, gated download and absent-sample handling');
})().catch(error => {console.error(error); process.exitCode = 1;});
