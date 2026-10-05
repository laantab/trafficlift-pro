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
 // Exercise the real free-video submission with the untouched, blank benefit field.
 b=browser({ok:true,json:async()=>winner});
 b.context.window.__currentWinner={...winner,url:'https://example.com/product'};
 for(const [id,value] of Object.entries({freeProductName:'Juicer',freeProductUrl:'https://example.com/product',freeProductFact:'',freeBuyerNeed:'',freeBuyingDetail:'',freeVideoLength:'15',freeVideoStyle:'warm'})) b.context.document.getElementById(id).value=value;
 let submitted;
 b.context.freeVideoRequest=async(path,options)=>{
  if(options?.method==='POST'){submitted=JSON.parse(options.body);return {video:{id:'one'}};}
  return {video:{status:'succeeded',video_url:'/video',message:'Ready'}};
 };
 b.context.setTimeout=fn=>fn();b.context.showFreeVideo=()=>{};b.context.loadFreeVideoHistory=async()=>{};
 await b.context.submitFreeVideo({textContent:''},{});
 assert(submitted,'Blank benefit must reach the video API');assert.equal(submitted.benefit,'');
 assert.equal(submitted.image_url,winner.image_url);assert.equal(submitted.seconds,15);
 // A saved campaign reopens through one GET, restoring its product and settings.
 b=browser({ok:true,json:async()=>winner});
 const saved={campaign_id:'saved-one',input_url:'https://example.com/lamp',mode:'paid',budget:40,channels:['meta_ads'],scraped_product:{title:'Saved Lamp',primary_image:'https://example.com/lamp.jpg'},compiled_package:{},meta:{processing_time_ms:0,ai_mode:'template_fallback'}};
 const reuseRequests=[];
 b.context.fetch=async(url,options)=>{reuseRequests.push({url,options});return {ok:true,json:async()=>saved}};
 const checkboxes=[{value:'meta_ads',checked:false},{value:'tiktok_ads',checked:true}];
 b.context.document.querySelectorAll=selector=>selector.startsWith('#channelGrid')?checkboxes:[];
 await b.context.loadCampaign('saved-one');
 assert.equal(reuseRequests.length,1);assert(reuseRequests[0].url.endsWith('/campaigns/saved-one'));
 assert.equal(b.nodes.get('resTitle').textContent,'Saved Lamp');
 assert.equal(b.nodes.get('productUrl').value,saved.input_url);
 assert.equal(b.nodes.get('dailyBudget').value,40);
 assert.equal(b.context.window.__currentWinner.name,'Saved Lamp');
 assert.equal(b.context.window.__currentWinner.image_url,saved.scraped_product.primary_image);
 assert(checkboxes[0].checked && !checkboxes[1].checked);
 assert(b.nodes.get('statusMessage').textContent.includes('no new generation'));
 // URL launch must verify the product and invoke the image-pin renderer.
 b=browser({ok:true,json:async()=>winner});
 b.context.document.getElementById('productUrl').value='https://link.amazon/example';
 b.context.document.querySelectorAll=()=>[{value:'pinterest'}];
 b.context.tick=async()=>{};b.context.showLoading=()=>{};b.context.setLoadingStep=()=>{};
 b.context.fetchHealthStatus=()=>{};b.context.renderResults=()=>{};
 b.context.AbortSignal=AbortSignal;
 let launchCalls=[],pinCalls=0;
 b.context.renderPinterestResult=()=>{pinCalls++};
 b.context.fetch=async(url,options)=>{launchCalls.push({url,options});return {ok:true,json:async()=>url.includes('/analyze-product-url')?{...winner,url:'https://amazon.com/dp/EXAMPLE'}:{compiled_package:{pinterest_seo_engine:{}},campaign_id:'new'}}};
 await b.context.launchCampaign();
 assert.equal(launchCalls.length,2);
 assert(launchCalls[0].url.includes('/analyze-product-url'));
 assert.equal(JSON.parse(launchCalls[1].options.body).product_payload.name,'Juicer');
 assert.equal(pinCalls,1);
 console.log('PASS: actual button request, same origin, Pinterest continuation, visible failures, curated rejection');
})().catch(e=>{console.error(e);process.exitCode=1});
