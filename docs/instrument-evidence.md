# Instrument observations and time review

A current instrument endpoint cannot reconstruct a historical trading universe. Tidebench v0.7 starts retaining immutable forward observations so a trader can inspect the evidence behind today's rules without overwriting its chronology.

## Workflow

Open **Data library → Instrument evidence**. Choose spot or USDT perpetuals and capture a response. Research package preparation also captures an observation when it needs fresh metadata. The selector shows the latest 100 observations; the UTC time query searches all retained observations. Inspect supported, unavailable and outside-scope members, filter by market/state/missing rule, review changes against the preceding response, or export the original observation.

A healthy member remains usable when another row has missing preopen sizes, invalid numbers, conflicting identifiers or unsupported contract units. Those rows remain in the observation with their actual reasons. Duplicate instrument rows are quarantined rather than choosing one arbitrarily. A newer complete endpoint observation that omits a symbol blocks use of its older cached metadata. Passing a previously observed expiry time also blocks current eligibility while metadata is cached. These controls do not liquidate inventory or invent settlement prices.

## What is captured

Each observation has a unique ID, source, region, requested instrument type, request/receipt times, exact receipt ordering, endpoint path, transport, parser version, full original `data` array, payload hash and complete observation hash. Update/delete triggers protect the records. Repeated identical responses have the same payload hash and different observation identities: A → suspension → A remains three observations.

The capture is the decoded REST **data array**, not the byte-for-byte HTTP envelope, headers or actual fallback hostname. Unrecognized rows remain in the payload. The supported execution scope remains USDT spot and linear USDT perpetuals with reviewed contract units. Synthetic captures retain an explicit Example source; they are not OKX observations.

Current metadata binds `observation_id` and `metadata_known_at`; datasets capture this provenance with the economic rules. Observations do not retrospectively change published datasets, sealed computations or old runs. Parser version 1 defines this release's interpretation. Future semantic changes must retain a versioned interpretation; they must not silently reinterpret old evidence as new facts.

`listTime` and `expTime` are endpoint declarations. They are separate from when this workspace received the information. An old listing timestamp in a response received today cannot establish what was known last year. At an announced future listing, the software also does not predict that a preopen member will become live without another observation.

## Time query

The query uses UTC milliseconds and the latest observation received by that time. Its universe is the members of that particular endpoint response, **not every asset ever listed**.

- Before any observation: unknown coverage, no manufactured members.
- At the recorded observation time: point observation.
- After it, within the explicitly chosen age: a bounded carry-forward assumption. This does not prove continuous tradability between observations.
- Beyond the age: every observed member is unknown, including previously healthy members.

A diff's `first_observed` / `not_observed` describes the two responses. It does not establish original listing, delisting, conversion or asset destruction. Unknown/outside-scope members remain visible. The response always declines historical-completeness claims.

## API and exact export

Authenticated endpoints under `/api/v1/pro/catalog`:

| Endpoint | Behavior |
|---|---|
| `POST /instrument-observations?source=okx&inst_type=SPOT` | Explicit capture; admin/trader/researcher, with cookie CSRF |
| `GET /instrument-observations` | Latest 100 observations in selected source/region/type |
| `GET /instrument-observations/{id}` | Stored raw rows and interpreted members |
| `GET /instrument-observations/{id}/diff?previous={id}` | Same-scope chronological comparison |
| `GET /instrument-observations/{id}/export` | Original observation plus its canonical hash |
| `GET /instrument-universe?as_of={utc_ms}&max_age_ms=3600000` | Causal observation selection and explicit coverage policy |

Receipt nanoseconds are strings in JSON to preserve exactness in browsers. Verify a downloaded export without its presentation fields:

```python
import hashlib, json
with open('instrument-observation.json') as stream:
    exported = json.load(stream)
canonical = json.dumps(exported['observation'], sort_keys=True,
                       separators=(',', ':'), allow_nan=False)
assert hashlib.sha256(canonical.encode()).hexdigest() == exported['content_hash']
```

## Recovery and acceptance

Schema 7 adds immutable observation evidence. Verified same-schema restore unions newer observations before financial replacement; conflicting identities or invalid hashes abort replacement. The latest retained observation remains authoritative for current lookup even if the restored legacy metadata cache is older. Financial tables retain ordinary restore semantics.

The counterexamples cover blank preopen rows, invalid numeric/asset units, duplicates, empty-response omission, A → B → A, future-information exclusion, stale coverage, announced expiry, immutable storage, exact exports, roles and actual recovery with newer observations. A hash-valid but conflicting identity is rejected before replacement. Desktop/mobile workflows exercise capture, inspection, export, historical unknown coverage, age expiry and spot/perpetual switching. The [public observation audit](audit/v0.7.0-public-instrument-observations.json) retains counts/hashes from a real public request without redistributing its raw market metadata.

This completes the first evidence stage in the [historical-universe design](historical-universe-design.md). Attributed historical metadata imports, complete historical membership, dynamic selection and position-bearing delisting/conversion settlement remain unimplemented. A published time or absent symbol alone supplies no safe conversion or settlement value.
