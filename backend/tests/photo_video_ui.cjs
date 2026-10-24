// DOM contract check without downloading a browser. Actual browser QA remains pending.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('index.html','utf8');
const elements=new Map();
function element(id) {
 if(!elements.has(id)) {
  const hidden=new Set(['hidden']);
  elements.set(id,{id,value:'',textContent:'',src:'',childNodes:[],classList:{add:x=>hidden.add(x),remove:x=>hidden.delete(x),contains:x=>hidden.has(x),toggle:(x,on)=>on?hidden.add(x):hidden.delete(x)},replaceChildren(){this.childNodes=[]},append(...nodes){this.childNodes.push(...nodes)},setAttribute(){},getAttribute(){return null}});
 }
 return elements.get(id);
}
const winner={name:'Sunset Projection Lamp',image_url:'https://example.com/lamp.jpg',url:'https://example.com/lamp'};
let submitted; let createCount=0; let historyJobs=[]; let statusJob=null; let statusChecks=0;
const context={document:{getElementById:element,createElement:()=>element('created-'+Math.random())},restoreCurrentWinner:()=>winner,buildWinnerVideoPrompt:()=> 'prompt',syncVideoModelOptions(){},updateVideoPromptCount(){},lucide:{createIcons(){}},showToast(){},URL,Date,AbortSignal,apiUrl:p=>p,setTimeout:fn=>{fn();return 1},fetch:async(path,opts={})=>{
 let data;
 if(path==='/api/v1/photo-videos'&&opts.method==='POST'){createCount++;submitted=JSON.parse(opts.body);data={video:{id:'a'.repeat(32)}};}
 else if(path==='/api/v1/photo-videos')data={videos:historyJobs};
 else {statusChecks++;data={video:statusJob||{id:'a'.repeat(32),status:'succeeded',message:'Video checked and ready',video_url:'/api/v1/photo-videos/'+'a'.repeat(32)+'/video',product_url:winner.url}};}
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
 assert(!element('videoResult').classList.contains('hidden'));assert.equal(element('videoResult').childNodes.length,7);
 assert.equal(element('videoResult').childNodes[1].src,'/api/v1/photo-videos/'+'a'.repeat(32)+'/video');
 assert.equal(element('videoResult').childNodes[2].textContent,'Download draft MP4');
 const kits=element('videoResult').childNodes[6].childNodes;
 assert.equal(kits.length,3,'three kits only after successful render');
 assert(kits[0].href.endsWith('/exports/tiktok'));
 assert(kits[1].href.endsWith('/exports/youtube-shorts'));
 assert(kits[2].href.endsWith('/exports/instagram-reels'));

 // A reload may interrupt polling while the backend job continues. Reattach to
 // the saved job ID rather than spending another quota slot or rendering twice.
 const pending={id:'b'.repeat(32),name:'Saved lamp video',created_at:1700000000,status:'running'};
 historyJobs=[pending];
 await context.loadFreeVideoHistory();
 let rows=element('freeVideoHistory').childNodes;
 assert.equal(rows.length,1);
 const resume=rows[0].childNodes[1];
 assert.equal(resume.textContent,' Resume progress');
 const originalCreates=createCount;
 statusJob={id:pending.id,status:'succeeded',message:'Recovered video ready',
   video_url:'/api/v1/photo-videos/'+pending.id+'/video',product_url:winner.url};
 historyJobs=[{...pending,...statusJob}];
 await resume.onclick();
 assert.equal(createCount,originalCreates,'resuming must not POST a new render');
 assert(statusChecks>=2,'saved status endpoint should be polled');
 assert.equal(element('videoStatus').textContent,'Recovered video ready');
 assert.equal(element('videoResult').childNodes[2].href,
   '/api/v1/photo-videos/'+pending.id+'/video');
 assert(!element('videoResult').classList.contains('hidden'));
 // Failed resumed jobs must not leave the previous video's download visible.
 historyJobs=[pending];
 statusJob={status:'failed',message:'Source photo could not be downloaded'};
 await context.loadFreeVideoHistory();
 rows=element('freeVideoHistory').childNodes;
 await rows[0].childNodes[1].onclick();
 assert.equal(createCount,originalCreates);
 assert.equal(element('videoStatus').textContent,'Source photo could not be downloaded');
 assert(element('videoResult').classList.contains('hidden'));
 assert.equal(element('videoResult').childNodes.length,0);
 const failed=element('failed');context.showFreeVideo({status:'failed',video_url:'/bad'},failed);assert.equal(failed.childNodes.length,0);
 console.log('PASS: new video, saved-job resume without duplicate POST, recovered MP4, failed-output withholding');
})().catch(e=>{console.error(e);process.exitCode=1});
