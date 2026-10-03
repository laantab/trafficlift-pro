# TrafficLift product-video sales standard — v2.0

The free studio now runs local evidence-led buyer research, hook selection,
visual planning, and editorial checks before accepting a render. These are
rule-based stages, not live language-model agents or independent seller
verification. No paid API calls were added.

A practical benefit copied from the exact seller listing is required. An
optional buyer need provides a situation-specific opening; an optional
supported buying detail addresses size, care or included accessories. Existing
generated pin copy is not proof. The planner retains three hook candidates,
selects the benefit-led hook, and builds hook → benefit/detail → one CTA.
The closing directs viewers to current price and product details rather than
inventing price or availability.

Reject blank benefits, formatting injection, unsupported proof/guarantee/urgency
phrases, quoted prices without dated evidence, and scripts too long for natural
speech. Actual narration durations are also checked before composition. The
photo-only compositor uses photo motion, never fake performance tests. Every
phrase has scene instructions and a concise caption. Longer scripts need 30
or 60 seconds; the renderer must not truncate narration.

Persist sales_plan in the job record, sales_script.json beside the video,
and the editorial review in quality.json. Preserve supplied claims, source
URL, hook candidates, selected hook, narration and scene directions.
User-entered facts remain labelled user-supplied and not independently verified.
Rule checks cannot prove arbitrary seller claims. Automatic listing extraction,
language-model rewriting, dated price verification and real demonstration
footage are not implemented by this patch.

Video format, full decode, audio, motion, captions and safe margins still run
before Play/Download becomes available. Put the actual shopping link into the
published post; a button drawn in an MP4 is not clickable.
