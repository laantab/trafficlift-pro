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

function scenario(firstStatus) {
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

  let creates = 0, polls = 0;
  const response = data => ({ok: true, json: async () => data});
  const context = {
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
        creates++;
        return response({video: {id: creates === 1 ? ID : ID2}});
      }
      if (path === '/api/v1/photo-videos') return response({videos: []});
      if (path === '/api/v1/photo-videos/' + ID || path === '/api/v1/photo-videos/' + ID2) {
        polls++;
        if (polls === 1 && firstStatus === 'disconnect') throw new TypeError('network interrupted');
        if (polls === 1 && firstStatus === 'failed') {
          return response({video: {status: 'failed', message: 'Fixture worker failed'}});
        }
        return response({video: {
          id: creates === 1 ? ID : ID2, status: 'succeeded', message: 'Ready',
          video_url: '/api/v1/photo-videos/' + (creates === 1 ? ID : ID2) + '/video',
          product_url: 'https://example.com/organizer', sales_plan: {phrases: [], evidence: []},
        }});
      }
      throw new Error('Unexpected request ' + path);
    },
  };
  vm.createContext(context);
  vm.runInContext(script, context);
  return {context, get, count: () => creates};
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

  const failed = scenario('failed');
  await assert.rejects(
    failed.context.submitFreeVideo(failed.get('videoStatus'), failed.get('videoResult')),
    /Fixture worker failed/,
  );
  await failed.context.submitFreeVideo(failed.get('videoStatus'), failed.get('videoResult'));
  assert.strictEqual(failed.count(), 2,
    'terminal failed jobs must release the pending guard for a deliberate new render');
  console.log('PASS: owner browser resumes interrupted job, links protected MP4, and releases failed job');
})().catch(error => {console.error(error); process.exitCode = 1;});
