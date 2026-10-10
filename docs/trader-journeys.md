# Resume the same trading work

The workspace connects tasks by their saved identity and source. Its starting view is the account and current book. Equity, cash, gross exposure and current conditions appear before research continuation; the adjacent next actions identify active preparation/research, approvals needing activation, results needing review and running paper controllers. Failed historical research remains a separate expandable history. An incomplete task snapshot displays a retry action rather than claiming there is nothing to do.

The monetary overview uses the account captured inside its exposure report, including that capture's timestamp and unknown economics. Returning from execution requests a fresh capture. If a matching report is unavailable, the source-matched account remains inspectable while exposure stays unavailable; cached cash and a different report are not presented as one account.


## Monitor the book, then open an order ticket

**Execution → Account & orders** opens positions and working orders first. **New order** opens the ticket. Market search's **Trade** action opens the same ticket for that exact source/market. Closing it or pressing Escape returns to monitoring, retains the unfinished values and returns focus to its trigger. A new ticket does not submit an order.

Search positions/orders by market and identity fields. Sort numeric columns by their numeric values, including small prices; unknown values stay last. Dense desktop tables keep market identity and actions fixed when horizontally scrolled. Mobile records show the core book quantities and actions, with secondary fields in **More details**. Large mobile books use a bounded scroll area; pagination returns to its first record rather than leaving the user at the previous page’s bottom. Pending orders are separate from **All orders & outcomes**.

The optional **Size by USDT notional** uses the current ask for a buy and bid for a sell. It floors native quantity to the lot, respects minimum size, and displays the unallocated remainder. USDT perpetual sizing requires verified linear base-valued contract rules. It refuses stale or mismatched OKX quotes. The tool currently applies to market orders; limit/stop tickets require explicit native quantity because future fill prices and margin reservations differ. Fees and slippage are excluded from the estimate. **Use calculated quantity** only edits the ticket; **Preview order** performs the server's account/risk check before the separate simulated submission.

Use **Analysis & controls** to reach account performance, exposure/scenarios, contributions, managed groups, independent strategies, releases and ledger. **Account details** contains margin and accounting details; **Synthetic market clock** is available only for the Example account. The top level **Risk & limits** remains the explicit risk-control task.

## Define a hypothesis, then prepare its inputs

1. Open **Strategies**, choose a research starting point, edit its mechanism and rejection hypothesis, and **Save version**. The result is an immutable record; a browser draft is only editable working text.
2. Choose **Research this version**. Set costs and the study method. **Open Data library** beside the dataset control preserves this research intent and its edited draft, and carries the selected product, market, interval and UTC window into preparation. Invalid or missing date edges do not erase the valid edge.
3. Choose the market, interval and aligned UTC window, then **Prepare research package**. Perpetual packages need trade, mark, funding and settlement marks. A package is usable only when its required evidence is ready.
4. **Open in research** from that package. The previous strategy version, costs and study settings remain, while the exact package inputs and UTC window are bound. Refresh preserves the package/version link and restores the local draft.
5. Without a selected result, the main area contains the configuration and run action, with history below. On wide screens, market/data, strategy/capital and evaluation share one parallel configuration area. Selecting a run brings its result and progress into the main area; single-strategy selection also focuses the result heading. Run the study. Its result is a distinct selected task, not a replacement for the editing form. **Use selected run configuration** explicitly copies an older result into the form; viewing history alone does not overwrite current work.

A portfolio draft follows the same round trip. **Prepare data for this leg** remembers the target leg; returning with its package fills that leg, without discarding the hypothesis, other packages, weights or controls. Packages must have compatible market and time identity. A mismatched package produces an actionable error.

An explicit portfolio-result link takes display priority over a recovered editor. **Resume portfolio draft** returns to the unfinished fields and removes the result selection from the URL; **Revise & research** deliberately copies the selected run into an editable revision. A version request returning after you leave or start another draft cannot change the new task. Delayed project loading, study creation and replay follow the same rule, including editing fields while waiting. A previously submitted study remains saved and appears in history; **View submitted research** explicitly opens it without changing the unfinished draft.

