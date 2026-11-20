# YouTube Phase 1

Launch Studio reuses an existing MP4 and the saved campaign selected by the owner.
It performs local technical inspection, stores a review, and queues the exact
approved bytes and metadata for a YouTube resumable upload. No generation APIs
are called by publishing. Human review of footage, narration, claims and product
identity remains required. Technical QA does not certify semantic correctness.

## Server setup before live acceptance

1. Enable YouTube Data API v3 in a Google Cloud project. Create a **web application**
   OAuth client and configure the consent screen, authorized test users (if in
   testing), privacy policy and applicable Google verification. Confirm the API
   project's upload eligibility/audit with Google. An unverified project's uploads
   can be restricted to Private. This cannot be inferred from an OAuth success.
2. Set `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, and `YOUTUBE_REDIRECT_URI`
   privately. Register the exact HTTPS callback
   `https://<official-host>/api/v1/publishing/youtube/callback` in Google.
3. Generate a Fernet key with
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   Save it privately as `TRAFFICLIFT_PUBLISHING_KEY` and back it up securely.
   Preserve this key across restarts. OAuth credentials, resumable session URLs,
   reviews and logs are encrypted under it. The credential key is distinct from
   the existing owner login secret. Changing it requires a deliberate migration.
4. Configure existing owner access using `TRAFFICLIFT_HOSTED_VIDEO=1` and
   `TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN` (32+ characters). Set
   `TRAFFICLIFT_VIDEO_DIR` to persistent storage, then opt in with
   `TRAFFICLIFT_YOUTUBE_ENABLED=1`. One logical owner is supported by the existing
   owner gate. Public multi-user accounts require separate tenant identities.
5. Keep reverse-proxy logs from recording OAuth callback query strings. The ASGI
   callback removes its query string before the app access log is written. The
   proxy must independently redact it. Do not enable requests HTTP debug logs.

Publishing uses the existing persistent video directory's `publishing/` folder.
Its SQLite queue survives application restarts. Every enabled application process
runs a small queue poller. SQLite leases prevent two workers from transferring
the same job concurrently. Network requests have bounded timeouts, and leases
are renewed between chunks. Disconnect removes the local grant immediately and
retains an encrypted revocation task until Google accepts revocation. In-progress
requests can finish before revocation takes effect.

OAuth requests only `youtube.upload` and `youtube.readonly`. The read scope is
needed to verify channel identity, uploaded metadata, processing and actual
visibility. PKCE, a one-use ten-minute state and a separate HttpOnly, Secure,
SameSite=Lax browser cookie bind the callback to the owner-initiated browser.
The existing Strict owner-session cookie stays unchanged. Owner mutations require
the same-origin header. The callback uses its browser-bound state instead.

## Review and upload

Open **Launch Studio** from the dashboard, unlock owner access, connect YouTube,
and explicitly select the returned channel. Choose a saved campaign and the
approved existing MP4. The 90 MB import limit matches existing social exports.
Technical checks require a decodable 9:16 H.264 MP4 with AAC audio, up to 180
seconds. The source file is neither rendered nor transcoded. Its SHA-256 digest
is checked again before transfer.

Enter the exact owner-approved marketing URL, title, description, audience,
synthetic-media disclosure and desired visibility. The description must retain
the exact URL. Recognizable Amazon ASIN mismatches are rejected. Short and
affiliate URLs require the owner's exact-product destination review. No URL is
rewritten and no redirect or retailer network request is performed. The owner
must verify the profile destination and avoid implying that Shorts description
URLs are clickable. No claims or captions are invented by this integration.

**Review YouTube preview** saves a draft without contacting YouTube's upload
endpoint. The owner can edit a draft. **Approve & Publish** atomically freezes
the snapshot and queues the upload. A stale review digest is rejected. The same
media hash and channel cannot produce another job even when its copy changes.
Manual historical uploads cannot be inferred reliably from file hashes in the
YouTube API. The owner must confirm that the video was not already posted,
especially the initial *A Season for Everyone* master distributed manually.

The queue persists a resumable-session URL before sending bytes, probes the
server's received offset, and uses 8 MiB chunks. An ambiguous final response is
reconciled through the same session before continuing. Missing/expired sessions
or uncertain initiation require attention rather than a fresh upload. There is
no automatic second insert. Safe transient attempts are bounded. The owner can
request **Reconcile existing upload** for a saved session or video ID.
After reconnecting an expired/revoked grant, that control verifies the same
selected channel again before resuming the existing session. It renews only the
credential binding and records the owner action. The approved media and metadata
stay frozen. An approved job interrupted before upload initiation can also
resume. An uncertain initiation without a saved session remains blocked against
duplicate submission.

Returned IDs are stored before metadata verification. The UI distinguishes
queued, uploading, uploaded, processing, private, unlisted, public, failed and
needs-attention states. A public watch URL appears only after API verification
of identity, metadata, audience/disclosure, successful processing and actual
Public visibility. A private or restricted upload keeps its actual status and
an owner YouTube Studio link. TrafficLift never changes visibility after upload.
Use **Verify YouTube status** to refresh terminal upload status after changes in
YouTube Studio.

## Verification and release boundary

Run the offline suite with
`python -m pytest backend/tests/test_youtube_publishing.py -q`.
Mocks and generated local fixtures cover OAuth, owner access, duplicate clicks,
immutable approvals, interrupted uploads and truthful status. Run the existing
Pinterest, provider, video identity, owner access and export regressions as well.
The publishing CI job has no provider credentials.

Live OAuth credentials, Google project audit/access and channel eligibility must
be checked by the owner before live acceptance. No actual account connection or
upload is authorized by implementing this PR. After explicit owner authorization,
use an existing, previously unposted MP4. Verify the chosen channel, saved video
ID, exact metadata, processing and actual visibility via API, then click twice
and confirm a single persisted upload. Test restart reconciliation using the
same session. Record evidence without tokens. Keep the PR unmerged and undeployed
until review and authorized verification are complete.

## Official protocol references

- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/youtube/v3/docs/channels/list
- https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- https://developers.google.com/youtube/v3/docs/videos/insert
- https://developers.google.com/youtube/v3/docs/videos
