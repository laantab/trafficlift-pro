// Hosted owner browser regression: a transient status outage must not create a second render.
// Uses only mocked same-origin HTTP, DOM and timers; no credentials or hosted quota.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync('index.html', 'utf8');
const start = source.indexOf('    async function freeVideoRequest(path, options = {})');
const end = source.indexOf('    function getLocalVideoBridge()', start);
assert(start > 0 && end > start, 'hosted owner browser functions must be present');
const script = 'const hostedVideoSite = true;\n' + source.slice(start, end);
const origin = 'https://trafficlift-pro.onrender.com';
const ID = 'a'.repeat(32);
const ID2 = 'b'.repeat(32);

function scenario(firstStatus, shared = {creates: 0, storage: new Map()}) {
  const nodes = new Map();
  class Element {
    constructor(tag) {
      this.tag = tag; this.value = ''; this.textContent = ''; this.childNodes = [];
      const classes = new Set(['hidden']);
      this.classList = {
        add: name => classes.add(name), remove: name => classes.delete(name),
        contains: name => classes.has(name),
      };
    }
    replaceChildren(...children) { this.childNodes = children; }
    append(...children) { this.childNodes.push(...children); }
  }
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, new Element(id));
    return nodes.get(id);
  };
  for (const [key, value] of Object.entries({
    freeProductName: 'Desk Organizer',
    freeProductUrl: 'https://example.com/organizer',
    freeProductFact: 'Keeps supplies organized',
    freeBuyerNeed: 'Less clutter',
    freeBuyingDetail: 'Check dimensions',
    freeVideoLength: '15',
    freeVideoStyle: 'clean',
  })) get(key).value = value;

  let polls = 0;
  const response = data => ({ok: true, json: async () => data});
  const context = {
    sessionStorage: {
      getItem: key => shared.storage.get(key) ?? null,
      setItem: (key, value) => shared.storage.set(key, String(value)),
      removeItem: key => shared.storage.delete(key),
    },
    location: {origin}, AbortSignal: {timeout: () => ({})},
    setTimeout: fn => {fn(); return 0;},
    restoreCurrentWinner: () => ({
      image_url: 'https://example.com/photo.jpg', name: 'Desk Organizer',
      seller_facts: [], research_sources: [],
    }),
    document: {getElementById: get, createElement: tag => new Element(tag)},
    fetch: async (url, options = {}) => {
      assert(url.startsWith(origin + '/api/v1/'), 'owner requests must be same-origin');
      const path = url.slice(origin.length);
      if (path === '/api/v1/photo-videos' && options.method === 'POST') {
        shared.creates++;
        return response({video: {id: shared.creates === 1 ? ID : ID2}});
      }
      if (path === '/api/v1/photo-videos') return response({videos: []});
      if (path === '/api/v1/photo-videos/' + ID || path === '/api/v1/photo-videos/' + ID2) {
        polls++;
        if (polls === 1 && firstStatus === 'disconnect') throw new TypeError('network interrupted');
        if (polls === 1 && firstStatus === 'failed') {
          return response({video: {status: 'failed', message: 'Fixture worker failed'}});
        }
        return response({video: {
          id: shared.creates === 1 ? ID : ID2, status: 'succeeded', message: 'Ready',
          video_url: '/api/v1/photo-videos/' + (shared.creates === 1 ? ID : ID2) + '/video',
          product_url: 'https://example.com/organizer', sales_plan: {phrases: [], evidence: []},
        }});
      }
      throw new Error('Unexpected request ' + path);
    },
  };
  vm.createContext(context);
  vm.runInContext(script, context);
  return {context, get, count: () => shared.creates};
}

