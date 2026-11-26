# Pinterest connection

Owner Studio signs the owner in to Pinterest through Pinterest's own permission
screen. TrafficLift then keeps an encrypted, self-renewing grant in the existing
publishing store, so scheduled Pins keep posting without a pasted token.

## Server setup

1. In the Pinterest app (My apps → Manage), add this redirect link exactly:
   `https://trafficlift-pro.onrender.com/api/v1/pinterest/callback`
2. Save `PINTEREST_APP_ID` and `PINTEREST_APP_SECRET` in the deployment's private
   settings. `TRAFFICLIFT_PUBLISHING_KEY` (already used for YouTube) encrypts the grant.
3. Open Owner Studio, open **Schedule a Pinterest video Pin**, and choose
   **Connect Pinterest**.

The requested permissions are `boards:read`, `pins:read`, `pins:write` and
`user_accounts:read`. The one-click token on the Pinterest Manage page is
read-only and cannot publish. `TRAFFICLIFT_PINTEREST_TOKEN` remains a fallback
when no owner grant is saved.

While the Pinterest app has Trial access, Pins it creates are visible only to
the account that created them. Public Pins need Standard access (Upgrade).
