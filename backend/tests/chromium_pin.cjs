// Real Chromium acceptance for the actual pin renderer, not a mocked canvas.
const fs = require('fs');
const assert = require('assert');
const { chromium } = require('playwright');
const { createCanvas } = require('@napi-rs/canvas');
(async () => {
  const source = fs.readFileSync('index.html','utf8')
    .replace(/<script[^>]*src=[^>]*><\/script>/g,'')
    .replace(/<script>\s*tailwind.config[\s\S]*?<\/script>/,'');
  const fixture = createCanvas(800,800);
  const c = fixture.getContext('2d');
  c.fillStyle='#fff'; c.fillRect(0,0,800,800);
  c.fillStyle='#222'; c.fillRect(310,90,180,540);
  c.fillRect(200,610,400,55);
  const png=fixture.toBuffer('image/png');
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:900},acceptDownloads:true});
    await page.addInitScript(()=>{window.lucide={createIcons(){}};});
    await page.route('http://127.0.0.1:8000/**',async route=>{
      const u=route.request().url();
      if(u.includes('/api/v1/product-image?'))return route.fulfill({status:200,contentType:'image/png',body:png});
      if(u.endsWith('/studio'))return route.fulfill({status:200,contentType:'text/html',body:source});
      return route.fulfill({status:200,contentType:'application/json',body:'{}'});
    });
    await page.goto('http://127.0.0.1:8000/studio',{waitUntil:'domcontentloaded'});
    const result=await page.evaluate(async()=>{
      window.__pinRenderGeneration=1;
      const winner={name:'Mainstays 17 LED Catch-All Base AC Outlet Adjustable Flexible Desk Lamp',category:'Home Lighting',image_url:'https://images.example/lamp.png'};
      const pkg=editPinterestPackage({pin_title:winner.name},winner);
      const jpeg=await renderPinPreview(winner,pkg);
      const img=new Image(); img.src=jpeg; await img.decode();
      const canvas=document.createElement('canvas');canvas.width=img.width;canvas.height=img.height;
      const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0);
      const hero=ctx.getImageData(0,0,1000,1080).data;
      let dark=0;for(let i=0;i<hero.length;i+=4){if(hero[i]<90&&hero[i+1]<90&&hero[i+2]<90)dark++;}
      return {width:img.width,height:img.height,headline:pkg.pin_title,cta:pkg.pin_cta_suggestion,darkPixels:dark,jpeg};
    });
    assert.equal(result.width,1000);assert.equal(result.height,1500);
    assert(result.headline.includes('Mainstays'),'Designed pin must identify the exact brand');
    assert(!result.headline.includes('…')&&!result.headline.includes('...'),'Headline truncated');
    assert(result.darkPixels>25000,'Hero subject not visible');
    // Exercise the actual browser download, not just the canvas preview.
    const downloadEvent=page.waitForEvent('download');
    await page.evaluate(()=>downloadPinImage());
    const download=await downloadEvent;
    assert.match(download.suggestedFilename(),/^pinterest-.*-1000x1500\.jpg$/);
    const saved=fs.readFileSync(await download.path());
    assert(saved.length>10000,'Downloaded JPEG is unexpectedly empty');
    assert.equal(saved[0],0xff);assert.equal(saved[1],0xd8);
    const savedImage=new (require('@napi-rs/canvas').Image)();
    savedImage.src=saved;
    assert.equal(savedImage.width,1000);assert.equal(savedImage.height,1500);
    fs.writeFileSync('/tmp/trafficlift-chromium-lamp.jpg',Buffer.from(result.jpeg.split(',')[1],'base64'));
    // A saved or legacy pin package must never download a category-only
    // title for the selected model, even when its product photo is valid.
    const stale = await page.evaluate(async () => {
      try {
        await renderPinPreview(
          {name:'JAS11242 LED Motion-Sensor Night Light', image_url:'https://images.example/lamp.png'},
          {pin_title:'Motion-Sensor Night Light',pin_cta_suggestion:'See Product Details'});
        return 'unexpected success';
      } catch (error) { return error.message; }
    });
    assert.match(stale,/exact product/);
    // Failed preview invalidates the cached download; never export the
    // preceding winner's designed pin for a newly selected product.
    assert.equal(await page.evaluate(() => window.__lastPinDataUrl),null);
    // A real browser with blocked storage must never issue a new winner request
    // under a temporary identity that disappears on reload.
    let discoveryRequests=0;
    await page.route('**/api/v1/discover-winner?**',route=>{
      discoveryRequests++;
      return route.fulfill({status:500,body:'Unexpected discovery request'});
    });
    const storageFailure=await page.evaluate(async()=>{
      Object.defineProperty(window,'localStorage',{configurable:true,get(){throw new Error('blocked')}});
      window.__discoveryClientId=null;
      await findWinningProduct();
      return document.getElementById('executionOutput').textContent;
    });
    assert.match(storageFailure,/browser site storage/i);
    assert.equal(discoveryRequests,0,'Blocked storage must not launch live research');
    console.log('PASS: real Chromium pin export and blocked-storage winner history fail-closed');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
