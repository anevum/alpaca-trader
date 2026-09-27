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
FORBIDDEN_PROVIDER_PREFIXES = (
    "openai",
    "anthropic",
    "google.generativeai",
    "litellm",
    "langchain",
)
BROKER_ORDER_METHODS = {
    "submit_order",
    "place_order",
    "create_order",
    "cancel_order",
    "cancel_all_orders",
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
    assert not any(
        module.startswith(FORBIDDEN_PROVIDER_PREFIXES) for module in observed
    )


def test_research_agent_exposes_no_broker_order_or_stage_execution_methods():
    defined = set()
    accessed = set()
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Attribute):
                accessed.add(node.attr)
    assert defined.isdisjoint(BROKER_ORDER_METHODS)
    assert accessed.isdisjoint(BROKER_ORDER_METHODS)
    assert defined.isdisjoint({"run_development", "run_validation", "run_holdout"})


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


def test_cli_has_no_protected_stage_runner_or_production_promotion_command():
    script = ROOT / "scripts" / "rhen_research_agent.py"
    tree = ast.parse(script.read_text(), filename=str(script))
    string_literals = {
        node.value.casefold()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not string_literals.intersection(
        {
            "run-development",
            "run-validation",
            "run-holdout",
            "promote-production",
        }
    )
