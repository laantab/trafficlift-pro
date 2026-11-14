// Krea is the default and owner access can be reached without finding hidden settings.
// No network or paid calls.
const fs=require('fs'),assert=require('assert');
const s=fs.readFileSync('index.html','utf8');
assert(s.includes('<option value="krea" selected>'));
assert(s.includes("document.getElementById('videoProvider').value = 'krea'"));
assert(s.includes('id="videoOwnerQuickButton"'));
assert(s.includes('onclick="showVideoOwnerUnlock()"'));
assert(s.includes("function showVideoOwnerUnlock()"));
assert(s.includes("setVideoOwnerQuickStatus(true)"));
assert(s.includes("setVideoOwnerQuickStatus(false)"));
assert(s.includes("readinessResponse.status === 401"));
assert(s.includes("checkKreaActivation()"));
assert(s.includes("Confirm the Video Studio start frame matches the exact product."));
assert(s.includes("approve_paid_generation:true"));
assert(s.includes("await submitKreaVideo(statusEl, resultEl)"));
assert(s.includes("provider !== 'heygen'"));
console.log('PASS: Krea-first workflow, owner guide, isolated image and paid consent');
