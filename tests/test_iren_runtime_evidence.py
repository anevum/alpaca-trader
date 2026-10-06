from app.iren.topology import INVENTORY, OPTIONAL_INVENTORY


def test_v43_runtime_inventory_shape():
    assert set(INVENTORY) == {
        "RHEN",
        "VELUM",
        "GRAEN",
        "PREOPEN",
        "RESEARCH_AGENT",
        "IREN_EXECUTOR",
        "NOSTRA",
    }
    assert OPTIONAL_INVENTORY == {"PREOPEN", "IREN_EXECUTOR"}
