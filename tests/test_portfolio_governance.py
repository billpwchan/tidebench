"""Counterexamples for captured inputs, one-use admission and recovery governance."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.pro_api import ResearchInput
from tidebench.research_protocol import ResearchFacts
from tidebench.store import dumps, encode, new_id, now_ms
from tidebench.strategy_registry import digest

HOUR = 3600000
END = 1767225600000


@pytest.fixture
async def setup(tmp_path):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected external request"))
    )
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    app = create_app(settings, MarketService(client=client))
    r = app.state.professional
    project = r.portfolio_registry.create_project(
        "Pre-registered basket",
        "Both allocations must survive shared cash, measured costs and independent testing.",
        {
            "capital_pct": "20",
            "rebalance_bars": 4,
            "legs": [
                {"inst_id": symbol, "weight": ".5", "strategy": {"kind": "buy_hold"}}
                for symbol in ("BTC-USDT", "ETH-USDT")
            ],
        },
        "researcher",
    )
    packages = []
    for symbol in ("BTC-USDT", "ETH-USDT"):
        package = r.packages.create_package(symbol, "1H", END - 100 * HOUR, END, "example")
        packages.append(await r.packages.run_package(package["id"]))
        assert packages[-1]["ready"]
    body = dict(
        name="Untouched final basket",
        portfolio_version_id=project["version"]["id"],
        package_ids=[p["id"] for p in packages],
        test_start=END - 40 * HOUR,
        test_end=END,
        warmup_bars=20,
        rejection_plan="Reject when the fixed return, drawdown or solvency criteria fail; no retuning on the final window.",
        criteria=dict(
            min_return_vs_cash_pct="-100",
            max_drawdown_pct="100",
            min_trades=1,
            min_observations=20,
            require_zero_debt=True,
        ),
    )
    yield r, project, packages, body
    await r.stop()
    await client.aclose()


def seal(r, body):
    preview = r.protocol.preview(body, "researcher")
    return r.protocol.seal({"preview_id": preview["id"], "preview_hash": preview["plan_hash"]}, "researcher")


def evaluate(r, holdout):
    return r.protocol.evaluate(holdout["id"], holdout["plan_hash"], "researcher")


async def test_frozen_inputs_execute_and_replay_without_catalog_access(setup, monkeypatch):
    r, project, _, body = setup
    holdout = seal(r, body)
    for method in ("load_candles", "verify_dataset", "get_margin_tiers"):
        monkeypatch.setattr(
            r.catalog, method, lambda *a, **k: pytest.fail("Frozen compute read mutable catalog")
        )
    monkeypatch.setattr(
        r.packages, "research_inputs", lambda *a: pytest.fail("Frozen compute re-read package")
    )
    run = evaluate(r, holdout)
    await r.offload(r.portfolios.compute, run["id"])
    run = r.portfolios.get(run["id"])
    assert run["status"] == "completed", run["error"]
    result = run["result"]
    assert len(result["equity"]) == 40
    assert all(point["ts"] >= body["test_start"] for point in result["equity"])
    assert all(order["quote_ts"] > body["test_start"] for order in result["orders"])
    assert result["evaluation"]["benchmark"]["total_return_pct"] == "0"
    assert r.protocol.verify_run(run)["one_use"]
    replay = r.portfolios.replay(run["id"], "researcher")
    await r.offload(r.portfolios.compute, replay["id"])
    replay = r.portfolios.get(replay["id"])
    assert replay["status"] == "completed", replay["error"]
    assert replay["manifest"]["replay_verified"]
    assert replay["manifest"]["result_hash"] == run["manifest"]["result_hash"]
    assert r.protocol.verify_run(replay)["is_replay"]
    evidence = r.protocol.trials(project["id"])
    assert (evidence["recorded_attempts"], evidence["primary_evaluations"], evidence["replay_attempts"]) == (
        2,
        1,
        1,
    )
    assert evidence["distinct_configurations"] == 1
    assert evaluate(r, holdout)["id"] == run["id"]


async def test_concurrent_evaluation_commits_one_primary(setup):
    r, _, _, body = setup
    holdout = seal(r, body)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: evaluate(r, holdout)["id"], range(4)))
    assert len(set(ids)) == 1
    with r.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM portfolio_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM research_consumptions").fetchone()[0] == 1


async def test_queue_rejection_does_not_consume_but_committed_compute_failure_does(setup, monkeypatch):
    r, _, _, body = setup
    holdout = seal(r, body)
    with r.store.write() as conn:
        for _ in range(10):
            conn.execute(
                "INSERT INTO pro_runs(id,source,status,config,created_at,updated_at) VALUES(?,'example','queued','{}',?,?)",
                (new_id(), now_ms(), now_ms()),
            )
    with pytest.raises(PlatformError) as error:
        evaluate(r, holdout)
    assert error.value.code == "research_queue_full"
    assert r.protocol.get(holdout["id"])["status"] == "sealed"
    with r.store.write() as conn:
        conn.execute("DELETE FROM pro_runs")
    run = evaluate(r, holdout)
    import tidebench.research_protocol as protocol

    monkeypatch.setattr(
        protocol,
        "evaluate_frozen_portfolio",
        lambda *a: (_ for _ in ()).throw(ValueError("injected computation failure")),
    )
    # Use the same-process branch to inject a genuine failure after committed admission.
    r.settings.research_process_isolation = False
    await r.offload(r.portfolios.compute, run["id"])
    assert r.portfolios.get(run["id"])["status"] == "failed"
    assert evaluate(r, holdout)["id"] == run["id"]
    assert r.protocol.get(holdout["id"])["status"] == "consumed"


async def test_alias_cross_bar_and_single_strategy_cannot_enter_sealed_portfolio(setup):
    r, _, packages, body = setup
    seal(r, body)
    job = r.catalog.create_job("BTC-USDT", "trade", "5m", END - 50 * HOUR, END, "example")
    job = await r.catalog.run_job(job["id"])
    with pytest.raises(PlatformError) as error:
        r.create_run(
            encode(
                ResearchInput(dataset_id=job["dataset_id"], start_ts=END - 20 * HOUR, end_ts=END).model_dump()
            )
        )
    assert error.value.code == "holdout_reserved"
    # An unbound portfolio and a newly named project use the same market-time guard.
    with pytest.raises(PlatformError) as error:
        r.portfolios.create(
            {
                "name": "Renamed exploratory run",
                "hypothesis": "Renaming must not erase exposure or reservations.",
                "legs": [{"package_id": p["id"], "weight": ".5"} for p in packages],
            },
            "researcher",
        )
    assert error.value.code == "holdout_reserved"


async def test_prior_single_warmup_exposure_prevents_new_final_seal(setup):
    r, _, packages, body = setup
    dataset = r.packages.research_inputs(packages[0]["id"])["dataset_id"]
    r.create_run(encode(ResearchInput(dataset_id=dataset, start_ts=END - 10 * HOUR, end_ts=END).model_dump()))
    # Selected evaluation starts later; its actual 2000-bar warmup already accessed this earlier interval.
    with pytest.raises(PlatformError) as error:
        r.protocol.preview(body | {"test_start": END - 70 * HOUR, "test_end": END - 50 * HOUR}, "researcher")
    assert error.value.code == "holdout_exposed"


async def test_restore_cannot_reset_consumption_or_trials(setup):
    r, project, _, body = setup
    holdout = seal(r, body)
    backup = r.backups.create()
    run = evaluate(r, holdout)
    await r.offload(r.portfolios.compute, run["id"])
    restored = r.backups.restore(backup["id"])
    assert restored["retained_research_facts"]["research_consumptions"] == 1
    assert restored["retained_research_facts"]["research_trials"] == 1
    assert r.protocol.get(holdout["id"])["status"] == "consumed_unavailable"
    with pytest.raises(PlatformError) as error:
        evaluate(r, holdout)
    assert error.value.code == "holdout_consumed_unavailable"
    evidence = r.protocol.trials(project["id"])
    assert evidence["recorded_attempts"] == 1
    assert evidence["items"][0]["status"] == "evidence_unavailable"
    assert r.book.risk("example")["halted"]


async def test_restore_before_new_exposure_retains_guard_without_missing_run_fk(setup):
    r, _, packages, body = setup
    backup = r.backups.create()
    dataset = r.packages.research_inputs(packages[0]["id"])["dataset_id"]
    run = r.create_run(encode(ResearchInput(dataset_id=dataset).model_dump()))
    r.backups.restore(backup["id"])
    with r.store.read() as conn:
        assert not conn.execute("SELECT 1 FROM pro_runs WHERE id=?", (run["id"],)).fetchone()
    with pytest.raises(PlatformError) as error:
        r.protocol.preview(body, "researcher")
    assert error.value.code == "holdout_exposed"


async def test_hash_valid_but_conflicting_restore_facts_fail_before_financial_replace(setup):
    r, _, _, body = setup
    holdout = seal(r, body)
    target = r.backups.create()
    with r.store.write() as conn:
        row = conn.execute("SELECT * FROM research_reservations LIMIT 1").fetchone()
        fact = ResearchFacts.checked(row)
        fact["created_at"] += 1
        conn.execute(
            "UPDATE research_reservations SET created_at=?,body=?,content_hash=? WHERE id=?",
            (fact["created_at"], dumps(fact), digest(fact), fact["id"]),
        )
        conn.execute("UPDATE pro_accounts SET cash='9999' WHERE source='example'")
    with pytest.raises(PlatformError) as error:
        r.backups.restore(target["id"])
    assert error.value.code == "governance_conflict"
    with r.store.read() as conn:
        assert conn.execute("SELECT cash FROM pro_accounts WHERE source='example'").fetchone()[0] == "9999"
    assert r.protocol.get(holdout["id"])["status"] == "sealed"


@pytest.mark.parametrize("mutation", ["empty", "bad_index", "bad_digest", "extra_key"])
async def test_corrupt_facts_reject_backup_and_preserve_original(setup, mutation):
    r, _, _, body = setup
    seal(r, body)
    with r.store.write() as conn:
        row = conn.execute("SELECT * FROM research_reservations LIMIT 1").fetchone()
        fact = json.loads(row["body"])
        if mutation == "empty":
            fact = {}
        if mutation == "extra_key":
            fact["invented"] = "field"
        conn.execute(
            "UPDATE research_reservations SET body=?,content_hash=? WHERE id=?",
            (dumps(fact), digest(fact) if mutation != "bad_digest" else "0" * 64, row["id"]),
        )
        if mutation == "bad_index":
            conn.execute("UPDATE research_reservations SET start_ts=start_ts+1 WHERE id=?", (row["id"],))
    with pytest.raises(PlatformError) as error:
        r.backups.create()
    assert error.value.code == "governance_integrity"


async def test_criteria_inconclusive_and_rejected_are_release_evidence(setup):
    r, _, _, body = setup
    body["criteria"]["min_observations"] = 100
    holdout = seal(r, body)
    run = evaluate(r, holdout)
    await r.offload(r.portfolios.compute, run["id"])
    run = r.portfolios.get(run["id"])
    assert run["result"]["evaluation"]["rejection"]["status"] == "inconclusive"
    preview = r.portfolio_releases.preview(run["id"])
    assert "no_oos_evidence" not in preview["required_acknowledgements"]
    assert "holdout_inconclusive" in preview["required_acknowledgements"]
    forged = copy.deepcopy(run)
    forged["manifest"]["governance"]["primary_run_id"] = new_id()
    with pytest.raises(PlatformError) as error:
        r.protocol.verify_run(forged)
    assert error.value.code == "portfolio_governance_integrity"


async def test_perpetual_seal_freezes_tiers_realized_rates_and_exact_settlement_marks(setup, monkeypatch):
    r, _, packages, body = setup
    swap = r.packages.create_package("BTC-USDT-SWAP", "1H", END - 100 * HOUR, END, "example")
    swap = await r.packages.run_package(swap["id"])
    project = r.portfolio_registry.create_project(
        "Frozen basis carry",
        "Past realized funding selects matched spot-long and perpetual-short notional under captured tiers.",
        {
            "mode": "funding_carry",
            "capital_pct": "20",
            "carry_threshold": "-.01",
            "rebalance_bars": 4,
            "legs": [
                {"inst_id": "BTC-USDT", "weight": ".5"},
                {"inst_id": "BTC-USDT-SWAP", "weight": "-.5", "leverage": "2", "direction": "short_only"},
            ],
        },
        "researcher",
    )
    tiers = await r.catalog.get_margin_tiers("BTC-USDT-SWAP", "example")
    body = body | {
        "portfolio_version_id": project["version"]["id"],
        "package_ids": [packages[0]["id"], swap["id"]],
    }
    preview = r.protocol.preview(body, "researcher", {"BTC-USDT-SWAP": tiers})
    with r.store.read() as conn:
        private = r.protocol.get(preview["id"], conn, private=True)
    captured = r.protocol.bundle(private)
    derivatives = captured["legs"][1]
    assert derivatives["tiers"] and derivatives["funding"]
    assert all(str(e["mark_price"]) and e["mark_ts"] == e["ts"] for e in derivatives["funding"])
    original_tier_hash = digest(derivatives["tiers"])
    tiers["tiers"][0]["mmr"] = ".99"
    for method in ("get_margin_tiers", "get_dataset", "load_candles", "verify_dataset"):
        monkeypatch.setattr(
            r.catalog,
            method,
            lambda *a, **k: pytest.fail("Frozen derivative evaluation fetched mutable inputs"),
        )
    holdout = r.protocol.seal(
        {"preview_id": preview["id"], "preview_hash": preview["preview_hash"]}, "researcher"
    )
    run = evaluate(r, holdout)
    await r.offload(r.portfolios.compute, run["id"])
    run = r.portfolios.get(run["id"])
    assert run["status"] == "completed", run["error"]
    assert run["result"]["funding"]
    assert digest(r.protocol.bundle(private)["legs"][1]["tiers"]) == original_tier_hash
    replay = r.portfolios.replay(run["id"], "researcher")
    await r.offload(r.portfolios.compute, replay["id"])
    assert r.portfolios.get(replay["id"])["manifest"]["replay_verified"]
