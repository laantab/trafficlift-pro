// Offline UI wiring contract. No paid HeyGen calls.
const fs = require('fs'), assert = require('assert');
const s = fs.readFileSync('index.html','utf8');
assert(s.includes('<option value="heygen" selected>HeyGen Video V2'));
assert(s.includes("document.getElementById('videoProvider').value = 'heygen'"));
assert(s.includes("if (provider === 'heygen')"));
assert(s.includes("await submitHeyGenVideo(statusEl, resultEl)"));
assert(s.includes("window.confirm('HeyGen Video V2 uses paid HeyGen credits"));
assert(s.includes("approve_paid_generation:true"));
assert(s.includes("credentials:'same-origin'"));
assert(s.includes("let heygenPendingVideoId = null"));
assert(s.includes("if (heygenPendingVideoId)"));
assert(s.includes("do not submit again") || s.includes("Do not submit again"));
assert(s.includes("video.controls = true"));
assert(s.includes("provider !== 'free' && provider !== 'heygen'"));
console.log('PASS: HeyGen selected, explicit paid approval, owner cookie, duplicate avoidance and preview');
