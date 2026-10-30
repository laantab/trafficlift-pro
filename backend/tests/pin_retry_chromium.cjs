// Real Chromium regression: frozen discovery is never called during Pinterest retry.
const assert = require('assert');
const fs = require('fs');
const {chromium} = require('playwright');
const {createCanvas, Image} = require('@napi-rs/canvas');

(async () => {
  const html = fs.readFileSync('index.html','utf8')
    .replace(/<script[^>]*src=[^>]*><\/script>/g,'')
    .replace(/<script>\s*tailwind.config[\s\S]*?<\/script>/,'');
  const canvas = createCanvas(1200,1200), ctx=canvas.getContext('2d');
  ctx.fillStyle='#f8fafc';ctx.fillRect(0,0,1200,1200);
  ctx.fillStyle='#23314d';ctx.fillRect(280,100,640,950);
  const image=canvas.toBuffer('image/png');
  const winner={id:'verified-pin-1',name:'MEZMUT 20 oz Insulated Tumbler Cup Travel Coffee Mug with Lid (Mixed1, 6Pack)',
    image_url:'https://m.media-amazon.com/images/I/real-listing.jpg',image_status:'verified',
    url:'https://www.amazon.com/dp/B0GVF21RZG',source:'discovery',
    discovery:{version:'3.0'},category:'Kitchen',identity_keys:['asin:B0GVF21RZG']};
  const pkg={pin_title:winner.name,board_title:'Coffee Tumblers',pin_cta_suggestion:'See Product Details',
    recommended_keywords:['coffee tumbler'],optimized_description:'Review this exact listing.'};
  const response={campaign_id:'pin-retry-1',compiled_package:{pinterest_seo_engine:pkg}};
  let generateCalls=0,discoverCalls=0,photoCalls=0,mode='502';
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({acceptDownloads:true});
    await page.addInitScript(()=>{
      localStorage.setItem('tlp_api_base',location.origin);
      window.lucide={createIcons(){}};
    });
    await page.route('**/*',async route=>{
      const u=route.request().url();
      if(u==='http://127.0.0.1:8000/')return route.fulfill({status:200,contentType:'text/html',body:html});
      if(u.includes('/api/v1/discover-winner')||u.includes('/api/v1/find-winner')) {
        discoverCalls++;return route.fulfill({status:500,body:'Unexpected discovery request'});
      }
      if(u.includes('/api/v1/traffic/generate')) {
        generateCalls++;
        if(mode==='502')return route.fulfill({status:502,contentType:'application/json',body:JSON.stringify({detail:'Temporary Pinterest package failure'})});
        if(mode==='missing')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({campaign_id:'no-pin'})});
        return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(response)});
      }
      if(u.includes('/api/v1/pin-image?')) {
        photoCalls++;
        if(mode==='photo-error')return route.fulfill({status:502,body:'Image unavailable'});
        return route.fulfill({status:200,contentType:'image/png',body:image});
      }
      if(u.includes('/api/v1/'))return route.fulfill({status:200,contentType:'application/json',body:'{}'});
      return route.abort();
    });
    await page.goto('http://127.0.0.1:8000/',{waitUntil:'domcontentloaded'});
    await page.evaluate(w=>{persistCurrentWinner(w);displayWinnerCard(w)},winner);
    assert.equal(await page.locator('#pinterestLaunchBtn').count(),1);
    assert.match(await page.locator('#pinterestLaunchBtn').innerText(),/Generate Pinterest Pin/);
    await page.locator('#pinterestLaunchBtn').click();
    await page.waitForFunction(()=>document.getElementById('pinterestResult')?.textContent.includes('Pinterest HTTP 502'));
    assert.equal(await page.evaluate(()=>restoreCurrentWinner().id),winner.id);
    assert.equal(discoverCalls,0,'Retry must not rediscover or replace the winner');
    assert.equal(await page.evaluate(()=>window.__lastPinDataUrl),null);

    mode='missing';
    await page.locator('#pinterestLaunchBtn').click();
    await page.waitForFunction(()=>document.getElementById('pinterestResult')?.textContent.includes('compiled_package.pinterest_seo_engine'));
    assert.equal(await page.evaluate(()=>restoreCurrentWinner().id),winner.id);

    mode='photo-error';
    await page.locator('#pinterestLaunchBtn').click();
    await page.waitForFunction(()=>document.getElementById('pinterestResult')?.textContent.includes('Pin not ready'));
    assert.equal(await page.evaluate(()=>window.__lastPinDataUrl),null,'Failed image cannot leave stale JPEG');
    assert.equal(await page.evaluate(()=>restoreCurrentWinner().id),winner.id);

    mode='ok';
    await page.locator('#pinterestLaunchBtn').click();
    await page.waitForFunction(()=>!!document.querySelector('#pinPreviewWrap img') && !!window.__lastPinDataUrl);
    const jpg=await page.evaluate(()=>window.__lastPinDataUrl);
    assert(jpg.startsWith('data:image/jpeg;base64,'));
    const output=Buffer.from(jpg.split(',')[1],'base64');
    const decoded=new Image();decoded.src=output;
    assert.equal(decoded.width,1000);assert.equal(decoded.height,1500);
    assert(output.length>10000);
    const downloadEvent=page.waitForEvent('download');
    await page.locator('#pinterestPackage button[onclick="downloadPinImage()"]').click();
    const download=await downloadEvent;
    assert.match(download.suggestedFilename(),/1000x1500\.jpg$/);
    const downloaded=fs.readFileSync(await download.path());
    assert(downloaded.length>10000);
    assert.equal(downloaded[0],0xff);assert.equal(downloaded[1],0xd8);
    const saved=new Image();saved.src=downloaded;
    assert.equal(saved.width,1000);assert.equal(saved.height,1500);
    assert.equal(discoverCalls,0);

    await page.reload({waitUntil:'domcontentloaded'});
    await page.waitForSelector('#pinterestLaunchBtn');
    assert.equal(await page.evaluate(()=>restoreCurrentWinner().id),winner.id,'Saved winner survives reload');
    assert.equal(discoverCalls,0,'Reloading pin action must not trigger discovery');
    assert.equal(generateCalls,4,'One bounded POST per click, no hidden retries');
    assert(photoCalls>=2,'Hosted pin must request same-origin image bytes');
    console.log('PASS: 502, missing package, image failure, persisted winner, retry, real JPEG preview and browser download; no discovery');
  } finally {await browser.close();}
})().catch(e=>{console.error(e.stack||e);process.exitCode=1});
