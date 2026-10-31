// Verify the HeyGen check is performed inside the owner modal, on its own origin.
// This test makes no external request or paid video.
const fs = require('fs'), assert = require('assert'), vm = require('vm');
const source = fs.readFileSync('index.html','utf8');
assert(source.includes('onclick="verifyHeyGenConnection()"'));
assert(source.includes('id="heygenConnectionStatus"'));
const start = source.indexOf('    async function verifyHeyGenConnection()');
const end = source.indexOf('    async function loadHostedAcceptanceSample()', start);
assert(start >= 0 && end > start);
const fn = source.slice(start,end);
let status = '', authorized = false, calls = 0;
const note = {set textContent(v) {status=v}, get textContent(){return status}};
const context = {hostedVideoSite:true, location:{origin:'https://trafficlift-pro.onrender.com'},
  document:{getElementById(id){assert.strictEqual(id,'heygenConnectionStatus');return note}},
  AbortSignal,
  fetch:async (url,options) => {
    calls++;
    assert.strictEqual(url,'https://trafficlift-pro.onrender.com/api/v1/heygen-video/verify-key');
    assert.strictEqual(options.credentials,'same-origin');
    assert.strictEqual(options.method,'GET');
    return authorized
      ? {ok:true,status:200,json:async()=>({provider:'heygen',authenticated:true,paid_generation_started:false})}
      : {ok:false,status:401,json:async()=>({detail:'Video access requires authorization.'})};
  }};
vm.createContext(context);vm.runInContext(fn,context);
(async()=> {
  await context.verifyHeyGenConnection();
  assert(status.includes('Owner session is missing'));
  authorized=true;await context.verifyHeyGenConnection();
  assert(status.includes('No video credits used'));
  assert.strictEqual(calls,2);
  console.log('PASS: HeyGen owner-modal same-origin verification and 401 recovery');
})().catch(e=>{console.error(e);process.exitCode=1});
