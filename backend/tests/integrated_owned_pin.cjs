// One owned, synthetic exact-product fixture across Pinterest and narrated video QA.
// This is NOT licensed retailer photography and does NOT certify a real listing.
'use strict';
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const {createCanvas, Image} = require('@napi-rs/canvas');

(async () => {
  const html = fs.readFileSync('index.html', 'utf8')
    .replace(/<script[^>]*src=[^>]*><\/script>/g, '')
    .replace(/<script>\s*tailwind.config[\s\S]*?<\/script>/, '');
  // Same first, locally drawn product image as video_phase2_mp4_smoke.py.
  // Owned test art, deliberately not attributed to a manufacturer.
  const source = createCanvas(800, 800), ctx = source.getContext('2d');
  ctx.fillStyle = '#fcfcfc'; ctx.fillRect(0, 0, 800, 800);
  ctx.fillStyle = '#235F68';
  ctx.beginPath(); ctx.roundRect(190, 110, 420, 580, 35); ctx.fill();
  ctx.fillStyle = '#CD8D44'; ctx.fillRect(240, 620, 320, 80);
  const productPng = source.toBuffer('image/png');
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({acceptDownloads: true, viewport: {width: 1280, height: 900}});
    await page.addInitScript(() => {window.lucide = {createIcons() {}};});
    await page.route('http://127.0.0.1:8000/**', route => {
      const url = route.request().url();
      if (url.includes('/api/v1/product-image?')) {
        return route.fulfill({status: 200, contentType: 'image/png', body: productPng});
      }
      if (url.endsWith('/studio')) {
        return route.fulfill({status: 200, contentType: 'text/html', body: html});
      }
      return route.fulfill({status: 200, contentType: 'application/json', body: '{}'});
    });
    await page.goto('http://127.0.0.1:8000/studio', {waitUntil: 'domcontentloaded'});
    const result = await page.evaluate(async () => {
      window.__pinRenderGeneration = 1;
      const winner = {name: 'Desk Organizer', category: 'Office Organization',
        image_url: 'https://images.example/owned-desk-organizer.png'};
      const pkg = editPinterestPackage({
        pin_title: 'Desk Organizer — Teal Body and Orange Lower Panel',
        pin_cta_suggestion: 'See Desk Organizer Details',
      }, winner);
      const jpeg = await renderPinPreview(winner, pkg);
      const image = new Image(); image.src = jpeg; await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = image.width; canvas.height = image.height;
      const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
      const pixels = ctx.getImageData(0, 1150, 1000, 350).data;
      const bins = new Set();
      for (let i = 0; i < pixels.length; i += 80) {
        bins.add([pixels[i] >> 4, pixels[i + 1] >> 4, pixels[i + 2] >> 4].join(':'));
      }
      return {width: image.width, height: image.height, title: pkg.pin_title,
        cta: pkg.pin_cta_suggestion, bottomColorBins: bins.size};
    });
    assert.equal(result.width, 1000);
    assert.equal(result.height, 1500);
    assert.match(result.title, /Desk Organizer/i);
    assert.doesNotMatch(result.title, /Explore .* Ideas|Review listed features|Picture your setup/i);
    assert(result.bottomColorBins > 5, 'Download must include a designed footer, not a raw photo');
    const pending = page.waitForEvent('download');
    await page.evaluate(() => downloadPinImage());
    const download = await pending;
    assert.match(download.suggestedFilename(), /1000x1500\.jpg$/);
    const bytes = fs.readFileSync(await download.path());
    const image = new Image(); image.src = bytes;
    assert.equal(image.width, 1000); assert.equal(image.height, 1500);
    assert(bytes.length > 10000, 'Designed pin must not be empty');
    fs.writeFileSync('/tmp/trafficlift-integrated-pin.jpg', bytes);
    fs.writeFileSync('/tmp/trafficlift-integrated-pin-evidence.json',
      JSON.stringify({...result, product: 'Desk Organizer',
        source: 'Owned synthetic drawing; no retailer photography rights implied',
        downloadedBytes: bytes.length}, null, 2));
    console.log('PASS: actual Chromium 1000x1500 designed Desk Organizer download from owned fixture');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
