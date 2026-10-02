# Validation — October 2, 2026

Base main: ed1f46805e018aa9addcf0dee343542af8d82762.

- New Python tests: 21 passed.
- Full branch suite: 249 passed, 2 failed.
- Same-environment main baseline: 228 passed, the same 2 failed.
- Existing failures: test_case_33_wrap_text_handles_long_titles_without_throwing and test_url_resolver_flags_non_amazon_target. Tests were not weakened or changed.
- Every inline JavaScript block: Node syntax check passed.
- Node DOM contract: preview after import, default free engine, current-product request, checked playback/download and failed-output withholding passed. This is not a real browser test.
- Real compositor acceptance: Sunset Projection Lamp, original seller photo, supplied seller fact, measured speech, burned captions, original music, 15-second 1080×1920 H.264/AAC. Full FFmpeg decode passed; exported closing frame inspected, no clipping or product distortion.
- API acceptance with fixture image transport and actual compositor: create job, duplicate render refused, download withheld before checks, succeeded, MP4 downloaded, history retained. No paid generation or new discovery call.
- Windows launcher, Windows Arial/caption rendering and social upload: not exercised here. Chromium download failed with truncated archives; browser QA remains outstanding.
- Longer 30/60-second selections preserve duration using longer holds rather than generating extra claims. Default 15-second sample is the accepted pacing reference; longer ad pacing needs human review.

No main merge or production deployment is included in this work.
