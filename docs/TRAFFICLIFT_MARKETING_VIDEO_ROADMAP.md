# TrafficLift Pro — Product Marketing & Video Upgrade Roadmap
Updated: 2026-10-19
Status: Approved plan, not an assertion that work is completed.
Scope: laantab/trafficlift-pro only. Never modify Digital Product Factory.

## Product objective
For a novice user, one click should discover one distinct, evidence-backed product with its exact seller photo, prepare a Pinterest-ready pin and a narrated, reviewable promotional video, then expose preview and download. A pasted seller product URL is a separate entry point that uses precisely that listing. Never claim guaranteed sales, demand, profit, or verified features from unverified snippets.

## Non-negotiable safeguards
- Preserve working discovery, URL analysis, pin downloads, campaign history, local video, RTX and optional provider paths.
- Default path uses free/local components; no paid AI or video API call without explicit approval and a clear cost estimate.
- Do not fabricate demonstrations, buyer outcomes, reviews or product specifications. Use seller-supported claims and disclose uncertainty.
- Real matching product images only; block exports when missing or unusable; require image rights review before publication.
- Keep local video loopback-only; don't silently turn on public Render rendering. Cloud subscriber rendering needs authentication, quotas, isolation, durable queues and cost controls.
- CI regression tests and manual playback/browser checks required before merge or deployment; maintain rollback instructions.

## Phase 0 — Stabilize current one-click pipeline (release blocker)
- Confirm button responds in Chrome on the Windows laptop, shows progress and specific failures.
- Verify 1 real winner -> matching photo -> complete pin -> JPEG 1000x1500 download -> saved campaign reopened.
- Test exact-product URL separately, no substituted products.
- Confirm fresh discovery exclusions, missing-provider handling and no repeated product when another is available.

## Phase 1 — Pinterest Marketing Editor (PR #10, in progress)
- Headline starts with the user's job/outcome, not unedited retailer titles.
- Description and board suggestion follow product intent; avoid fixed unsupported character-count or algorithm guarantees.
- Editorial fact checker rejects unsupported promises or seller/source mismatches; clear review state and cite seller source.
- Preview/download identical, text fully visible, verified image, accessible alt-text suggestion based on real image.
- Quality gate distinguishes Marketing-Ready from proven winner; draft until manual rights/claims review.
- Keep one-click default with optional editing after result.

## Phase 2 — More professional product video scenes
- 15/30/60 sec presets, vertical 1080x1920.
- Hook -> buyer problem -> seller-supported solution -> real product/detail view -> one CTA.
- Use multiple licensed or approved real listing photographs, detail crops and tasteful motion; never generate fake product actions.
- Smooth transitions, stable framing, safe text margins and reliable subtitle rendering.
- If only one image is available, tell the user and produce an honest photo showcase, not a fabricated live demo.

## Phase 3 — Narration and sound polish
- Optimize free Kokoro narration pronunciation, pauses, pace and emphasis.
- Time captions against measured speech; music ducking; peak and perceived loudness checks.
- Prohibit clipped audio, silence-only movies and unsupported seller claims.
- Test actual video playback on Windows laptop; run full FFmpeg/ffprobe decoding checks.

## Phase 4 — One-button novice workflow and export
- Single primary Find Winning Product button; smaller Use My Product URL entry point.
- Show genuine sequential progress and recover from research/image/render errors.
- Preview selected product photo before rendering; retain recent campaigns and renders.
- Only enable Play/Download after full QA; export JPEG, MP4, pin description, alt text, board suggestion and seller link.
- No pairing codes for CPU video, no accidental provider charges, no repeated instructions or black command window after setup.

## Phase 5 — Subscriber-ready architecture (separate design gate)
- Decide on costed, authenticated cloud rendering versus local companion app; do not promise free unlimited web rendering.
- Add account ownership, job queue and concurrency limits, quotas, video storage retention, cancellation, monitoring and abuse controls.
- Define per-subscriber compute/storage pricing and support boundaries before enabling service.
- Ensure license notices, platform-policy checks and seller image usage rights.

## Release acceptance
1. Zero paid provider calls in automated tests.
2. Existing Python, Node and pin/video suites pass; add focused regression tests per phase.
3. Real browser: research, URL analysis, pin generate/download and saved campaign reopen.
4. Windows: launch, real narration, accurate image, captions/audio, MP4 decode, play/download for all duration presets.
5. Negative tests: 404/503, nonmatching photo, missing facts, long title, provider timeout, duplicate winner, interrupted job.
6. Approval/rollback: don't merge/deploy while red or while required end-to-end acceptance remains unverified.
