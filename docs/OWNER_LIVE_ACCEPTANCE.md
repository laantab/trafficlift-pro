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
the existing hosted health endpoint responds and unauthenticated video history
is denied. This does **not** sign in, create a render, consume a video quota
slot, download an MP4, or establish customer-launch readiness. It is safe to
run before obtaining the private owner code.

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
MP4. It verifies no-store headers, security cookie flags, MP4 content type,
a plausible `ftyp` MP4 header, and nontrivial downloaded bytes. It rejects
redirects and does not print the access code.

If polling times out, note the job ID from the error and run
`python scripts/owner_video_live_acceptance.py --resume <32-character-job-id> --output owner-check.mp4`.
Resume does not create another render or consume a new daily slot.

**Limits:** This is an authenticated HTTP acceptance probe, not a full
Chromium user-interface test. The downloaded MP4 still requires human visual,
audio, caption, product-accuracy, rights, and platform safe-zone review.
A successful run does not by itself establish customer-launch readiness.
