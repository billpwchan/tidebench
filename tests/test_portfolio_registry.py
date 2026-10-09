"""Portfolio definition identity, exact research binding and shared capital."""

import copy
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D

import pytest
from tidebench.platform import PlatformError
from tidebench.portfolio_construction import construction_weights
from tidebench.portfolio_registry import PortfolioDefinition, PortfolioRegistry
from tidebench.portfolio_research import PortfolioInput
from tidebench.store import Store, encode


def definition(**changes):
    return PortfolioDefinition.model_validate(
        {"legs": [{"inst_id": s, "weight": ".5"} for s in ("BTC-USDT", "ETH-USDT")], **changes}
    ).record()


@pytest.fixture
def registry(tmp_path):
    return PortfolioRegistry(Store(tmp_path / "state.db"), {"code_fingerprint": "current"})


def project(registry, **changes):
    return registry.create_project(
        "Joint portfolio",
        "A declared capital budget controls correlated assets together.",
        definition(**changes),
        "researcher",
    )


def test_versions_dedupe_serialize_and_verify_content(registry):
    p = project(registry)
    v = p["version"]

    def create(bars):
        return registry.create_version(
            p["id"], v["hypothesis"], definition(rebalance_bars=bars), "researcher", v["id"]
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(create, [1, 2, 3, 4]))
    assert {r["revision"] for r in rows} == {2, 3, 4, 5}
    assert create(1)["id"] == rows[0]["id"]
    with registry.store.write() as conn:
        conn.execute(
            "UPDATE portfolio_versions SET hypothesis=? WHERE id=?", ("Changed without new version", v["id"])
        )
    with pytest.raises(PlatformError, match="integrity"):
        registry.version(v["id"])


def test_parent_and_trimmed_metadata_guard(registry):
    first, second = project(registry), project(registry)
    with pytest.raises(PlatformError, match="Parent"):
        registry.create_version(
            first["id"],
            "A different economic hypothesis matters.",
            definition(),
            "researcher",
            second["version"]["id"],
        )
    with pytest.raises(PlatformError, match="name"):
        registry.create_project("  ", "An economic hypothesis is required.", definition(), "researcher")


def test_exact_binding_uses_numeric_identity_and_complete_ordered_universe(registry):
    version = project(registry, capital_pct="75.00")["version"]
    packages = [{"inst_id": s, "bar": "1H"} for s in ("BTC-USDT", "ETH-USDT")]
    body = encode(
        PortfolioInput(
            name="Joint research",
            hypothesis=version["hypothesis"],
            portfolio_version_id=version["id"],
            capital_pct="75.0",
            carry_threshold="0.00",
            legs=[{"package_id": str(i) * 32, "weight": "0.50"} for i in (1, 2)],
        ).model_dump()
    )
    assert registry.validate_binding(body, packages)["id"] == version["id"]
    for change in (
        {"capital_pct": "74"},
        {"max_residual_pct": "1"},
        {"failure_policy": "ignore"},
        {"execution_contract": "other"},
        {"rebalance_bars": 23},
        {"top_k": 2},
        {"risk_window": 80},
        {"vol_target_pct": "15"},
        {"vol_floor_pct": "15"},
        {"covariance_shrinkage": ".4"},
        {"correlation_stress": ".9"},
        {"carry_window": 12},
        {"carry_cost_settlements": 21},
        {"carry_buffer_bps": "20"},
        {"carry_max_age_hours": 16},
    ):
        with pytest.raises(PlatformError, match="match"):
            registry.validate_binding(body | change, packages)
    wrong = copy.deepcopy(body)
    wrong["legs"][0]["strategy"]["fast"] = 13
    with pytest.raises(PlatformError, match="match"):
        registry.validate_binding(wrong, packages)
    with pytest.raises(PlatformError, match="match"):
        registry.validate_binding(body, list(reversed(packages)))
    registry.implementation = {"code_fingerprint": "upgrade"}
    with pytest.raises(PlatformError, match="installed"):
        registry.validate_binding(body, packages)


def test_construction_signal_retention_momentum_ties_and_missing_funding():
    legs = definition()["legs"]
    symbols = [r["inst_id"] for r in legs]
    weights = construction_weights(
        {"mode": "independent_signals"}, legs, dict.fromkeys(symbols, None), {symbols[0]: -2}, {}, None
    )
    assert weights == {symbols[0]: D("-.5"), symbols[1]: 0}
    weights = construction_weights(
        {"mode": "momentum", "top_k": 1}, legs, {}, {}, dict.fromkeys(symbols, ".1"), None
    )
    assert weights == {symbols[0]: 0, symbols[1]: D(".5")}
    assert not any(
        construction_weights({"mode": "funding_carry", "carry_threshold": 0}, legs, {}, {}, {}, None).values()
    )


def test_stored_legacy_version_reads_without_rewrite_and_requires_explicit_revision(registry):
    from tidebench.store import dumps
    from tidebench.strategy_registry import digest

    p = project(registry)
    version = p["version"]
    legacy = {k: v for k, v in version["definition"].items() if k != "execution_contract"}
    identity = digest(
        dict(definition=legacy, hypothesis=version["hypothesis"], implementation=version["implementation"])
    )
    with registry.store.write() as conn:
        conn.execute(
            "UPDATE portfolio_versions SET definition=?,content_hash=? WHERE id=?",
            (dumps(legacy), identity, version["id"]),
        )
    read = registry.version(version["id"])
    assert read["definition"] == legacy and read["content_hash"] == identity
    config = encode(
        PortfolioInput(
            name="Legacy binding",
            hypothesis=version["hypothesis"],
            portfolio_version_id=version["id"],
            legs=[{"package_id": str(i) * 32, "weight": ".5"} for i in (1, 2)],
        ).model_dump()
    )
    with pytest.raises(PlatformError) as error:
        registry.validate_binding(config, [{"inst_id": s, "bar": "1H"} for s in ("BTC-USDT", "ETH-USDT")])
    assert error.value.code == "portfolio_execution_legacy"
    revised = registry.create_version(p["id"], version["hypothesis"], legacy, "researcher", version["id"])
    assert revised["definition"]["execution_contract"] == "reduce_group_v2_allowance"
    assert revised["content_hash"] != identity
    assert registry.version(version["id"])["content_hash"] == identity
