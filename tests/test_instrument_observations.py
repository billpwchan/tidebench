import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
import tidebench.instrument_observations as evidence
from tidebench.catalog import CatalogService
from tidebench.market import MarketError, MarketService
from tidebench.store import Store, dumps

T = 1_767_225_600_000


def raw(symbol="BTC-USDT", **values):
    swap = symbol.endswith("-SWAP")
    return {
        "instId": symbol,
        "instType": "SWAP" if swap else "SPOT",
        "baseCcy": symbol.split("-")[0],
        "quoteCcy": "USDT",
        "state": "live",
        "listTime": "1609459200000",
        "expTime": "",
        "tickSz": "0.1",
        "lotSz": "0.01",
        "minSz": "0.01",
        "ctType": "linear" if swap else "",
        "settleCcy": "USDT" if swap else "",
        "ctVal": "0.01" if swap else "",
        "ctMult": "1" if swap else "",
        "ctValCcy": symbol.split("-")[0] if swap else "",
        **values,
    }


@pytest.fixture
async def catalog(tmp_path):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected network")))
    service = CatalogService(Store(tmp_path / "obs.sqlite3"), MarketService(client=client))
    yield service
    await client.aclose()


def capture(catalog, monkeypatch, rows, when=T, inst_type="SPOT"):
    monkeypatch.setattr(evidence, "time", SimpleNamespace(time_ns=lambda: when * 1_000_000 + 123))
    return catalog.observations.capture(rows, "okx", "global", inst_type, when)


async def test_blank_preopen_and_invalid_rows_are_retained_without_poisoning_live_market(catalog):
    rows = [
        raw(),
        raw("NEW-USDT", tickSz="", lotSz="", minSz="", state="preopen", listTime="1900000000000"),
        None,
        raw("BTC-USDC"),
    ]

    async def get(*_):
        return rows

    catalog.market._get = get
    observation = await catalog.observe_instruments()
    assert observation["rows"] == rows and observation["row_count"] == 4
    assert observation["members"][1]["eligibility"] == "unknown"
    assert observation["members"][1]["list_time"] == 1900000000000
    assert observation["members"][1]["metadata"] is None
    assert "missing_tickSz" in observation["members"][1]["reasons"]
    assert (await catalog.get_instrument("BTC-USDT"))["observation_id"] == observation["id"]
    with pytest.raises(MarketError, match="unavailable"):
        await catalog.get_instrument("NEW-USDT")


@pytest.mark.parametrize(
    "changes",
    [{"tickSz": "NaN"}, {"minSz": "0"}, {"baseCcy": "ETH"}, {"listTime": "tomorrow"}, {"instType": "SWAP"}],
)
def test_invalid_member_cannot_become_eligible_or_remove_healthy_member(catalog, monkeypatch, changes):
    item = capture(catalog, monkeypatch, [raw(), raw("NEW-USDT", **changes)])
    assert item["members"][0]["eligibility"] == "eligible"
    assert item["members"][1]["eligibility"] == "unknown"
    assert item["rows"][1] == raw("NEW-USDT", **changes)


def test_swap_preopen_unknown_sizing_and_unsupported_units_are_visible(catalog, monkeypatch):
    item = capture(
        catalog,
        monkeypatch,
        [
            raw("BTC-USDT-SWAP"),
            raw("NEW-USDT-SWAP", ctVal="", ctType="", settleCcy=""),
            raw("ETH-USDT-SWAP", ctMult="2"),
        ],
        inst_type="SWAP",
    )
    assert item["members"][0]["metadata"]["contract_size_base"] == evidence._decimal("0.01", "test")
    assert item["members"][1]["metadata"] is None
    assert item["members"][2]["scope"] == "unsupported"
    assert item["members"][2]["eligibility"] == "unknown"


def test_duplicate_instruments_are_quarantined_even_if_identical(catalog, monkeypatch):
    item = capture(catalog, monkeypatch, [raw(), raw(), raw("ETH-USDT")])
    assert all(
        m["metadata"] is None and m["reasons"] == ["duplicate_instrument"] for m in item["members"][:2]
    )
    assert item["members"][2]["eligibility"] == "eligible"


async def test_latest_full_observation_omission_never_falls_back_to_stale_rules(catalog):
    rows = [raw()]

    async def get(*_):
        return rows

    catalog.market._get = get
    first = await catalog.get_instrument("BTC-USDT")
    rows = []
    empty = await catalog.observe_instruments()
    assert empty["row_count"] == 0
    with pytest.raises(MarketError, match="not present"):
        await catalog.get_instrument("BTC-USDT")
    rows = [raw()]
    last = await catalog.observe_instruments()
    current = await catalog.get_instrument("BTC-USDT")
    assert current["observation_id"] == last["id"] != first["observation_id"]
    assert current["observed_at"] == last["received_at"]


