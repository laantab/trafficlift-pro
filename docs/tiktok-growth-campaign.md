# TikTok 30-Day Plan (4your Good Health, @hammer2085)

Open **/growth** on TrafficLift Pro, or use the "TikTok 30-Day Plan" link at the top of Owner Studio.

## What it does

- Shows today's video: topic, hook, four-scene script, what to show, on-screen words, caption and hashtags.
- Checks the script automatically: matches the topic, short enough to caption, no unsupported health or
  results claims, personal results labeled, plain ending with no engagement bait, caption and hashtag length.
- Makes a free narrated vertical video from 1 to 4 of your own photos, using the same renderer and release
  checks as Owner Studio campaigns. You tick four review boxes before the download unlocks.
- Logs each post's results and tells you what to repeat and what to adjust.

## What it never does

- It never posts, comments, follows or buys followers. You upload each video to TikTok yourself.
- It never uses a paid video service. Narration and rendering are the free built-in ones.
- It never promises views or followers. The "pace needed" figure is a yardstick only.

## Daily checklist

1. Open /growth. Read "Your next step".
2. Film the four scenes on your phone, or add photos and click "Make the video".
3. Play the video. Tick the four review boxes. Download it.
4. Upload it to TikTok yourself. Paste the caption with "Copy caption and hashtags".
5. The next day, type that video's numbers into "Log the results".
6. Once a week, read "What to repeat, what to adjust" and swap a weak day for more of what works.

## Setup

None. The plan starts the first day you open the page. Use "Change the day the plan started" to move it.
Free videos count toward the same daily video limit as Owner Studio campaigns.

## Files

- `backend/growth_plan.py` — the 30 days, script checks and results advice (plain data, no network).
- `backend/growth_api.py` — owner-only page data, free video maker, results log.
- `static/growth.html` — the page.
- `backend/campaigns.py` — one added line so a topic video can set its own opening words.
- `backend/tests/test_growth_plan.py` — tests.
