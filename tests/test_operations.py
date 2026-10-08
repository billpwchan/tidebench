import pytest
from tidebench.operations import IncidentStore
from tidebench.platform import PlatformError
from tidebench.store import Store


def test_incident_acknowledgement_does_not_hide_failure_and_recurrence_reopens(tmp_path):
    incidents = IncidentStore(Store(tmp_path / "ops.sqlite3"))
    conditions = [{"kind": "quote", "subject": "okx:BTC-USDT", "details": {"error": "transport timeout"}}]
    incidents.reconcile(conditions)
    first = incidents.list()[0]
    incidents.acknowledge(first["id"], "operator", "Investigating feed transport and failover.")
    incidents.reconcile(conditions)
    assert incidents.list()[0]["status"] == "acknowledged"
    restored = IncidentStore(incidents.store)
    assert restored.list()[0]["ack_actor"] == "operator"
    restored.reconcile([])
    assert restored.list()[0]["status"] == "resolved"
    with pytest.raises(PlatformError, match="recovered"):
        restored.acknowledge(first["id"], "operator", "An outdated incident response note.")
    restored.reconcile(conditions)
    reopened = restored.list()[0]
    assert reopened["status"] == "open" and reopened["occurrences"] == 2
    assert reopened["ack_actor"] is None
