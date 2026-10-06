from app.graen.service import GraenRuntime


def test_graen_runtime_has_no_production_promotion_authority():
    runtime = GraenRuntime()
    health = runtime.health()

    assert health["production_promotion_authority"] is False
    assert health["execution_authority"] is False
    assert health["broker_orders_possible"] is False
