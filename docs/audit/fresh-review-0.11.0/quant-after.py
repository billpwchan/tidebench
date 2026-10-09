import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.market import MarketService
from tidebench.pro_api import ResearchInput
from tidebench.store import encode
from tidebench.platform import PlatformError
from tidebench.provenance import research_identity

HOUR = 3600000
END = 1767225600000


async def fresh(root):
    def blocked(request):
        raise AssertionError("External requests forbidden")
    client = httpx.AsyncClient(transport=httpx.MockTransport(blocked))
    settings = Settings(data_dir=root, worker_enabled=False, _env_file=None)
    r = create_app(settings, MarketService(client=client)).state.professional
    return r, client


async def inputs(r, mode):
    p = r.registry.create_project(
        "Independent quant audit", "Test only causal selection and retained trial accounting.",
        {"strategy": {"kind": "sma_cross", "fast": 5, "slow": 20, "allocation": ".1"}},
        "researcher")
    job = r.catalog.create_job("BTC-USDT", "trade", "1H", END-400*HOUR, END, "example")
    job = await r.catalog.run_job(job["id"])
    options = {"grid": {"fast": [5, 8], "slow": [20]}}
    if mode == "walk_forward":
        options.update(train_bars=160, test_bars=80, step_bars=80)
    config = encode(ResearchInput(dataset_id=job["dataset_id"],
        strategy=p["version"]["definition"]["strategy"], strategy_version_id=p["version"]["id"],
        liquidation_fee_bps=50, mode=mode, options=options).model_dump())
    return p, config


async def main():
    out = {"implementation": research_identity(), "scope": "Isolated SQLite and blocked external requests; synthetic causal selection and actual backup/restore; no venue orders."}
    with TemporaryDirectory(prefix="tidebench-v11-quant-ledger-") as directory:
        r, client = await fresh(Path(directory))
        try:
            p, config = await inputs(r, "grid")
            backup = r.backups.create()
            run = r.create_run(config)
            before = r.governance.trials(p["id"])
            restored = r.backups.restore(backup["id"])
            after = r.governance.trials(p["id"])
            with r.store.read() as conn:
                facts = [dict(row) for row in conn.execute("SELECT kind,run_id,candidate_count,project_id FROM research_trials WHERE project_id=?",(p["id"],))]
            out["retained_single_trials"] = dict(before_counts={k:before[k] for k in ("run_count","candidate_configurations")},
                after_counts={k:after[k] for k in ("run_count","candidate_configurations")},
                retained=restored["retained_research_facts"], actual_facts=facts)
            assert before["candidate_configurations"] == 2
            assert after["run_count"] == 1 and after["candidate_configurations"] == 2 and len(facts)==1 and facts[0]["candidate_count"]==2
        finally:
            await r.stop()
            await client.aclose()
    with TemporaryDirectory(prefix="tidebench-v11-quant-oos-") as directory:
        r, client = await fresh(Path(directory))
        try:
            p, config = await inputs(r, "walk_forward")
            run = r.create_run(config)
            await r.perform_run(run["id"])
            run = r.run(run["id"])
            assert run["status"] == "completed", run["error"]
            folds=run["result"]["folds"]
            winner=max(folds,key=lambda f:f["test_result"]["metrics"]["total_return_pct"])
            preview=r.registry.preview_release(r,run["id"],winner["id"])
            command = {"run_id":run["id"],"selection":winner["id"],
                "preview_hash":preview["preview_hash"],"acknowledgements":[],
                "review":"Intentionally selected the highest test score after examining every OOS fold."}
            try:
                r.registry.approve_release(r, command, "trader")
            except PlatformError as exc:
                rejection = {"code": exc.code, "message": exc.message}
            else:
                raise AssertionError("Post-test selection must require explicit acknowledgement")
            release=r.registry.approve_release(r, command | {"acknowledgements": preview["required_acknowledgements"]}, "trader")
            out["unacknowledged_selection_refused"] = rejection
            out["selection_evidence"] = preview["selection_evidence"]
            out["post_test_fold_selection"] = dict(
                fold_returns=[dict(id=f["id"],test_return=f["test_result"]["metrics"]["total_return_pct"],fast=f["selected_strategy"]["fast"]) for f in folds],
                winner=winner["id"],selection_scope=preview["selection_scope"],
                required_acknowledgements=preview["required_acknowledgements"],approval_status=release["status"],
                release_definition=release["config"]["strategy"])
            assert "post_test_selection" in preview["required_acknowledgements"]
            assert preview["selection_evidence"]["independent_final_validation"] is False
        finally:
            await r.stop()
            await client.aclose()
    path=Path("/tmp/tidebench-v11-quant-after-observations.json")
    path.write_text(json.dumps(out,indent=2))
    print(json.dumps(out,indent=2))


if __name__ == "__main__":
    asyncio.run(main())
