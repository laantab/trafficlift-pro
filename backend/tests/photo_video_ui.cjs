// DOM contract check without downloading a browser. Actual browser QA remains pending.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('index.html','utf8');
const elements=new Map();
function element(id) {
 if(!elements.has(id)) {
  const hidden=new Set(['hidden']);
  elements.set(id,{id,value:'',textContent:'',src:'',childNodes:[],classList:{add:x=>hidden.add(x),remove:x=>hidden.delete(x),contains:x=>hidden.has(x),toggle:(x,on)=>on?hidden.add(x):hidden.delete(x)},replaceChildren(){this.childNodes=[]},append(...nodes){this.childNodes.push(...nodes)}});
 }
 return elements.get(id);
}
const winner={name:'Sunset Projection Lamp',image_url:'https://example.com/lamp.jpg',url:'https://example.com/lamp'};
let submitted;
const context={document:{getElementById:element,createElement:()=>({})},restoreCurrentWinner:()=>winner,buildWinnerVideoPrompt:()=> 'prompt',syncVideoModelOptions(){},updateVideoPromptCount(){},lucide:{createIcons(){}},showToast(){},URL,Date,AbortSignal,apiUrl:p=>p,setTimeout:fn=>{fn();return 1},fetch:async(path,opts={})=>{
 let data;
 if(path==='/api/v1/photo-videos'&&opts.method==='POST'){submitted=JSON.parse(opts.body);data={video:{id:'a'.repeat(32)}};}
 else if(path==='/api/v1/photo-videos')data={videos:[]};
 else data={video:{status:'succeeded',message:'Video checked and ready',video_url:'/api/v1/photo-videos/'+'a'.repeat(32)+'/video',product_url:winner.url}};
 return {ok:true,json:async()=>data};
}};
vm.createContext(context);
const open=source.slice(source.indexOf('    function openVideoModal()'),source.indexOf('    function closeVideoModal()'));
const funcs=source.slice(source.indexOf('    function syncVideoProviderOptions()'),source.indexOf('    function getLocalVideoBridge()'));
vm.runInContext(funcs+'\n'+open,context);
(async()=>{
 context.openVideoModal();
 assert.equal(element('freeProductPreview').src,winner.image_url);
 assert.equal(element('videoProvider').value,'free');
 assert.equal(element('freeProductName').value,winner.name);
 assert(element('videoResult').classList.contains('hidden'));
 element('freeVideoLength').value='15';element('freeVideoStyle').value='warm';element('freeProductFact').value='Warm glow.';
 await context.submitFreeVideo(element('videoStatus'),element('videoResult'));
 assert.equal(submitted.image_url,winner.image_url);assert.equal(submitted.benefit,'Warm glow.');assert.equal(submitted.seconds,15);
 assert(!element('videoResult').classList.contains('hidden'));assert.equal(element('videoResult').childNodes.length,3);
 const failed=element('failed');context.showFreeVideo({status:'failed',video_url:'/bad'},failed);assert.equal(failed.childNodes.length,0);
 console.log('PASS: preview, defaults, current-product payload, progress completion, checked playback/download, failed-output withholding');
})().catch(e=>{console.error(e);process.exitCode=1});
