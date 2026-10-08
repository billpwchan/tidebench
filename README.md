# Tidebench

**Evidence before execution.**

An open-source crypto research and execution workbench for OKX spot and linear USDT perpetuals. Version your market data, test a hypothesis out of sample, and follow every simulated order through the account ledger.

[![CI](https://github.com/billpwchan/tidebench/actions/workflows/ci.yml/badge.svg)](https://github.com/billpwchan/tidebench/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![React 19](https://img.shields.io/badge/react-19-149ECA?logo=react&logoColor=white)](frontend/package.json)

English · [简体中文](README.zh-CN.md) · [Quick start](#quick-start) · [Research model](docs/pro-research.md) · [Operations](docs/operations.md)

![Tidebench trading overview, using explicitly synthetic prices](docs/assets/overview.png)

## A connected trading workflow

Go from an economic hypothesis to an immutable strategy version, reproducible research, reviewed paper release and an inspectable forward decision journal. Shared-capital historical portfolios, one-use holdouts and explicit costs make the evidence reviewable. See the [workflow guide](docs/strategy-workflows.md) and [current self-audit](docs/audit/README.md).

| Workspace | What you can do |
|---|---|
| Overview | Monitor actual positions, pending orders, asset exposure, risk attention and active research from one desk |
| Markets | Inspect confirmed candles and timestamped OKX quotes; switch explicitly to an isolated synthetic source |
| Data library | Prepare a complete research package in one action: trade, mark, settled funding and exact-time settlement marks; inspect blockers, immutable versions and hashes; import attributed history |
| Strategies | Immutable hypotheses and definitions; five reference families, bounded declarative programs, exits, loss budgets and editable research recipes |
| Research | Version-bound replay, grids, cost stress, train/test, walk-forward; one-use pre-registered holdouts and trial ledger |
| Portfolio research | One cash budget across 2–10 aligned markets; fixed weights, independent signals, momentum and funding carry; independent chronological test windows |
| Portfolio | Spot inventory and isolated perpetual positions in one account; order previews, market/limit/stop simulation, reservations, cancellation, leverage, margin and funding |
| Risk | Asset and market gross/net exposure, concentration, isolated margin buffers and captured custom price shocks; transactional limits and a persistent halt |
| Operations | Named users and roles, sessions, audit, feed age, progress health, disposable research processes, persistent incidents, verified backups and recovery |

The research engine makes time, costs and accounting visible. Signals use confirmed closes; historical fills occur at the next open. Walk-forward candidates are selected on training data only. Test folds start with independent accounts; the UI does not invent a continuous equity curve from overlapping experiments.

Perpetual research uses separate mark prices, actual settled funding rates, contract multipliers and captured maintenance tiers. It exposes historical bar approximations and the use of current tiers as a scenario assumption. A liquidation gap becomes an explicit liability and pauses new simulated risk; it does not disappear from P&L.

**Execution is local simulation.** Tidebench reads real public market data but sends no exchange orders. No exchange API key is needed or accepted. This is the execution scope of this product, including its Docker deployment.

## Quick start

Requires Python 3.12–3.14, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node.js 22.12+ and Make.

```bash
git clone https://github.com/billpwchan/tidebench.git
cd tidebench
make setup
make dev
```

Open [localhost:5173](http://localhost:5173) and create your administrator. Passwords have a 12-character minimum; sessions use HTTP-only cookies. No default account is shipped.

For an offline walkthrough, choose **Example**:

1. Open **Strategies**, load a reference hypothesis or define your own, and save a version. Choose **Research this version**.
2. Open **Data library → Research packages**, choose a spot or perpetual market and UTC range, then **Prepare research package**. A perpetual package gathers trade, mark, realized funding and settlement marks before becoming ready.
3. Prepare and select the exact data version in the bound study. Evaluate costs and independent test windows; export or replay the captured evidence. **Research governance** freezes a one-use final test before evaluation.
4. **Review paper release** from the result, inspect the selected configuration and current policy, approve and activate. In **Execution**, advance the synthetic clock and inspect actual decisions, orders, exits and observed account performance. Order previews and captured exposure scenarios remain available.
5. Open **Operations**. Create and verify a backup. A restore replaces workspace state, revokes sessions, cancels pending orders and halts strategies.

Example time can be paused, accelerated or stepped forward, with separate capital. OKX failures remain visible; the application never substitutes synthetic prices automatically.

### Built application

```bash
make build
uv run python -m tidebench
```

Open [localhost:8000](http://localhost:8000). One Python process serves the application, API and bounded background workers. Local state lives in `./data`.

### Docker

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the generated value in `TIDEBENCH_BOOTSTRAP_TOKEN` in your private `.env`, then:

```bash
docker compose up --build
```

Open [localhost:8080](http://localhost:8080). Enter that bootstrap token when creating the first administrator. It is needed because the container sees the browser connection through the Docker network. Subsequent logins use your username and password.

For HTTPS hosting, set exact allowed hosts/origins, enable secure cookies and use the [deployment runbook](docs/operations.md). Keep one process and one writer per database. The supplied Compose binding is private to the host.

## Inspect the implementation

```mermaid
flowchart LR
    UI[React / TypeScript workbench] --> API[FastAPI + sessions / roles / CSRF]
    API --> Catalog[Immutable datasets + durable downloads]
    Catalog --> OKX[Regional OKX public endpoints]
    Catalog --> Research[Deterministic research plans]
    API --> Book[Unified spot / perpetual simulation]
    Book --> Risk[Transactional risk + native asset journal]
    Research --> DB[(SQLite WAL / single writer)]
    Risk --> DB
    DB --> Recovery[Verified backups + safe recovery]
```

| Contract | Read more |
|---|---|
| One-action research packages, funding marks and immutable handoff | [Data packages](docs/data-packages.md) |
| Data identity, pagination, retention and imports | [Data operations](docs/data-operations.md) |
| Exposure, isolated margin and captured stress scenarios | [Portfolio risk](docs/portfolio-risk.md) |
| Strategy recipes, review, holdouts, portfolio models and forward evidence | [Strategy workflows](docs/strategy-workflows.md) |
| Signals, costs, funding, liquidation and OOS selection | [Professional research](docs/pro-research.md) |
| Process boundaries, persistence and precision | [Architecture](docs/architecture.md) |
| Authentication, roles, recovery and deployment | [Operations](docs/operations.md) |
| Actual acceptance evidence | [Verification](docs/verification.md) |
| Public APIs and data rights | [OKX integration](docs/okx-integration.md) |

<details>
<summary>Research and execution workspaces</summary>

![Observed paper-account performance on synthetic data; not investment returns](docs/assets/strategy-performance.png)

![Shared-capital portfolio study on synthetic data](docs/assets/portfolio-research.png)

![Research on a synthetic dataset; illustrative, not investment performance](docs/assets/research.png)

![Versioned research packages with synthetic inputs](docs/assets/data-packages.png)

![Actual local simulated spot and perpetual positions](docs/assets/workspace.png)

![Captured custom price shocks on the synthetic book](docs/assets/portfolio-risk.png)

</details>

## Verification

```bash
make check
cd frontend && npx playwright install chromium && cd ..
make browser-test
```

Tests exercise causal replay, indicator state, cost sensitivity, train-only selection, margin tiers, funding deduplication, native asset accounting, concurrent idempotency, stop races, role/CSRF boundaries and actual backup recovery. Browser acceptance covers the rendered desktop and mobile workflows. The [verification record](docs/verification.md) identifies what was measured and the exact scope of that evidence.

## Model boundaries

- REST polling is suitable for bar research and forward paper workflows; it does not provide tick-level execution or a latency guarantee.
- Market, limit and stop orders use a local full-fill model. Historical bars cannot reconstruct queue position, partial fills, market impact or exact intrabar paths.
- Public settled funding history has limited retention. Older research requires attributed imports with an explicit coverage declaration; missing history blocks derivative research.
- Historical funding marks can be one-minute bar-open approximations. Captured current maintenance tiers are scenario inputs, not historical tier evidence.
- Managed forward deployment is single-market. Historical portfolio construction does not provide managed multi-leg forward execution or strategy-level contribution attribution. Current-universe selection does not establish survivorship-free history.
- One shared workspace with role-based users, one process and one SQLite writer. This deployment does not provide distributed failover or tenant isolation.
- A short test run cannot prove months of availability or strategy profitability. Metrics state insufficient-sample and insolvent-account conditions explicitly.

See [scope and acceptance](docs/commercial-readiness.md) for implemented controls and evidence boundaries. Exchange execution is a separate product boundary requiring explicit authorization and a private reconciliation adapter.

## Contribute

Reproducible issues and focused changes to research correctness, accounting, data quality and trader workflows are particularly useful. Read [CONTRIBUTING.md](CONTRIBUTING.md), open an [issue](https://github.com/billpwchan/tidebench/issues), or submit a pull request. Report security findings through [SECURITY.md](SECURITY.md).

## License and data rights

Original application code uses the [MIT License](LICENSE). It grants no rights to exchange data, services or trademarks. No real OKX dataset is bundled. Hosted redistribution and commercial data services must satisfy the applicable provider terms; see [data rights](docs/okx-integration.md#data-rights-and-commercial-scope).