(async () => {
  const interrupted = scenario('disconnect');
  const status = interrupted.get('videoStatus');
  const result = interrupted.get('videoResult');
  await assert.rejects(
    interrupted.context.submitFreeVideo(status, result),
    /network interrupted/,
  );
  assert.strictEqual(interrupted.count(), 1, 'first submission should create one job');
  await interrupted.context.submitFreeVideo(status, result);
  assert.strictEqual(interrupted.count(), 1,
    'retry after polling outage must resume the known job, not consume another render');
  const link = result.childNodes.find(node => node.tag === 'a' && node.textContent === 'Download draft MP4');
  assert(link, 'finished owner job must offer an MP4 download');
  assert.strictEqual(link.href, origin + '/api/v1/photo-videos/' + ID + '/video');

  // A page reload loses JavaScript memory but must not lose the accepted job.
  // Clicking Render Video after reload must resume the saved job, not POST again.
  const shared = {creates: 0, storage: new Map()};
  const beforeReload = scenario('disconnect', shared);
  await assert.rejects(
    beforeReload.context.submitFreeVideo(beforeReload.get('videoStatus'), beforeReload.get('videoResult')),
    /network interrupted/,
  );
  assert.strictEqual(shared.creates, 1, 'one render accepted before reload');
  const afterReload = scenario('success', shared);
  await afterReload.context.submitFreeVideo(afterReload.get('videoStatus'), afterReload.get('videoResult'));
  assert.strictEqual(shared.creates, 1,
    'a reloaded browser must resume the existing job rather than submit another render');
  const resumedLink = afterReload.get('videoResult').childNodes.find(
    node => node.tag === 'a' && node.textContent === 'Download draft MP4');
  assert(resumedLink, 'reloaded browser must show the existing protected MP4');
  assert.strictEqual(resumedLink.href, origin + '/api/v1/photo-videos/' + ID + '/video');
  assert.strictEqual(shared.storage.has('tlp_pending_hosted_video_job'), false,
    'terminal success must clear the saved pending job');

  const failed = scenario('failed');
  await assert.rejects(
    failed.context.submitFreeVideo(failed.get('videoStatus'), failed.get('videoResult')),
    /Fixture worker failed/,
  );
  await failed.context.submitFreeVideo(failed.get('videoStatus'), failed.get('videoResult'));
  assert.strictEqual(failed.count(), 2,
    'terminal failed jobs must release the pending guard for a deliberate new render');
  // Regression: after an expired owner cookie, unlocking must automatically
  // resume the saved job, without another POST or manual render click.
  const unlockStart = source.indexOf('    async function unlockHostedOwnerVideo()');
  const unlockEnd = source.indexOf('    async function loadHostedAcceptanceSample()', unlockStart);
  assert(unlockStart > 0 && unlockEnd > unlockStart);
  const accessNodes = new Map();
  const accessNode = id => {
    if (!accessNodes.has(id)) accessNodes.set(id, new (class {
      constructor() {this.value = ''; this.textContent = ''; this.classList = {remove() {}};}
    })());
    return accessNodes.get(id);
  };
  accessNode('videoOwnerAccessCode').value = 'private-fixture-code';
  const recovery = {logins: 0, resumes: 0, histories: 0};
  const ownerContext = {
    document: {getElementById: accessNode},
    freeVideoRequest: async (path, opts) => {
      assert.equal(path, '/api/v1/video-owner-login');
      assert.equal(JSON.parse(opts.body).access_code, 'private-fixture-code');
      recovery.logins++;
    },
    loadFreeVideoHistory: async () => {recovery.histories++;},
    loadHostedAcceptanceSample: async () => {},
    waitForFreeVideoJob: async (id) => {assert.equal(id, ID); recovery.resumes++;},
    pendingHostedVideoJobId: ID,
    hostedVideoSite: true,
  };
  vm.createContext(ownerContext);
  vm.runInContext(source.slice(unlockStart, unlockEnd), ownerContext);
  await ownerContext.unlockHostedOwnerVideo();
  assert.equal(recovery.logins, 1);
  assert.equal(recovery.histories, 1);
  assert.equal(recovery.resumes, 1, 'unlock must resume the preserved render job');

  // Regression: the old Copy Pin path downloaded a raw retailer photo.
  // It must invoke only the existing 1000x1500 designed-pin download.
  const pinStart = source.indexOf('    async function copyPinBundle(');
  const pinEnd = source.indexOf('    // ── Clean one-click Find Winner', pinStart);
  assert(pinStart > 0 && pinEnd > pinStart);
  const pinCalls = {downloads: 0, notices: []};
  const pinContext = {
    navigator: {clipboard: {writeText: async () => {}}},
    window: {__lastPinDataUrl: 'data:image/jpeg;base64,fixture'},
    downloadPinImage: () => {pinCalls.downloads++;},
    showToast: (...args) => {pinCalls.notices.push(args);},
  };
  vm.createContext(pinContext);
  vm.runInContext(source.slice(pinStart, pinEnd), pinContext);
  await pinContext.copyPinBundle('Title', 'Description', 'Tags', 'https://seller.example/raw.jpg', '');
  assert.equal(pinCalls.downloads, 1, 'Copy Pin must download the designed pin, not a product photo');
  pinContext.window.__lastPinDataUrl = null;
  await pinContext.copyPinBundle('Title', 'Description', 'Tags', 'https://seller.example/raw.jpg', '');
  assert.equal(pinCalls.downloads, 1, 'No design must mean no image download');
  assert(pinCalls.notices.length, 'Missing design must explain how to generate one');

  console.log('PASS: owner browser resumes interrupted job, links protected MP4, and releases failed job');
})().catch(error => {console.error(error); process.exitCode = 1;});