Strategy-authoring and research drafts live in **sessionStorage**, separated by authenticated user, origin, source and form. Invalid rule JSON stays editable, including its original malformed text, while submission is blocked. Damaged or unavailable browser storage is visible. These drafts are not synced between browsers and do not grant approval or trading authority. Saved versions and research evidence live on the server. Explicit clearing affects the current draft, not historical evidence.

## Interrupt research without losing its responsibility

**Cancel research** targets only the displayed saved run. A queued study can be cancelled immediately. A running study first records **Cancellation requested**; it stays active until calculation and owned worker cleanup have finished. Confirmed cancellation means that run will not publish a result. It does not halt paper execution or cancel other studies.

If the service restarts before cleanup is observed, cancellation remains requested until the previous worker’s exit is verified. **Cancellation needs exit evidence** explains the blocked state; **Recheck cancellation** checks the same run again. The service never kills a PID whose identity it cannot establish.

The configuration, attempted candidates and final-evaluation consumption remain recorded. Restoring an older backup also retains later cancellation responsibility and consumed final evaluations, including runs absent from that backup; restoring cannot silently restart that study. Cancellation cannot make an already exposed final window unseen again. Start a new study from the preserved configuration; follow the existing governance protocol if a new final window is required. Cancellation racing with a completed result preserves whichever terminal transaction committed first.

## Approve, activate and inspect the exact controller

From a result, review the selected configuration, costs, economic evidence and actual account capacity. Saving an approval does not start trading. Activation revalidates the current account in the write transaction.

**Inspect deployment** opens the deployment returned by activation. A portfolio approval opens its exact managed group. The URL includes the task/source; reload and browser history retain that selection. A missing identity is an explicit error, not an automatic jump to another controller. A delayed activation receipt preserves its server fact but cannot pull a user back from another source, page or selected task. Closing or restarting a review also invalidates the old window’s display callbacks.

Approval history separates **Activation recorded** from **Current controller state**. A stopped controller leaves its original approved activation record intact. Failure to load current state displays unknown instead of inferring that it is still running.

## Stop controllers, then decide what to do with inventory

A managed member's action is **Stop whole portfolio**. Before submission, the dialog identifies the source, group and markets and explains that every member stops while filled inventory remains. Cancelling the dialog sends no stop request. Confirmation waits for the server's matching group/source/stopped receipt.

Stopping an independent strategy also retains positions. Use the existing position-protection workflow for an actual reduce-only exit, and inspect its separate receipt. Stopping a controller and flattening an account are different economic actions.

## Find a market and return to work

**Command/Ctrl K** searches the current source's eligible USDT spot and linear perpetual catalog. **Inspect** opens and expands the selected market; **Trade** locks that market into the paper ticket without submitting an order. Filter Spot or USDT perpetuals, move between matches with Arrow Down/Up, press Enter to inspect, and Tab to the Trade action. Loading, failed catalog and no matching instrument are distinct states.

Links have the form `#research?source=example&view=advanced&run=RUN_ID` or `#execution?source=okx&view=managed:GROUP_ID`. They carry identifiers and selection, never authorization. Selecting an already-active tab keeps the selected task. Changing source clears incompatible task selections. Operations is explicitly global; its liquidity observations use public OKX data.

Keyboard navigation restores dialog focus to its trigger and focuses the content region after page changes. Desktop and narrow-screen acceptance covers back/forward/reload, explicit missing tasks, drafts, stop cancellation and late responses.

## Actual journey views

[Desktop current book](assets/desk-overview-v0.13-desktop.png) · [Mobile current book](assets/desk-overview-v0.13-mobile.png) · [Parallel research desk](assets/desk-research-v0.13-desktop.png). These v0.13 views use synthetic acceptance fixtures and do not represent investment returns.

Earlier connected-context views are retained:


[Desktop research/data return](assets/journey-data-return-v0.12-desktop.png) · [Mobile research/data return](assets/journey-data-return-v0.12-mobile.png) · [Desktop task overview](assets/journey-overview-v0.12-desktop.png) · [Mobile task overview](assets/journey-overview-v0.12-mobile.png). Captured from the tested application with explicitly labelled synthetic inputs.
