# Research data packages

A package is a durable, exact selection of historical research inputs. A trader specifies the source, market, interval and UTC window once. Spot packages require trade candles; USDT linear perpetual packages require trade candles, mark candles and realized funding history. An index candle component is optional. Downloaded history, attributed imports and synthetic examples retain their identities.

`ready` means the historical inputs have passed coverage, hash, instrument-rule and funding-mark validation. It does not certify a strategy, reproduce account settlement prices, or supply historical margin tiers. Research separately captures and discloses its margin-tier scenario and execution assumptions.

## Service contract

```python
from tidebench.data_packages import DataPackageService

packages = DataPackageService(store, catalog)

package = packages.create_package(
    "BTC-USDT-SWAP", "1H", start_ms, end_ms, "okx",
    include_index=False,
    idempotency_key="desk-request-2026-10-08-001",
    dataset_ids={"funding": attributed_import_dataset_id},  # optional
)

packages.get_package(package["id"])
packages.list_packages(source="okx", limit=100)
packages.cancel_package(package["id"])
packages.retry_package(package["id"])
packages.reconcile_package(package["id"])
packages.reconcile_pending()
await packages.prepare_package(package["id"], max_observations=100)
await packages.advance_pending()

manifest = packages.get_manifest(package["id"], verify=True)
inputs = packages.research_inputs(package["id"])
```

`DataPackageError` extends `MarketError` and exposes `code`, `message` and `status`. Input errors use 422, missing packages/datasets use 404, state conflicts use 409 and capacity limits use 429. Underlying catalog validation continues to expose `MarketError`; HTTP translation belongs to the API layer.

All ranges are half-open `[start,end)` UTC epoch milliseconds, aligned to the chosen bar. Supported intervals are `1m`, `5m`, `15m`, `1H`, `4H` and `1Dutc`. A package accepts 2–98,000 bars. Real-source windows cannot end in the future. The workspace allows five active packages and twenty active catalog downloads; allocation checks both limits in one transaction. Preparation accepts at most 100,000 funding events in a package and 1–1,000 new funding observations per call.

`dataset_ids` is a partial mapping from `trade|mark|funding|index` to exact published dataset IDs. Only required/selected kinds are accepted. Every bound version must match source, configured region, instrument, kind, interval and both range boundaries exactly. Larger overlapping datasets are not silently cropped. All remaining components receive private new catalog jobs. Packages do not guess the newest version or share an active job with another package.

Without an explicit key, a canonical request fingerprint supplies idempotency. Identical requests return the existing package, including a canceled or blocked package. A new version requires a new explicit key. With an explicit key, changing any input produces `idempotency_conflict`. A client should generate one key per intentional submission and retain it for transport retries.

## Returned state

The service returns the following shape; the HTTP layer may omit the full manifest from list/detail responses and expose it through an explicit download route.

```json
{
  "id": "package-id",
  "source": "okx",
  "region": "global",
  "inst_id": "BTC-USDT-SWAP",
  "bar": "1H",
  "start": 1780272000000,
  "end": 1780358400000,
  "include_index": false,
  "status": "preparing",
  "ready": false,
  "progress": 0.9,
  "error": null,
  "blockers": [],
  "components": [
    {
      "kind": "funding",
      "job_id": "owned-job-id",
      "dataset_id": "exact-dataset-id",
      "status": "completed",
      "progress": 1.0,
      "error": null,
      "rows": 3,
      "pages": 1
    }
  ],
  "coverage": {"start": 1780272000000, "end": 1780358400000, "complete": false},
  "funding_marks": {
    "captured": 0,
    "total": 3,
    "policy": "attributed_import_or_exact_timestamp_historical_1m_open_approximation"
  },
  "manifest": null,
  "manifest_hash": null,
  "created_at": 1780359000000,
  "updated_at": 1780359000000
}
```

The example shows one component; perpetual responses contain trade, mark and funding, plus index when selected. An explicitly bound published dataset has `job_id=null`, `status="bound"`, `progress=1`, and no download row/page counts. Component state is read from the actual persistent catalog job. Package aggregate state advances through the scheduler, so a child can finish shortly before the aggregate changes on the next reconciliation tick. The progress value reserves the last portion for funding-mark preparation and is not an ETA.

| State | Meaning | Allowed next action |
| --- | --- | --- |
| `queued` | Owned child jobs await dispatch | Cancel or dispatch |
| `running` | Historical downloads have started | Cancel or continue |
| `preparing` | Required dataset versions pass validation; funding marks are being captured | Cancel or continue preparation |
| `failed` | A download or mark preparation failed | Explicit retry or cancel |
| `blocked` | Available history, identity, rules or integrity cannot meet readiness | Inspect blockers, bind corrected versions in a new package, or cancel |
| `canceled` | User canceled this version | Create a new version |
| `ready` | Immutable verified manifest published | Launch research or download manifest |

A blocker has `{kind,code,message}`. Important codes include `funding_history_incomplete`, `dataset_coverage_incomplete`, `instrument_rules_mismatch`, `dataset_integrity_error`, `package_dataset_mismatch` and provider-specific funding-mark errors. A partially completed download set is never research-ready.

## Persistence and scheduling

Creation inserts the package, all missing catalog jobs and their ownership links in one SQLite `BEGIN IMMEDIATE` transaction. A failed allocation rolls everything back. Each package owns a unique child-job set; selecting an already published dataset records its immutable identity without creating another download. There is no second historical-download implementation.

