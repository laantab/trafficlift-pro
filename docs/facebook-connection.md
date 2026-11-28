# Facebook connection

Session record, 27 Nov 2026. TrafficLift Pro now posts Reels to a Facebook Page
through its own Meta app. Everything below is live on
`https://trafficlift-pro.onrender.com`. No code changed in this session; it was
account and settings work.

## What exists now

| Piece | Value |
| --- | --- |
| Facebook Page | **Keep It Real Now** (Page ID `1421689971036286`), Business type, website `https://www.keepitrealnow.com` |
| Facebook account | The owner's newer personal account (the one with no older Pages) |
| Meta developer account | Registered and verified by phone on that same account |
| Meta app | **TrafficLift Pro**, App ID `1200551115634870`, status **Published (Live)** |
| Use case | Manage everything on your Page |
| Permissions | `pages_show_list`, `pages_read_engagement`, `pages_manage_posts` |
| Redirect link | `https://trafficlift-pro.onrender.com/api/v1/facebook/callback` |
| Privacy policy | `https://keepitrealnow.com/privacy-policy/` |
| Render settings | `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET` saved on the `trafficlift-pro` service |
| TrafficLift | Page connected: `/api/v1/facebook/status` reports `connected: true`, `page_name: Keep It Real Now` |
| Autopilot | Comic Book Creator: YouTube Shorts **and Facebook** on, daily at 09:00 America/Los_Angeles |

The permission grant covers the Keep It Real Now Page only, not future Pages.

## Things worth knowing

- The owner has **two Facebook accounts**. His older Pages are on the other
  account. This setup uses the account above, which had no Pages, so a new one
  was created.
- Meta refuses a phone number on the developer sign-up page until the same
  number has been added in Accounts Center → Personal details → Contact info.
  That is why the first verification text never arrived.
- The Owner Studio access code is the Render setting
  `TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN`.
- The App Secret was pasted into Render by the owner. It is not written down
  anywhere else, including here.

## Not yet verified

No Reel has posted yet. Autopilot had already run for 27 Nov, so the first
Facebook Reel is due **28 Nov at 09:00 Pacific**. Check the Page after that.

## Open items for the next session

1. **Confirm the first Reel posted** to Keep It Real Now and is visible to the
   public. If it failed, read the Render logs for `facebook_publisher`.
2. **Owner Studio has no Facebook controls.** `static/campaigns.html` sends the
   Autopilot form without a `facebook` field, so pressing **Save Autopilot**
   there turns Facebook back off (the API defaults it to `false`). Facebook was
   switched on by posting to `/api/v1/autopilot` directly. Add a "Post to
   Facebook" checkbox and include `facebook` in the save.
3. **No "choose your Page" screen.** After Facebook sends the owner back to
   `/owner?facebook=choose-page`, nothing is shown. The Page was selected by
   posting to `/api/v1/facebook/page` directly. Add the picker, a "Connect
   Facebook" link, and a connected/not connected line.
4. **`/api/v1/facebook/status` (owner-only) returns more than it needs** in
   `choices` while a Page choice is pending. Return only `id` and `name`.
5. The Page has no profile picture or cover photo yet.
6. The 30-day plan videos (4your Good Health) can also go to Facebook through
   `/api/v1/growth/facebook/settings`. That is still off.

## Picking this up in a new chat

Say: "Continue the Facebook connection for TrafficLift Pro. Read
`docs/facebook-connection.md` in `laantab/trafficlift-pro` and start with the
open items."
