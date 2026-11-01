// Mobile layout and desktop-preservation acceptance in real Chromium.
// No external calls, credentials, product research or paid generation.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const source = fs.readFileSync('index.html', 'utf8')
 .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
(async () => {
 const browser = await chromium.launch({headless:true});
 try {
   const page = await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
   await page.setContent(source,{waitUntil:'domcontentloaded'});
   const mobile = await page.evaluate(() => {
     const rect = id => document.getElementById(id).getBoundingClientRect();
     return {viewport:innerWidth, scroll:document.documentElement.scrollWidth,
       control:rect('controlPanel').toJSON(), results:rect('resultsPanel').toJSON(),
       header:rect('trafficliftHeaderInner').toJSON(),
       videoButton:[...document.querySelectorAll('#trafficliftHeaderActions button')]
        .find(x=>x.getAttribute('onclick')==='openVideoModal()')?.getBoundingClientRect().toJSON()};
   });
   assert(mobile.scroll <= mobile.viewport+1, 'phone dashboard must not scroll horizontally');
   assert(mobile.results.y >= mobile.control.bottom-1, 'results must stack below controls');
   assert(mobile.videoButton?.height >= 44,'Video button must be touch sized');
   await page.evaluate(()=>{
     const modal=document.getElementById('videoModal');
     modal.classList.remove('hidden');
     modal.style.display='flex';
   });
   const video=await page.evaluate(()=>{
     const modal=document.getElementById('videoModal');
     const inner=modal.firstElementChild.getBoundingClientRect();
     return {viewport:innerWidth,scroll:document.documentElement.scrollWidth,
       height:inner.height,screen:innerHeight,submit:document.getElementById('videoSubmitBtn').getBoundingClientRect().toJSON(),
       selection:document.getElementById('videoProvider').value};
   });
   assert(video.scroll<=video.viewport+1,'phone Video Studio must fit without sideways scrolling');
   assert(Math.abs(video.height-video.screen)<2,'phone Video Studio should fill the screen');
   assert(video.selection==='heygen','preserve HeyGen default');
   assert(video.submit.width>270,'render control should span phone width');
   await page.setViewportSize({width:1440,height:900});
   await page.evaluate(()=>{document.getElementById('videoModal').style.display='none'});
   const desktop=await page.evaluate(()=>{
     const c=document.getElementById('controlPanel').getBoundingClientRect();
     const r=document.getElementById('resultsPanel').getBoundingClientRect();
     return {control:c.toJSON(),results:r.toJSON(),header:document.getElementById('trafficliftHeaderInner').getBoundingClientRect().toJSON()};
   });
   assert(desktop.results.x>desktop.control.right-1,'desktop must retain side-by-side layout');
   assert(desktop.header.height===64,'desktop header must remain original 64px');
   console.log('PASS: 390px touch dashboard, full-height Video Studio, 1440px desktop unchanged');
 } finally {await browser.close()}
})().catch(err=>{console.error(err);process.exitCode=1});