At exclusive process startup, before dispatching workers:

```python
catalog.resume_pending()
packages.resume_pending()
```

The package recovery method releases abandoned preparation claims only. It does not reset catalog worker tokens. These startup recovery methods must not be invoked as runtime retry commands or while another process owns the workspace. The application workspace process lock remains the authority for exclusive startup.

The existing catalog loop dispatches catalog jobs as usual and calls `await packages.advance_pending()` each tick. That method aggregates child state and prepares at most one package with twenty new funding observations, rotating among eligible packages. Partial preparation persists each observation before yielding and releases its own preparation claim. A later tick resumes the remaining observations. This prevents a large historical funding series from monopolizing the catalog scheduler or starving a smaller package. `run_package(id)` is an optional local/CLI driver of existing queued child jobs followed by one preparation batch; large packages can remain `preparing` until additional calls or scheduler ticks complete them.

Preparation uses an atomic package claim. Competing workers cannot capture the same package concurrently. Every observation write and publication rechecks the current state and claim token. User cancellation atomically cancels only owned queued/running catalog jobs, invalidates the package claim and preserves already completed versions for inspection. A late provider response cannot append observations or publish a canceled package. Canceling the async worker during shutdown preserves its committed observations and releases only its matching claim.

Retry is explicit and accepts only `failed`. It requeues failed child jobs with no worker claim, retaining IDs, saved records and pagination cursors. Running siblings keep their worker tokens. A mark-only failure retries preparation against the same datasets and reuses already captured observations. Failed packages do not enter an automatic, unbounded retry loop. A canceled or quality-blocked version is not resurrected.

## Readiness and provenance

Readiness requires current catalog manifest hash schema 3 or later: this is the **dataset manifest version**, independent of the workspace database schema. Current v0.2 catalog downloads/imports already use schema 3. Older dataset schemas without hash-bound quality and instrument metadata are explicitly blocked; create a verified new version instead of silently promoting the old claim.

The service verifies every record hash, complete requested coverage, confirmed/aligned candle grids, no gaps, matching source/region/market/range, and identical captured instrument metadata hashes across components. A price dataset with one missing bar is insufficient. Funding completeness follows its catalog coverage proof or explicitly attributed importer declaration; the service does not infer a regular settlement interval.

For each realized funding event, an imported mark must include a finite positive price, `mark_ts` equal to the event timestamp and a nonempty `mark_price_source`. If no mark is supplied, asynchronous preparation requests the existing catalog settlement-mark helper, which accepts only the confirmed historical one-minute mark candle opening at that exact timestamp. It never substitutes a nearby observation or future close. Missing marks fail preparation. Synthetic packages use the same deterministic absolute-time price series as their synthetic candles and retain explicit synthetic attribution.

The immutable package manifest includes full exact dataset manifests and content hashes, requested UTC window, source/region, instrument rules and version, captured funding observations, a separate funding-observation SHA-256, approximation/model policies and publication time. Its SHA-256 binds the entire manifest. SQLite triggers prohibit subsequent mutation of a published package, its component links and captured observations. `get_manifest(verify=True)` additionally rechecks the referenced dataset hashes and persisted observations before `research_inputs()` can release version IDs to a run.

The research launch inputs are:

```json
{
  "dataset_id": "trade-dataset-id",
  "mark_dataset_id": "mark-dataset-id",
  "funding_dataset_id": "funding-dataset-id",
  "source": "okx",
  "start_ts": 1780272000000,
  "end_ts": 1780358400000,
  "package_id": "package-id",
  "package_manifest_hash": "sha256"
}
```

Spot omits mark/funding; optional index adds `index_dataset_id`. The research runtime captures `get_manifest()["funding_events"]` and the full package manifest in its own run snapshot, so replay binds the package's exact observations without refreshing settlement marks. Current-tier scenario capture and strategy validation remain research runtime responsibilities.

## OKX history constraints

OKX documents a three-month retention window for public historical funding rates, with `after` paging toward older `fundingTime` values and separate `realizedRate` fields. A longer candle history does not establish funding coverage. Packages preserve available partial history and block readiness until an attributed complete funding version is selected. An import remains `transport="user_import"` with provider provenance; it is not relabeled as an OKX API download. See [OKX historical funding documentation](https://app.okx.com/docs-v5/zh/#public-data-rest-api-get-funding-rate-history).

Funding frequency can change by instrument, so the package binds actual settlement timestamps and never assumes an eight-hour schedule. See the [OKX API change log](https://www.okx.com/docs-v5/log_en/#2023-12-20). Candle time alignment follows the requested UTC intervals, including explicit `1Dutc`; see [OKX historical candles](https://app.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history).

No exchange dataset is bundled or redistributed by this feature. The repository includes deterministic synthetic test fixtures. Fetching or importing data does not grant redistribution rights; the source terms described in [OKX integration](okx-integration.md) and [data operations](data-operations.md) still apply.

## Failure acceptance

The tests exercise a real persistent Store and CatalogService with mocked provider transport: concurrent idempotent creation, transaction rollback during child allocation, both capacity boundaries, exact-version binding, retention exhaustion, candle gaps, instrument-rule drift, failed-page cursor recovery, late download/mark responses after cancellation, competing preparation claims, shutdown with partially captured funding, bounded scheduler batches, token-preserving retry/recovery, manifest corruption and published-record immutability. They make no exchange requests and require no credentials.
