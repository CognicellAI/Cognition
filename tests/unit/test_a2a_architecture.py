"""Architecture guardrails for the optional A2A transport facade."""

from __future__ import annotations

import ast
from pathlib import Path


def test_a2a_sdk_imports_are_confined_to_layer_6_adapter() -> None:
    """Prevent A2A wire types from leaking into Cognition's neutral runtime."""
    server_root = Path(__file__).parents[2] / "server" / "app"
    adapter_root = server_root / "protocols" / "a2a"
    violations: list[str] = []

    for path in server_root.rglob("*.py"):
        if path.is_relative_to(adapter_root):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules.append(node.module)
            if any(module == "a2a" or module.startswith("a2a.") for module in modules):
                violations.append(str(path.relative_to(server_root)))

    assert violations == []
