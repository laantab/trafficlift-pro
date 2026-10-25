# Protected owner website-to-MP4 acceptance

The CI integration tests use fixture bytes and **do not** establish that a real
owner can create, poll and download an MP4 on Render. The existing real Kokoro
CI test verifies a local composition separately.

`scripts/owner_video_live_acceptance.py` is an **explicit, opt-in** acceptance
probe against the existing official HTTPS Render host. It does not add routes,
enable public rendering, modify hosting, or use paid video providers. Run it
only when owner-hosted video is already intentionally enabled and provisioned.

## Free read-only preflight (no access code needed)

Run `python scripts/owner_video_live_acceptance.py --preflight` to verify that
the existing hosted health endpoint responds and unauthenticated video history,
individual video status, master MP4 download, platform export ZIP, owner sample
status, and owner sample MP4 are all denied with HTTP 401. Job checks use a
non-existent 32-character job ID. Download checks stream without reading or
saving any response body and always close the response. This does **not** sign
in, create a render, consume a video quota slot, download an MP4 or ZIP, or
establish customer-launch readiness. It is safe to run before obtaining the
private owner code.

## Preconditions

- The owner access code is available privately. **Never commit, paste into
  chat, log, or pass it as a command-line argument.**
- A real, specific product and publicly accessible HTTPS product photo URL
  have been selected. Do not use an arbitrary image without marketing rights.
- The existing Render service has sufficient video capacity and disk space.
  A new run consumes **one** of the configured daily hosted render slots.
- The owner has approved a live video render on the existing service.

Set the following environment variables **in your private terminal**, not in
GitHub Actions or a repository file:

- `TRAFFICLIFT_OWNER_ACCESS_CODE` — existing owner-only access code.
- `TRAFFICLIFT_ACCEPTANCE_PRODUCT_NAME` — specific product name.
- `TRAFFICLIFT_ACCEPTANCE_IMAGE_URL` — HTTPS product photo URL.
- `TRAFFICLIFT_ACCEPTANCE_PRODUCT_URL` — HTTPS product listing URL.
- Optional: `TRAFFICLIFT_ACCEPTANCE_BENEFIT`.

Run `python scripts/owner_video_live_acceptance.py --run --output owner-check.mp4`.
The script first checks that unauthenticated history is denied, then signs in
using the HttpOnly cookie, creates one job, polls status, and downloads the
MP4. Install local `ffprobe` and `ffmpeg` first. The script verifies no-store
headers, security cookie flags, MP4 content type, a plausible `ftyp` header,
real video/audio streams, positive duration, and full video/audio decoding
before atomically publishing the download. It rejects redirects, corrupt
downloads, and missing decoding tools without printing the access code or
untrusted media metadata.

If polling times out, note the job ID from the error and run
`python scripts/owner_video_live_acceptance.py --resume <32-character-job-id> --output owner-check.mp4`.
Resume does not create another render or consume a new daily slot.

**Limits:** This is an authenticated HTTP acceptance probe, not a full
Chromium user-interface test. The downloaded MP4 still requires human visual,
audio, caption, product-accuracy, rights, and platform safe-zone review.
A successful run does not by itself establish customer-launch readiness.


## Real Chromium owner website-to-MP4 acceptance (opt-in)

The separate `scripts/owner_video_browser_acceptance.cjs` drives the **actual
hosted browser page**, rather than just HTTP requests: it opens the Video
modal, enters the private owner access code into the password field, uses
the session cookie, creates **one** video, watches the production browser
status code, clicks **Download draft MP4**, and checks the downloaded bytes.
It also verifies anonymous history is denied, the owner cookie has Secure,
HttpOnly and SameSite=Strict, and the MP4 link is same-origin. No screenshots,
traces, HAR, owner credentials or customer MP4 files are uploaded to CI.

On a trusted computer with Node.js, install the browser test dependency and
Chromium using `npm install --no-save --no-package-lock playwright@1.56.1`
and `npx playwright install chromium`. Set the same private environment
variables listed above, then run
`node scripts/owner_video_browser_acceptance.cjs --run`.
To resume an existing job **without starting another render**, run
`node scripts/owner_video_browser_acceptance.cjs --resume <32-character-job-id>`.
The optional `TRAFFICLIFT_ACCEPTANCE_OUTPUT` selects a new output filename;
an existing file is never overwritten. An interrupted creation prints only
its non-secret job ID before waiting, so resume rather than clicking Run again.

The browser fixture regression runs in CI without credentials, external
requests, a real render, or quota usage. **It is not proof of production
acceptance.** The live Chromium command requires the private owner access
code and a rights-cleared product photo; it must be explicitly run by an
authorized operator. Review the saved MP4's video, audio, captions, claims,
photo rights and platform framing before any customer-launch claim.
