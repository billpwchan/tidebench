# Release positioning and demonstration

## Repository description

Self-hosted crypto research and paper execution for OKX spot and USDT perpetuals. Versioned data, walk-forward evaluation, funding, risk and auditable accounting.

The useful distinction is inspectable evidence across the workflow: a result identifies its data and model, an OOS choice identifies its training window, and an account change identifies its order, cost and journal entries. Real screenshots, reproducible installation and tested recovery make those claims reviewable.

## Suggested announcement

Tidebench is an open-source crypto research and execution workbench for OKX spot and USDT perpetuals.

It connects immutable market datasets, out-of-sample research and a persistent simulation account. You can run single replays, parameter grids, cost stress, train/test or walk-forward plans, inspect settled funding and isolated-margin assumptions, and replay the captured evidence.

The portfolio supports previewed market/limit/stop simulation, reservations, cancellation, transactional risk and a native asset journal. Named users, server sessions, audit, metrics and verified backup recovery ship in the same application.

Public data needs no exchange key. Orders stay local; historical bar and funding-price approximations are explicit. The repository includes acceptance tests, a real HTTP workload script and operating procedures. Reviews of research causality, accounting and actual trader workflows are particularly welcome.

[Repository](https://github.com/billpwchan/tidebench) · [Verification](verification.md) · [Model](pro-research.md)

This text is a draft for the maintainer. No community post or external message is sent automatically.

## Demonstration sequence

1. Select the explicitly labeled Example source, then download a perpetual's trade, mark and funding datasets in Data library.
2. Inspect dataset versions, quality, units and provenance; open them in Research.
3. Run a walk-forward plan, inspect training selection and independent test folds, then compare a cost-stress plan.
4. Export actual JSON and replay a captured run; show that its deterministic result agrees.
5. Preview and submit a local perpetual order; inspect margin, funding, orders and journal. Queue and cancel a limit order.
6. Halt new risk and reduce an existing position. Show verified backup recovery in a disposable workspace; sign in again and confirm execution remains halted.

Screenshots and any published example returns must remain labeled synthetic. Do not promise returns, manufacture adoption numbers, buy stars or post unsolicited repeated advertisements. Discoverability should come from useful code, clear scope, searchable topics, concrete release notes and maintained contribution paths.
