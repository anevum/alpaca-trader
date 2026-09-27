import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "app" / "research_agent"
FORBIDDEN = {
    "app.execution",
    "app.alpaca_client",
    "app.strategy",
    "app.risk",
    "app.sizing",
    "app.market_data",
    "app.main",
}


def imported_module(node):
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return None
        return node.module
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return None


def test_research_agent_package_has_no_live_execution_import_coupling():
    observed = set()
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            module = imported_module(node)
            if isinstance(module, str):
                observed.add(module)
            elif isinstance(module, list):
                observed.update(module)
    assert observed.isdisjoint(FORBIDDEN)


def test_research_agent_is_not_imported_by_existing_live_application_modules():
    offenders = []
    for path in (ROOT / "app").glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            module = imported_module(node)
            names = [module] if isinstance(module, str) else (module or [])
            if any(name.startswith("app.research_agent") for name in names):
                offenders.append(path.name)
    assert offenders == []
