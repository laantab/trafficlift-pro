# Durable organic campaign pipeline

The owner dashboard is `/pipeline`. It uses the existing owner session; neither
same-origin headers nor the page URL alone authorize reads or writes. All campaign,
queue and metric records use the existing Fernet publishing key on persistent disk.
Media lives under the configured video storage root. Do not rotate that key without
migrating the existing YouTube and pipeline records.

## What is connected

* Existing research discovery can store recommendations, with original provenance.
  It is explicitly enabled separately because the configured research provider may
  consume research credits. Curated fallback scores are not measured sales evidence.
* Eligible campaigns bind a canonical product, unchanged marketing URL, resolved
  destination, dated market evidence, current stock, media rights and affiliate
  eligibility. Amazon ASIN and marketplace must match. A short-link token is not an
  ASIN. Resolved destinations and eligibility are owner attestations, not a claim
  that the app verified a commission contract or browser-only redirect itself.
* Registered original media can be reused, or locally packaged as a JPEG and a
  12-second silent H.264/AAC photo overview. No text/image/video/voice generation
  API is called. The limited template contains no new performance claims.
* The scheduler selects existing eligible work before requesting more research.
  Default schedule is Monday/Wednesday/Friday at 09:00 America/Los_Angeles,
  with a maximum of three platform-post attempts total per local week. Failed or
  uncertain attempts consume slots conservatively. The policy and next slot are
  persisted; concurrent workers cannot claim the same post/slot. Missed slots do
  not become a catch-up batch.
* Pinterest image creation uses its v5 API and verifies account, public board,
  returned post, copy and destination. YouTube delegates to the existing resumable
  upload queue; a processed Private upload is not reported as public. TikTok uses
  creator-info preflight, FILE_UPLOAD and status reconciliation. Only a public
  post ID is considered a public publication. Instagram remains deferred.
* Pinterest analytics, YouTube public statistics and TikTok video metrics are
  polled at most once per six hours per published post. Status, source and reporting
  window accompany each immutable observation. PROCESSING is unknown, not zero.
* After 72 hours and at least 1,000 comparable impressions, a low outbound click
  rate can trigger one distinct hook/layout variant. No second revision is created
  blindly, and views alone do not justify a conversion claim. Enough clicks prompt
  destination/offer review rather than an invented sales diagnosis. Versions keep
  their own ledger entries. New visuals are local packaging of the verified photo.

## Operator setup before activation

1. Preserve the existing hosted owner secret, publishing key, disk and YouTube grant.
2. Set `TRAFFICLIFT_PIPELINE_ENABLED=1` to start the bounded worker. This alone does
   not publish anything: the owner policy defaults disabled.
3. Configure the server Pinterest token, username and public board ID. Pinterest
   Standard access is required; trial credentials cannot publish to production.
4. For TikTok, configure a valid token for the intended username and approved
   Direct Post application. Check `video.publish`, `video.list`, current public
   visibility options, app eligibility and TikTok's Content Sharing Guidelines.
   Do not assume a browser login, Codex connector grant or app review transfers to
   this server. TikTok's required creator-facing consent/UX requirements still apply;
   autonomous operation must not be enabled for an integration that does not satisfy
   those requirements. Expired grants require renewal by the operator.
5. Register each eligible campaign and import its exact product photo/existing MP4.
   The current baking mat's marketing link resolves to **B0HLRWFBHF**, not the older
   **B0HJ7Z522R** test campaign. Do not attach it to the older product automatically.
6. Import already-published work using the owner-only history endpoint before
   enabling autonomous selection. Record its platform account, actual media SHA256,
   public URL and original posting time. Owner-recorded history is labelled as such,
   not passed off as API verification. The bytes/account/platform dedupe key prevents
   caption-only reposts. Do not recreate the currently live baking-mat posts.
7. Check the generated preview and actual destination, then enable the owner policy.
   Use `POST /api/v1/pipeline/tick` for a bounded due-work check; it cannot bypass the
   due time or weekly cap. There is no hidden "publish now" bypass.
8. Once the server pipeline is verified and active, disable the overlapping Codex
   scheduled posting routine to prevent two independent coordinators posting.
   This PR does not silently alter that routine or the live service.

## APIs

* `GET /api/v1/pipeline/status`: policy, capabilities, campaigns, recommendations,
  assets, posting ledger, metric observations and strategy assessments.
* `PUT /api/v1/pipeline/policy`: validated schedule/authority; no credentials.
* `POST /api/v1/pipeline/research`: explicit opted-in existing discovery.
* `POST /api/v1/pipeline/campaigns`: register or refresh verified eligibility.
* `POST /api/v1/pipeline/campaigns/{id}/assets`: bounded streamed original import.
* `POST /api/v1/pipeline/campaigns/{id}/package`: bounded local photo packaging.
* `POST /api/v1/pipeline/campaigns/{id}/prepare`: immutable queued publication.
* `POST /api/v1/pipeline/campaigns/{id}/history`: owner-recorded historical post.
* `POST /api/v1/pipeline/posts/{id}/metrics`: windowed observations for that platform.
* `POST /api/v1/pipeline/posts/{id}/reconcile`: inspect a saved remote receipt without creating another post.
* `POST /api/v1/pipeline/tick`: reconcile, measure and dispatch due work.

## Failure and attribution boundaries

An attempt is persisted before a remote write. A crash during create or a timeout
becomes `needs_attention`; it is never blindly resent. Receipts and resumable YouTube
sessions are reconciled separately. Token, account, asset, campaign revision or
evidence changes stop the affected item. Do not delete a ledger row to retry.

Evidence expires after seven days; refresh it through a fresh market/availability
check rather than silently extending dates. Research recommendations without an
eligible link and rights-verified media remain recommendations. The app does not
automatically enroll affiliate programs or promise an item is profitable.

No pixel is installed on Amazon's checkout. Views and clicks do not establish sales.
Shared-link Amazon reports are rejected as individual-post sales attribution. Use
approved platform-specific tracking IDs and genuine affiliate reports if available.
Orders/commission are not synthesized. The app currently has no Amazon report importer.

## Validation and rollout

Run `python -m pytest backend/tests/test_pipeline.py backend/tests/test_youtube_publishing.py`.
Tests use temporary storage and mocked remote platforms, including real local media
encoding/decoding. No live generation or publication is part of CI. Also run the
existing campaign, media, discovery, owner-auth and pin regression checks. Review the
PR, configure server credentials privately, verify an owner-approved distinct test
campaign end-to-end, then decide whether to merge/deploy/enable. Offline adapter tests
are not evidence of live app approval or credential availability.

Primary platform references:

* https://developers.tiktok.com/doc/content-posting-api-get-started/
* https://developers.tiktok.com/doc/content-sharing-guidelines/
* https://developers.pinterest.com/docs/api/v5/pins-create/
* Existing YouTube implementation and `youtube-publishing.md`.
