from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "app" / "preopen_state"

FORBIDDEN_IMPORTS = (
    "from app.execution",
    "import app.execution",
    "from app.alpaca_client",
    "import app.alpaca_client",
    "from app.risk",
    "import app.risk",
    "from app.sizing",
    "import app.sizing",
    "from app.strategy",
    "import app.strategy",
    "from .execution",
    "from ..execution",
    "from .alpaca_client",
    "from ..alpaca_client",
    "from .risk",
    "from ..risk",
    "from .sizing",
    "from ..sizing",
    "from .strategy",
    "from ..strategy",
)


def test_preopen_package_has_no_live_execution_imports():
    for path in PACKAGE.glob("*.py"):
        source = path.read_text()
        for forbidden in FORBIDDEN_IMPORTS:
            assert forbidden not in source, f"{path} contains {forbidden}"


def test_live_main_does_not_import_preopen_layer():
    source = (ROOT / "app" / "main.py").read_text()
    assert "preopen_state" not in source


def test_shadow_service_declares_no_execution_authority():
    source = (PACKAGE / "service.py").read_text()
    assert '"shadow_only": True' in source
    assert '"execution_authority": False' in source
