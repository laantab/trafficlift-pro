# LongCat Distilled trial

The owner-only video engine adds one silent product clip from a verified seller
photo. It is disabled by default. This integration uses fal.ai, not a subscription
to a similarly named LongCat website, and needs no GPU on Render.

## Configuration

On the TrafficLift Pro Render service, retain the existing owner video setup
(`TRAFFICLIFT_HOSTED_VIDEO=1` and `TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN`). Configure
`FAL_KEY` server-side and set `TRAFFICLIFT_LONGCAT_VIDEO_ENABLED=1` only when ready
to test. Never paste keys into the dashboard or commit them. Setting the flag to
`0` disables LongCat without changing other engines.

After deploying this change, sign in with the existing owner video access flow,
select a seller-listed product, and open Video → Other video engines and advanced
options → LongCat Distilled. Check setup (free), then confirm one paid generation.
The player and link appear after polling the existing job. Refreshing the same tab
retains a known pending job ID and resumes polling. A network error during
submission can leave the provider job state uncertain: check fal.ai history before
resubmitting. The same tab blocks resubmission in that case; close it only after
checking provider history. There is no automatic generation retry.

## Cost and scope

The fixed model is `fal-ai/longcat-video/distilled/image-to-video/720p`, using
162 frames at 30 fps (about 5.4 seconds). At the published $0.01 per generated
second, the estimate is $0.054 per clip. This estimate is not a billing cap.
100 generated clips would be about $5.40, including unusable attempts.

This is a silent visual trial. Narration, captions, finished ad assembly, and
human presenter generation are not included. Framing follows the source image;
9:16 output is not promised. Compare product shape, markings, color, framing,
and motion against the seller photo before using a result. Measure cost per
usable clip when comparing with Wan or HeyGen.

Verified provider references (2026-11-04):
- https://fal.ai/models/fal-ai/longcat-video/distilled/image-to-video/720p
- https://fal.ai/models/fal-ai/longcat-video/distilled/image-to-video/720p/api

## Verification

- `python -m pytest backend/tests/test_longcat_video_api.py backend/tests/test_wan_video_api.py -q`
- `node backend/tests/longcat_video_ui.cjs`

These use mocked provider responses and spend no credits. They do not establish
render quality, live billing, or production credentials. A real paid clip is
still required to evaluate the model in TrafficLift Pro.
