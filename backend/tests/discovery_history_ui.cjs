const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('index.html','utf8');
const start = html.indexOf('    const DISCOVERY_HISTORY_KEY');
const end = html.indexOf('    async function findWinningProduct()', start);
const code = html.slice(start,end);
const stored = new Map();
const storage = {getItem:key=>stored.get(key)||null, setItem:(key,value)=>stored.set(key,value)};
function browser(storageImpl=storage) {
 const context={window:{}, localStorage:storageImpl, URLSearchParams, crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789012'}, Date};
 vm.createContext(context);vm.runInContext(code,context);return context;
}
let first=browser();
const client=first.discoveryClientId();
first.rememberDiscoveredWinner({source:'discovery',id:'discover-one',identity_keys:['name-one','url-one']});
first.rememberDiscoveredWinner({source:'discovery',id:'discover-two',identity_keys:['name-two','url-two']});
let refreshed=browser();
assert.equal(refreshed.discoveryClientId(),client);
const params=refreshed.discoveryParams();
assert.equal(params.get('exclude'),'discover-one,discover-two');
assert.equal(params.get('exclude_keys'),'name-one,url-one,name-two,url-two');
assert.equal(params.get('client_id'),client);
refreshed.rememberDiscoveredWinner({source:'discovery-curated',id:'saved-item',identity_keys:[]});
assert.equal(refreshed.discoveryHistory().length,2);
stored.set('trafficlift.discovery.history.v3','invalid json');
assert.doesNotThrow(()=>browser().discoveryParams());
// A 31-day-old winner must remain in browser exclusions. The server already
// retains permanent history, but the browser must not silently discard it.
stored.set('trafficlift.discovery.history.v3',JSON.stringify([{id:'old',keys:['name-old'],at:Date.now()-31*86400000}]));
const oldWinner=browser();
assert.equal(oldWinner.discoveryHistory().length,1,'old winners must not expire in local history');
assert.equal(oldWinner.discoveryParams().get('exclude'),'old');
assert.equal(oldWinner.discoveryParams().get('exclude_keys'),'name-old');
// A browser with disabled site storage cannot maintain a durable identity.
// Fail before research instead of silently issuing a new ID on every reload.
const blocked={getItem(){throw new Error('blocked')},setItem(){throw new Error('blocked')}};
assert.throws(()=>browser(blocked).discoveryParams(),/storage/i);
// Some privacy modes silently discard writes instead of throwing.
const discarded={getItem(){return null},setItem(){}};
assert.throws(()=>browser(discarded).discoveryParams(),/storage/i);
console.log('PASS: refresh identity, permanent browser exclusions, fallback refusal, malformed storage');
