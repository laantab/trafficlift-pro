// Verify HeyGen controls sit under Video Engine and report real activation states.
// No provider generations or paid API calls.
const fs=require('fs'), assert=require('assert'), vm=require('vm');
const source=fs.readFileSync('index.html','utf8');
const select=source.indexOf('id="videoProvider"');
const panel=source.indexOf('id="heygenEnginePanel"');
const owner=source.indexOf('id="hostedVideoOwnerAccess"');
assert(select>=0 && panel>select && owner>panel,'activation must appear below engine selector, above owner section');
assert(source.includes('onclick="verifyHeyGenConnection()"'));
assert.equal((source.match(/id="heygenConnectionStatus"/g)||[]).length,1);
const begin=source.indexOf('    async function checkHeyGenActivation()');
const end=source.indexOf('    async function loadHostedAcceptanceSample()',begin);
assert(begin>=0 && end>begin);
let authorized=false, enabled=true, calls=[], note='', state='';
const labels={
 heygenConnectionStatus:{set textContent(x){note=x},get textContent(){return note}},
 heygenEngineState:{set textContent(x){state=x},get textContent(){return state},className:''}
};
const context={
 hostedVideoSite:true,location:{origin:'https://trafficlift-pro.onrender.com'},AbortSignal,
 document:{getElementById(id){return labels[id] || null}},
 fetch:async (url,options)=>{
  calls.push(url);assert.equal(options.credentials,'same-origin');assert.equal(options.method,'GET');
  const isReady=url.endsWith('/readiness');
  if(!authorized)return {ok:false,status:401,json:async()=>({detail:'Video access requires authorization.'})};
  if(isReady)return {ok:true,status:200,json:async()=>({enabled})};
  assert(url.endsWith('/verify-key'));
  return {ok:true,status:200,json:async()=>({authenticated:true,paid_generation_started:false})};
 }
};
vm.createContext(context);vm.runInContext(source.slice(begin,end),context);
(async()=>{
 await context.checkHeyGenActivation();assert(state.includes('Owner access needed'));
 await context.verifyHeyGenConnection();assert(note.includes('Unlock Video Studio'));
 authorized=true;enabled=false;await context.checkHeyGenActivation();assert.equal(state,'Disabled');
 await context.verifyHeyGenConnection();assert.equal(state,'Disabled');
 enabled=true;await context.verifyHeyGenConnection();
 assert(state.includes('ACTIVE'));assert(note.includes('No video credits used'));
 assert(calls.every(x=>x.startsWith(context.location.origin+'/api/v1/heygen-video/')));
 console.log('PASS: HeyGen dropdown placement, authorization, disabled and connected states');
})().catch(e=>{console.error(e);process.exitCode=1});