def test_a_b_a_history_preserves_three_observations_and_returned_rule_identity(catalog, monkeypatch):
    first = capture(catalog, monkeypatch, [raw()], T)
    middle = capture(catalog, monkeypatch, [raw(state="suspend")], T + 1000)
    last = capture(catalog, monkeypatch, [raw()], T + 2000)
    listed = catalog.observations.list("okx", "global", "SPOT")
    assert [r["id"] for r in listed] == [last["id"], middle["id"], first["id"]]
    assert first["payload_hash"] == last["payload_hash"] != middle["payload_hash"]
    assert (
        first["members"][0]["metadata"]["instrument_version"]
        == last["members"][0]["metadata"]["instrument_version"]
    )
    assert catalog.observations.diff(middle["id"], first["id"])["changes"][0]["fields"] == ["state"]


def test_asof_never_uses_future_observation_and_age_is_explicit_assumption(catalog, monkeypatch):
    observation = capture(catalog, monkeypatch, [raw()], T)
    service = catalog.observations
    assert service.universe("okx", "global", "SPOT", T - 1, 3600000)["reason"] == "no_prior_observation"
    point = service.universe("okx", "global", "SPOT", T, 0)
    assert point["coverage"] == "point_observation" and not point["historical_completeness"]
    carry = service.universe("okx", "global", "SPOT", T + 1, 1)
    assert carry["coverage"] == "bounded_carry_forward_assumption"
    stale = service.universe("okx", "global", "SPOT", T + 2, 1)
    assert stale["coverage"] == "unknown" and stale["members"][0]["eligibility"] == "unknown"
    assert stale["observation"]["id"] == observation["id"]


def test_announced_listing_and_expiry_are_not_retrospective_known_times(catalog, monkeypatch):
    capture(catalog, monkeypatch, [raw(listTime=str(T + 1000)), raw("ETH-USDT", expTime=str(T + 1000))], T)
    early = catalog.observations.universe("okx", "global", "SPOT", T, 2000)
    assert early["members"][0]["eligibility"] == "ineligible"
    assert early["members"][1]["eligibility"] == "eligible"
    later = catalog.observations.universe("okx", "global", "SPOT", T + 1000, 2000)
    assert later["members"][1]["reasons"] == ["at_or_after_announced_expiry"]
    # No future live observation is invented when an announced listing time passes.
    assert later["members"][0]["eligibility"] == "ineligible"


def test_diff_omission_is_not_delisting_and_rejects_wrong_scope_or_order(catalog, monkeypatch):
    first = capture(catalog, monkeypatch, [raw(), raw("ETH-USDT")], T)
    last = capture(catalog, monkeypatch, [raw(tickSz="0.01"), raw("SOL-USDT")], T + 1000)
    diff = catalog.observations.diff(last["id"], first["id"])
    assert {c["inst_id"]: c["change"] for c in diff["changes"]} == {
        "BTC-USDT": "changed",
        "ETH-USDT": "not_observed",
        "SOL-USDT": "first_observed",
    }
    assert diff["absence_policy"] == "unknown_not_delisted"
    with pytest.raises(MarketError):
        catalog.observations.diff(first["id"], last["id"])
    swap = capture(catalog, monkeypatch, [raw("BTC-USDT-SWAP")], T + 2000, "SWAP")
    with pytest.raises(MarketError):
        catalog.observations.diff(swap["id"], last["id"])


def test_immutable_storage_and_hash_index_validation(catalog, monkeypatch):
    item = capture(catalog, monkeypatch, [raw()])
    with catalog.store.write() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE instrument_observations SET region='us'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM instrument_observations")
    with catalog.store.read() as conn:
        row = dict(conn.execute("SELECT * FROM instrument_observations").fetchone())
    with pytest.raises(MarketError):
        evidence.InstrumentObservations.checked(row | {"source": "example"})
    body = json.loads(row["body"])
    body["extra"] = "unsupported"
    with pytest.raises(MarketError):
        evidence.InstrumentObservations.checked(
            row | {"body": dumps(body), "content_hash": evidence.digest(body)}
        )
    assert catalog.observations.get(item["id"])["id"] == item["id"]


async def test_announced_expiry_blocks_cached_current_metadata_without_fabricating_settlement(
    catalog, monkeypatch
):
    from tidebench.store import now_ms

    expiry = now_ms() + 60000

    async def get(*_):
        return [raw(expTime=str(expiry))]

    catalog.market._get = get
    original = await catalog.get_instrument("BTC-USDT")
    assert original["expiry_time"] == expiry
    monkeypatch.setattr("tidebench.catalog.now_ms", lambda: expiry + 1)
    with pytest.raises(MarketError, match="unavailable"):
        await catalog.get_instrument("BTC-USDT")
    assert len(catalog.observations.list("okx", "global", "SPOT")) == 1


def test_contract_family_conflict_is_not_silently_corrected(catalog, monkeypatch):
    item = capture(catalog, monkeypatch, [raw("BTC-USDT-SWAP", instFamily="ETH-USDT")], inst_type="SWAP")
    assert item["members"][0]["metadata"] is None and item["members"][0]["eligibility"] == "unknown"


def test_nonstandard_json_and_oversized_arrays_are_explicit_source_failures(catalog, monkeypatch):
    for rows in ([float("nan")], [None] * 10001):
        with pytest.raises(MarketError) as error:
            capture(catalog, monkeypatch, rows)
        assert error.value.code == "invalid_upstream_data"
    assert catalog.observations.list("okx", "global", "SPOT") == []
