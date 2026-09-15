# External discovery sources

OnionAtlas never treats a third-party index as canonical truth. External sources only produce **candidates**. Every candidate is validated, deduplicated, recorded as provenance and then independently checked by the OnionAtlas Tor worker.

## Source policy

A technically reachable endpoint is not automatically an appropriate automated discovery source.

Before adding a third-party source, verify:

- its Terms of Service or license permit automated retrieval for the intended use;
- relevant rate limits are known;
- the source can be queried without credentials being exposed in a URL;
- the source is not used as canonical truth;
- polling frequency and response-size limits are conservative.

For the first staging deployment, manual seeds plus link discovery are sufficient. External discovery should be enabled only after the end-to-end crawler loop is proven stable.

## Ahmia

Ahmia is a useful Tor search/reference service and publishes information about onion services. OnionAtlas contains an experimental helper for a recent-submissions endpoint, but it is **not part of the default autonomous staging configuration**.

Ahmia's public Terms of Service currently state that users must not scrape or replicate the service without permission. Therefore do not enable automated Ahmia polling unless the operator has explicit permission covering that use and follows the service's rate/usage requirements.

The existence of the CLI helper must not be interpreted as permission to consume the endpoint automatically.

If permission exists, the candidate pipeline remains:

```text
Ahmia observation
→ candidate only
→ OnionAtlas v3 validation
→ dedupe/provenance
→ frontier
→ independent Tor fetch
```

Without such permission, use manual seeds, a dataset/API with a suitable license, or another source the operator is authorized to automate.

## Generic HTTP regex source

An administrator can add another text/HTML/JSON source:

```text
onionatlas discovery add-http-source SOURCE_NAME URL_TEMPLATE
```

If the URL uses `{query}`, multiple `--query` values can be configured.

The v0.1 adapter deliberately has a narrow network policy:

- source URLs must use HTTPS;
- URL userinfo is rejected;
- HTTP redirects are not followed;
- response body is bounded;
- candidate count is bounded (default 5000 per query/run).

The adapter then:

1. extracts strings that look like v3 onion hostnames;
2. passes every value through OnionAtlas's cryptographic v3 hostname validator;
3. deduplicates candidates;
4. stores run statistics and provenance;
5. enqueues only OnionAtlas-valid candidates.

This adapter is intended for trusted administrator configuration, not as an unauthenticated public SSRF-style fetch endpoint. If source configuration is ever exposed to untrusted users, DNS/IP-level egress controls must be added before that interface is shipped.

## Novelty and cooldown

For each run OnionAtlas stores:

- raw candidates;
- valid candidates;
- unique candidates;
- newly discovered services;
- already-known services;
- rejected candidates;
- novelty rate.

When a source repeatedly returns a sufficiently large set with novelty below the v0.1 threshold, it receives a deterministic cooldown instead of being polled aggressively.

Inspect source state with:

```text
onionatlas discovery stats
```

## Provenance rule

A third-party source proves only that an address was observed there. It does **not** prove ownership, legitimacy, legality, availability, or identity of the service. Those concepts must remain separate in the data model.
