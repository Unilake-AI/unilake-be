from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

APP_ROOT = Path(__file__).parents[1] / "app"
BACKEND_ROOT = APP_ROOT.parent


def _module_name(path: Path) -> str:
    relative = path.relative_to(BACKEND_ROOT).with_suffix("")
    parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
    return ".".join(parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def _app_modules() -> dict[str, Path]:
    return {_module_name(path): path for path in APP_ROOT.rglob("*.py")}


def _concrete_domain(module: str) -> bool:
    parts = module.split(".")
    return (
        len(parts) >= 3
        and parts[:2] == ["app", "domains"]
        and parts[2]
        not in {
            "base",
            "registry",
        }
    )


def test_core_and_shared_do_not_depend_on_features_or_infrastructure() -> None:
    violations: list[str] = []
    for source, path in _app_modules().items():
        if not (source == "app.core" or source.startswith("app.core.")) and not (
            source == "app.shared" or source.startswith("app.shared.")
        ):
            continue
        for target in _imports(path):
            if target.startswith(("app.modules", "app.infra", "app.domains")):
                violations.append(f"{source} -> {target}")

    assert violations == []


def test_concrete_domains_depend_only_on_domain_contracts() -> None:
    violations: list[str] = []
    for source, path in _app_modules().items():
        if not _concrete_domain(source):
            continue
        for target in _imports(path):
            if target.startswith(("app.core", "app.modules", "app.infra", "fastapi", "sqlalchemy")):
                violations.append(f"{source} -> {target}")

    assert violations == []


def test_generic_modules_do_not_import_concrete_domains() -> None:
    violations = [
        f"{source} -> {target}"
        for source, path in _app_modules().items()
        if source.startswith("app.modules.")
        for target in _imports(path)
        if _concrete_domain(target)
    ]

    assert violations == []


def test_cross_module_imports_use_public_contracts_only() -> None:
    violations: list[str] = []
    for source, path in _app_modules().items():
        source_parts = source.split(".")
        if len(source_parts) < 3 or source_parts[:2] != ["app", "modules"]:
            continue
        source_module = source_parts[2]
        for target in _imports(path):
            target_parts = target.split(".")
            if len(target_parts) < 3 or target_parts[:2] != ["app", "modules"]:
                continue
            target_module = target_parts[2]
            if target_module == source_module:
                continue
            is_public_contract = len(target_parts) >= 4 and target_parts[3] == "contracts"
            if not is_public_contract:
                violations.append(f"{source} -> {target}")

    assert violations == []


def test_modules_do_not_depend_on_concrete_infrastructure() -> None:
    violations: list[str] = []
    for source, path in _app_modules().items():
        if not source.startswith("app.modules."):
            continue
        for target in _imports(path):
            if not target.startswith("app.infra"):
                continue
            is_persistence_model = path.name == "models.py" and target == "app.infra.db.base"
            if not is_persistence_model:
                violations.append(f"{source} -> {target}")

    assert violations == []


def test_infrastructure_imports_only_module_contracts() -> None:
    violations: list[str] = []
    for source, path in _app_modules().items():
        if not source.startswith("app.infra."):
            continue
        for target in _imports(path):
            if not target.startswith("app.modules."):
                continue
            parts = target.split(".")
            if len(parts) < 4 or parts[3] != "contracts":
                violations.append(f"{source} -> {target}")

    assert violations == []


def test_internal_modules_do_not_import_routers() -> None:
    violations = [
        f"{source} -> {target}"
        for source, path in _app_modules().items()
        if source != "app.main"
        for target in _imports(path)
        if target.startswith("app.modules.") and target.endswith(".router")
    ]

    assert violations == []


def test_application_import_graph_has_no_cycles() -> None:
    modules = _app_modules()
    graph: dict[str, set[str]] = defaultdict(set)
    for source, path in modules.items():
        for target in _imports(path):
            if target in modules and target != source:
                graph[source].add(target)

    visiting: list[str] = []
    visited: set[str] = set()

    def visit(module: str) -> list[str] | None:
        if module in visiting:
            cycle_start = visiting.index(module)
            return [*visiting[cycle_start:], module]
        if module in visited:
            return None

        visiting.append(module)
        for dependency in sorted(graph[module]):
            cycle = visit(dependency)
            if cycle is not None:
                return cycle
        visiting.pop()
        visited.add(module)
        return None

    detected_cycle: list[str] | None = None
    for module in sorted(modules):
        detected_cycle = visit(module)
        if detected_cycle is not None:
            break

    assert detected_cycle is None, " -> ".join(detected_cycle or [])


def test_removed_legacy_and_compatibility_packages_stay_removed() -> None:
    assert not (APP_ROOT / "core" / "database.py").exists()
    assert not (APP_ROOT / "modules" / "ai_engine").exists()
    assert not (APP_ROOT / "modules" / "query").exists()
