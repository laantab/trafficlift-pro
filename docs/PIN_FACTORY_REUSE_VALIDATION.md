# Pin Factory → TrafficLift validation

This branch exists to run TrafficLift's existing repair-test workflow against the
Pin Factory-inspired production changes already present on `main`:

- accept exact Amazon share-page identity when Amazon exposes productTitle,
  a 10-character hidden ASIN, and the primary landing image;
- prefer the retailer primary product image over gallery/detail images;
- degrade missing seller facts to a claim-safe exact-product overview video
  instead of inventing claims or failing before render.

No paid AI or video-provider calls are required by these regression tests.
