const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('index.html','utf8');
const start = html.indexOf('    const DISCOVERY_HISTORY_KEY');
const end = html.indexOf('    async function findWinningProduct()', start);
const code = html.slice(start,end);
const stored = new Map();
const storage = {getItem:key=>stored.get(key)||null, setItem:(key,value)=>stored.set(key,value)};
function browser() {
 const context={window:{}, localStorage:storage, URLSearchParams, crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789012'}, Date};
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
stored.set('trafficlift.discovery.history.v3',JSON.stringify([{id:'old',keys:[],at:Date.now()-31*86400000}]));
assert.equal(browser().discoveryHistory().length,0);
console.log('PASS: refresh identity, exclusion history, fallback refusal, malformed storage, expiration');
