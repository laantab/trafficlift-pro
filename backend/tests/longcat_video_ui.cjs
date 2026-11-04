const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('index.html', 'utf8');
const source = html.slice(html.indexOf('    let longcatPendingRequestId'), html.indexOf('    let wanPendingRequestId'));
function context(savedJob = null) {
    const requests = [];
    const nodes = {};
    const ctx = {
        sessionStorage: { getItem: () => savedJob, setItem() {}, removeItem() {} },
        hostedVideoSite: true, location: {origin:'https://trafficlift.test'},
        AbortSignal, window: {confirm: () => false},
        restoreCurrentWinner: () => ({image_url:'https://seller.test/photo.jpg'}),
        document: {getElementById: id => nodes[id] ||= {}, createElement: () => ({})},
        fetch: async (url, options) => {
            requests.push({url, options});
            return {ok:true,json:async () => url.endsWith('/readiness')
                ? {enabled:true} : {status:'completed',video_url:'https://cdn.test/clip.mp4'}};
        },
    };
    vm.createContext(ctx); vm.runInContext(source, ctx);
    return {ctx, requests};
}
(async () => {
    // Cancelling the cost confirmation only performs the free readiness check.
    let {ctx, requests} = context();
    await ctx.submitLongCatVideo({}, {});
    assert.equal(requests.length, 1);
    assert(requests[0].url.endsWith('/readiness'));
    // Reloading with an existing job polls it instead of paying again.
    ({ctx, requests} = context('019a1b2c-3d4e-7f80-9a1b-2c3d4e5f6a7b'));
    await ctx.submitLongCatVideo({}, {replaceChildren(){}, append(){}, classList:{remove(){}}});
    assert.equal(requests.length, 1);
    assert(requests[0].url.includes('/status/019a1b2c'));
    ({ctx, requests} = context('submission-uncertain'));
    await assert.rejects(ctx.submitLongCatVideo({}, {}), /Check fal.ai job history/);
    assert.equal(requests.length, 0);
    console.log('LongCat UI: cancel, resume and uncertain-submission checks passed');
})().catch(error => { console.error(error); process.exit(1); });
