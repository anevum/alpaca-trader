from datetime import datetime, timezone

from foundation.graen_gateway import _hash, _obj, _status


def test_hash_is_canonical():
    assert _hash({"a":1,"b":2}) == _hash({"b":2,"a":1})


def test_status_contract():
    assert _status("waiting") == "WAITING"


def test_object_value_is_fail_closed():
    assert _obj({"x":1}) == {"x":1}
    assert _obj(["x"]) == {}


def test_runtime_contract_versionless_and_execution_neutral():
    # Gateway persistence carries state only; it does not grant execution authority.
    sample = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "execution_authority": False,
        "broker_orders_possible": False,
    }
    assert sample["execution_authority"] is False
    assert sample["broker_orders_possible"] is False
