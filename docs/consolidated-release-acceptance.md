# TrafficLift Pro — consolidated release-candidate acceptance

**Scope freeze (28 Oct 2026):** Finish the agreed rebuild. No new winner-history refinements, new features, paid creative providers, or changes to working owner access, render limits or hosting plan. Baseline production commit: `ad253ca2503c3199eac3fb8d66f17c116d85d94b` (PR #148). This runbook is an acceptance gate, **not** a claim that the application passed it.

## One release candidate, one decision

Acceptance reference: actual JAS11242 motion-sensor night-light, old video job identified by user as `3bbd13d1` (short identifier only; **not** a valid full 32-character job ID for API scripts). The old 30-second MP4 **failed** marketing review: duplicate catalog photo, empty background, generic captions and narration, illegible subtitle typography. The old 1000×1500 pin used generic “Explore Tech & Gadgets Ideas” wording. Do not recertify an old file merely because the new code is deployed.

1. **Safety and recovery gate (no new renders):** CI passes all required jobs. Production health + video readiness pass. Unauthenticated history, job, MP4 and each of three platform downloads return 401. Owner cookie expiration must preserve existing job ID, allow secure reauthentication, and rejoin polling/history **without POSTing another render**. Verify in actual authenticated browser before asserting live pass.
2. **Product evidence gate:** Obtain the actual listing, precise variant/model, two distinct seller-supported product claims and rights-cleared exact-product source photos of sufficient resolution. Record source, permissions and model/variant match in private QA evidence; do not place access codes in tickets/logs. Do **not** substitute synthetic lifestyles or other products. If evidence is insufficient, mark **BLOCKED: product evidence** and do not generate an advertisement.
3. **Pinterest gate:** Every download route must yield a genuinely designed 1000×1500 product-specific pin. No enlarged raw retailer photo, generic category headline, stale previous-winner pin, or unverified claims. Inspect downloaded pixels as well as dimensions.
4. **Video creative gate:** Use one verified product identity with seller-supported details. Reject generic placeholders and raw listing fragments; avoid repeating same photo and fact, excessive dark empty area, tiny or clipped captions and overlapping CTA. Check narration against script and inspect actual decoded frames at 2, 7, 12, 18, 24, 29 seconds **if applicable to that video's duration**. A synthetic CI fixture is only engineering evidence, not JAS11242 advertising approval. No new customer render unless evidence gate passes and owner explicitly approves expected cost/quota.
5. **Existing-master export gate:** Prefer an already completed **post-fix** MP4, with its full private job ID, over a new render. Once authenticated, check playable H.264/AAC 1080×1920 master, then download three owner-only kits; verify the original MP4 SHA-256 matches the MP4 in every ZIP. Inspect caption SRT, metadata, safe-zone previews and post copy. Exports must **not** trigger another render. The offline and opt-in verifier is `scripts/owner_existing_export_acceptance.py`; do not put owner access code on the command line or in CI logs.
6. **Release decision:** Merge/deploy only if the complete existing regression CI passes and the change addresses a reproduced blocker. Confirm exact merge SHA and Render **live** SHA, post-deploy health/security. Mark `PASS` only after authenticated customer workflow and real-product creative approval; otherwise label the specific blocker `FAIL` or `NOT VERIFIED`, with links to screenshots, media, GitHub CI and Render deployment. Do not send the user back to repeat a test until engineering checks pass.

## Evidence checklist (fill out, never infer)

| Gate | Status | Objective evidence |
| --- | --- | --- |
| GitHub required CI | NOT VERIFIED for final candidate | Run URL, four job conclusions |
| Render live candidate + health | NOT VERIFIED for final candidate | Commit SHA, deployment ID, dated health responses |
| Post-deploy anonymous protections | NOT VERIFIED for final candidate | Dated 401 requests for every protected route |
| Session expire → reauth → same job | NOT VERIFIED | Browser trace with no duplicate POST |
| JAS11242 licensed exact-product photos and facts | NOT VERIFIED | Private listing/rights evidence |
| JAS11242 designed Pinterest download | NOT VERIFIED | Actual 1000×1500 file and visual review |
| JAS11242 corrected real video | NOT VERIFIED | Actual post-fix MP4, frame sheet, narration review |
| Existing-master TikTok, Shorts, Reels ZIPs | NOT VERIFIED | Full job ID, ZIP hash comparison and safe-zone review |

**Hard stop:** Missing authenticated access or licensed exact-product imagery is a blocker, not permission to claim success, invent facts or create a paid render.
