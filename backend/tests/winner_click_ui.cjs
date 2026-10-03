const fs=require('fs'), vm=require('vm'), assert=require('assert');
const html=fs.readFileSync('index.html','utf8');
function browser(response) {
 const nodes=new Map(), requests=[], events=new Map();
 const node=id=>{if(!nodes.has(id)) nodes.set(id,{innerHTML:'',textContent:'',style:{},value:'',classList:{add(){},remove(){}},querySelector(){return node(id+'span')},addEventListener(type,fn){events.set(id+type,fn)},appendChild(){}});return nodes.get(id)};
 const location={hostname:'127.0.0.1',pathname:'/studio',origin:'http://127.0.0.1:8000',href:'http://127.0.0.1:8000/studio',search:''};
 const context={console:{log(){},warn(){},error(){}},window:{location,lucide:{createIcons(){}}},
 document:{getElementById:node,querySelector(){return {content:''}},querySelectorAll(){return []},addEventListener(){},createElement(){return node('new')}},
 localStorage:{getItem(){return 'https://stale.example'},setItem(){}},sessionStorage:{getItem(){return null},setItem(){}},
 URL,URLSearchParams,Date,AbortController,crypto:require('crypto').webcrypto,
 setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){},
 fetch:async url=>{requests.push(url);return url.includes('/discover-winner')?response:{ok:true,json:async()=>({})}}};
 context.lucide=context.window.lucide;vm.createContext(context);
 for(const match of html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)) {
  if(match[1].includes('tailwind.config')) continue;
  vm.runInContext(match[1],context);
 }
 // Replace visual drawing only; the click handler and request pipeline are real.
 context.displayWinnerCard=()=>{};context.renderPinterestResult=()=>{};context.renderPinPreview=()=>{};
 return {context,requests,nodes,events};
}
const winner={id:'one',name:'Juicer',image_url:'https://example.com/image.jpg',image_status:'verified',source:'discovery',discovery:{version:'3.0'},identity_keys:['one']};
(async()=>{
 let b=browser({ok:true,json:async()=>winner});
 await b.events.get('findWinningProductBtnclick')();
 assert.equal(b.requests.length,2);assert(b.requests[0].startsWith('http://127.0.0.1:8000/api/v1/discover-winner'));
 assert(b.requests[1].includes('/traffic/generate'));assert.equal(b.context.window.__currentWinner.id,'one');
 for(const response of [{ok:false,status:503,json:async()=>({detail:'Research unavailable'})},{ok:true,json:async()=>({...winner,source:'discovery-curated'})}]) {
  b=browser(response);await b.events.get('findWinningProductBtnclick')();
  assert.equal(b.requests.length,1);assert(b.nodes.get('executionOutput').innerHTML.includes('Find Winning Product failed'));
  assert.equal(b.nodes.get('findWinningProductBtn').disabled,false);
 }
 console.log('PASS: actual button request, same origin, Pinterest continuation, visible failures, curated rejection');
})().catch(e=>{console.error(e);process.exitCode=1});
