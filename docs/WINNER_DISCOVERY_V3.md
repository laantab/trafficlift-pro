# Fresh product discovery v3

Find Winning Product rotates across twelve categories, three bounded live
queries per request, and retailer/open-web query variants using the current
month/year. It gathers up to twelve concrete products, excludes previously
shown products before image work, qualifies up to six candidates concurrently,
then selects the strongest qualified research evidence. Equal evidence uses
random tie-breaking; random selection is not proof of demand.

Evidence must mention the exact normalized product name, not merely a shared
word. At least one matching source must contain review/popularity/sales terms.
The reported 0–60 score measures source breadth and those terms, not measured
sales or profitability. Actual demand, competition, margin, publication age,
and future sales are not independently established by search snippets.
Returned source links, provider, retrieval time, attempted queries/candidates,
and score meaning make the result inspectable.

The fresh-only endpoint never calls the curated pool. Provider outages return
503; no new or photo-qualified product returns 404. These messages are part
of correct operation, not a reason to recycle an old product. Existing URL
analysis and saved-list browsing remain separate workflows.

## Repeat prevention

Stable SHA-256 identities replace process-dependent Python hashes. Normalized
names and canonical URLs (without common affiliate/tracking parameters) prevent
exact-title and same-URL repeats. Full-name candidate deduplication preserves
different models with shared brand prefixes.

An anonymous random browser ID scopes SQLite history. Accepted identity keys
are atomically claimed for thirty days, including across worker processes;
two concurrent requests cannot reserve the same product. Browser storage keeps
the last hundred results and sends their IDs/keys across refreshes. It refuses
legacy saved-list results and repeats returned by a mismatched backend.
History is recorded when a winner is accepted, before Pinterest generation,
so a downstream pin failure cannot make discovery recycle that product.

History does not span other browsers/devices. Clearing browser storage creates
a new identity. Name changes and different retailer URLs without common model
identifiers may describe the same physical product; semantic SKU matching is
not claimed. HTTP response loss can reserve a product the user never saw.

## Runtime

Set TRAFFICLIFT_DISCOVERY_DB to a SQLite file on the service's persistent disk,
for example /var/data/discovery.sqlite3. Default: data/discovery.sqlite3.
Without durable hosting storage, browser history still survives refreshes but
server history/category rotation can reset on redeployment. Multi-host service
instances need shared database storage; local SQLite covers workers on one host.

Research phase: eleven seconds; request research/qualification budget: thirty-two
seconds; three worker threads per phase. Pending operations cancel at deadline;
already-running HTTP calls finish under provider timeouts and cannot reserve a
winner. Browser timeout: seventy-five seconds, including cold-start allowance.
No live research or product-generation calls were made by the new regression
tests; all new provider/qualification paths are mocked.

## Validation

Regression coverage includes twenty distinct successive selections, stable IDs
across processes, tracking URL canonicalization, history reopening, browser
scope, atomic concurrent reservations, exhaustion without repeats, failed first
photo, evidence isolation, missing market signals, provider failure, and request
validation. Node tests cover storage persistence, malformed history, expiration,
and history/exclusion parameters. CI runs the new and existing suites.

Real provider behavior and live browser acceptance must still be verified on
the deployed service. No finite test suite can guarantee that external search
providers always return qualified new products.
