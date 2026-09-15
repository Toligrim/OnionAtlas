# External discovery sources

OnionAtlas never treats a third-party index as canonical truth. External sources only produce **candidates**. Every candidate is validated, deduplicated, recorded as provenance and then independently checked by the OnionAtlas Tor worker.

## Built-in Ahmia recent-submissions source

The first v0.1 source is intentionally small and conservative:

```text
https://ahmia.fi/add/onionsadded/
```

The current Ahmia code exposes this route through `AddListView` as `add/onionsadded/`. It lists onion URLs submitted to Ahmia. Ahmia's own maintenance documentation describes periodic deletion of added onions, so this endpoint is useful as a rolling source of fresh candidates rather than a canonical archive.

Enable it once:

```text
onionatlas discovery add-ahmia
```

Default interval: six hours.

After that, the discovery timer calls `discovery run-due`; OnionAtlas extracts v3 onion candidates, verifies their checksum, records the discovery run and evidence, and enqueues new targets for its own Tor crawler.

### Why the full Ahmia onion list is not the default

Ahmia also exposes `/onions/`. Its current `OnionListView` aggregates the index with a configured upper size of hundreds of thousands of domains. Pulling that entire response on every discovery tick is wasteful and would create a large burst of mostly-known candidates.

The full list can later be used as a **one-time/bootstrap import** with streaming and checkpoints, but it is intentionally not part of the first continuous loop.

## Generic HTTP regex source

An administrator can add another text/HTML/JSON source:

```text
onionatlas discovery add-http-source SOURCE_NAME URL_TEMPLATE
```

If the URL uses `{query}`, multiple `--query` values can be configured.

The adapter:

1. downloads only a bounded text response;
2. extracts strings that look like v3 onion hostnames;
3. passes every value through OnionAtlas's cryptographic v3 hostname validator;
4. deduplicates candidates;
5. stores run statistics and provenance;
6. enqueues only OnionAtlas-valid candidates.

The adapter must not be pointed at untrusted internal URLs. In v0.1 source configuration is administrator-controlled; stronger egress/SSRF policy is part of production hardening.

## Novelty and cooldown

For each run OnionAtlas stores:

- raw candidates;
- valid candidates;
- unique candidates;
- newly discovered services;
- already-known services;
- rejected candidates;
- novelty rate.

When a source repeatedly returns a sufficiently large set with novelty below the configured v0.1 threshold, it receives a deterministic cooldown instead of being polled aggressively.

Inspect source state with:

```text
onionatlas discovery stats
```

## Provenance rule

A third-party source proves only that an address was observed there. It does **not** prove ownership, legitimacy, legality, availability, or identity of the service. Those concepts must remain separate in the data model.
