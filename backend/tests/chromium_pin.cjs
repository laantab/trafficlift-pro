// Real Chromium acceptance for the actual pin renderer, not a mocked canvas.
const fs = require('fs');
const assert = require('assert');
const { chromium } = require('playwright');
const { createCanvas } = require('@napi-rs/canvas');
(async () => {
  const source = fs.readFileSync('index.html','utf8')
    .replace(/<script[^>]*src=[^>]*><\\/script>/g,'')
    .replace(/<script>\\s*tailwind.config[\\s\\S]*?<\\/script>/,'');
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
    assert(!result.headline.includes('Mainstays'),'Listing title leaked to pin');
    assert(!result.headline.includes('…')&&!result.headline.includes('...'),'Headline truncated');
    assert(result.darkPixels>25000,'Hero subject not visible');
    fs.writeFileSync('/tmp/trafficlift-chromium-lamp.jpg',Buffer.from(result.jpeg.split(',')[1],'base64'));
    console.log('PASS: real Chromium renders full 1000x1500 pin, claim-safe lamp headline, and visible product photo');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
