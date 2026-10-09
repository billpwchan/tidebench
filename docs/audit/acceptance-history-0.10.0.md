# v0.10 acceptance history

These independent attempts retain their original bytes and implementation identities. Passing a later attempt does not turn an earlier failure into a pass. Isolated synthetic studies and public observations use separate workspaces; the operator database and exchange credentials are excluded.

| Attempt | Implementation | Actual observation | Interpretation |
|---|---|---|---|
| [Initial public paper](v0.10.0-public-feed-paper-initial.json) | `205f89b2d7ed671ff52de64921cb58d4ccb04845` | 362.736 seconds, seven confirmed decisions, four strategy orders, then flat; −2.54639786262 USDT | Bounded integration passed; 14-day acceptance failed. |
| [Funding wait elapsed failure](v0.10.0-soak-funding-wait-failure.json) | `205f89b2d7ed671ff52de64921cb58d4ccb04845` | 602.977 seconds; one group failed on another group's unpublished funding | Correct risk waiting was incorrectly classified as terminal execution failure. The separate [funding-wait correction](fresh-institutional-review-0.10.0-funding-wait-addendum.zh-CN.md) preserves original pending orders across publication and restart. |
| [Capital allowance rejection](v0.10.0-soak-capital-allowance-rejection.json) | `4b0d466e82b486256ba71006605bdd223592ec36` | 603.306 seconds; ETH/SOL new-risk child rejected after shared equity fell | The capital guard was correct. Original v1 frozen orders compensate on rejection; bounded transparent replanning requires a new execution contract. This failure is retained. |
| [Public quote rejection](v0.10.0-public-feed-paper-quote-rejection.json) | `4b0d466e82b486256ba71006605bdd223592ec36` | 275.826 seconds; no quote observed after the new signal boundary | Product safely rejected the precondition. Collector originally ended instead of using the controller's normal bounded retry; incomplete attempt remains a failure. |
| [Funding-fixed public paper](v0.10.0-public-feed-paper-funding-fixed.json) | `4b0d466e82b486256ba71006605bdd223592ec36` | 362.406 seconds, seven confirmed decisions, five strategy orders, then flat; −2.345892713607 USDT | Original fills preserved through an actual controller evaluation; exact contribution and frozen-window verification passed. A market-clock regression correctly makes the linked return unavailable. 14-day acceptance failed. |

The initial [28-case report](v0.10.0-portfolio-risk-battery-initial.json), [replay](v0.10.0-portfolio-risk-replay-initial.json) and [manifest](v0.10.0-public-evidence-manifest-initial.json) retain the `205f89b` numerical identity. Their original manifest filenames predate the archival suffix; file bytes and recorded hashes remain unchanged. They are superseded only by a separately computed, matching-source report, never relabeled.

The public L2 run preserves 72 captures and six unsupported reports. Exchange timestamps ahead of the receive clock remain adverse evidence; changing a timestamp or relaxing the clock gate to create a pass is prohibited by the evidence contract.

Interrupted collector-development attempts are private incomplete diagnostics, not elapsed acceptance results. Only completed reports with bound driver/implementation identities appear in this record. No raw book arrays, operational databases or credentials are published.

## Separately completed current-source observations

The [v2 elapsed run](v0.10.0-soak-observations.json) passed 611.289 real seconds on frozen `d941806`, with the original `.5/.5` scenario, 60 market steps, 154 simulated fills, 93 completed batches, 120 funding settlements, and one actual SIGTERM/restart preserving all 77 earlier receipts. No replacement was required under this particular scheduling; independent deterministic and accelerated actual-book cases separately exercised genuine guard rejection and smaller continuation.

The [current public paper path](v0.10.0-public-feed-paper-observations.json) passed its scoped integration after 363.141 seconds, seven confirmed decisions and five strategy fills, ending flat at −1.482058153590 USDT with exact contribution reconciliation. Two market-clock regressions make linked return unavailable; long-window acceptance remains false. Start/end implementation and driver hashes match. [All 28 current numerical cases](v0.10.0-portfolio-risk-battery.json) also have exact [captured replay](v0.10.0-portfolio-risk-replay.json). The [current publication manifest](v0.10.0-public-evidence-manifest.json) records identities and hashes.

The earlier `205f89b` offline/managed/workload artifacts remain byte-identical. Current runs use distinct `-v2` filenames and the new manifest. New reports do not relabel earlier evidence.
