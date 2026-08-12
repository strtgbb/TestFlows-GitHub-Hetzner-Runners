"""Guard test for the import-layering invariant.

The config package and the providers layer used to import each other, forcing
many imports inline to dodge `partially initialized module` cycles. After the
config_schema / argtypes extraction the invariant is:

  * nothing in the ``providers`` layer may import the ``config`` package or the
    ``args`` module at module top level (they use the ``config_schema`` and
    ``argtypes`` leaves instead);
  * the leaves ``cloud_provider`` / ``config_schema`` / ``argtypes`` may not
    import ``config`` / ``args`` / ``providers`` at top level either.

This test statically parses each module and fails if a top-level import
reintroduces a forbidden edge. Inline (in-function) imports are ignored — they
remain the escape hatch for the rare genuine cycle.
"""
import ast
import os

from testflows.core import *

_REPO_ROOT = os.path.abspath(os.path.join(current_dir(), "..", "..", "..", ".."))
_PKG_ROOT = os.path.join(_REPO_ROOT, "testflows", "runners")

_CONFIG = "testflows.runners.config"
_ARGS = "testflows.runners.args"
_PROVIDERS = "testflows.runners.providers"


def _module_name(path: str) -> str:
    rel = os.path.relpath(path, _REPO_ROOT)[: -len(".py")]
    return rel.replace(os.sep, ".")


def _resolve(module_name: str, node: ast.ImportFrom):
    """Resolve a `from ... import` to absolute dotted targets."""
    if node.level == 0:
        base = node.module
        return [base] if base else []
    # relative: anchor = the module's package, minus (level-1) components
    pkg = module_name.rsplit(".", 1)[0]
    parts = pkg.split(".")
    anchor = ".".join(parts[: len(parts) - (node.level - 1)]) if node.level > 1 else pkg
    if node.module:
        return [f"{anchor}.{node.module}"]
    # `from . import a, b` -> sibling submodules
    return [f"{anchor}.{alias.name}" for alias in node.names]


def _targets(module_name: str, tree: ast.Module):
    """Absolute dotted targets of every MODULE-LEVEL import."""
    out = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            out += _resolve(module_name, node)
    return out


def _forbidden(target: str, forbid_providers: bool) -> bool:
    if target == _CONFIG or target.startswith(_CONFIG + "."):
        return True
    if target == _ARGS or target.startswith(_ARGS + "."):
        return True
    if forbid_providers and (target == _PROVIDERS or target.startswith(_PROVIDERS + ".")):
        return True
    return False


def _scan_files():
    # (path, forbid_providers): provider modules may import sibling provider
    # modules, so only the leaves additionally forbid importing `providers`.
    files = []
    providers_dir = os.path.join(_PKG_ROOT, "providers")
    for root, _dirs, names in os.walk(providers_dir):
        for name in names:
            if name.endswith(".py"):
                files.append((os.path.join(root, name), False))
    for leaf in ("cloud_provider.py", "config_schema.py", "argtypes.py"):
        files.append((os.path.join(_PKG_ROOT, leaf), True))
    return files


@TestScenario
def providers_and_leaves_do_not_import_config_or_args_at_top(self):
    """No top-level import of the config package / args module from the
    providers layer or the config_schema / argtypes / cloud_provider leaves."""
    violations = []
    for path, forbid_providers in _scan_files():
        module_name = _module_name(path)
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for target in _targets(module_name, tree):
            if _forbidden(target, forbid_providers):
                violations.append(f"{module_name} -> {target}")

    assert not violations, "forbidden top-level imports (reintroduce a cycle):\n" + "\n".join(
        sorted(violations)
    )


@TestScenario
def guard_detects_a_planted_violation(self):
    """Sanity: the resolver flags a config-package import (so the guard isn't
    vacuously passing)."""
    src = "from ...config import Config\n"
    tree = ast.parse(src)
    targets = _targets("testflows.runners.providers.hetzner.provider", tree)
    assert any(_forbidden(t, False) for t in targets), targets


@TestScenario
def guard_allows_config_schema_and_argtypes(self):
    """Sanity: importing the leaves is allowed (not a false positive)."""
    src = "from ...config_schema import Config\nfrom ...argtypes import path_type\n"
    tree = ast.parse(src)
    targets = _targets("testflows.runners.providers.hetzner.provider", tree)
    assert not any(_forbidden(t, False) for t in targets), targets


@TestFeature
@Name("import layering")
def feature(self):
    """Static guard for the config/providers/args import-layering invariant."""
    for scenario in loads(current_module(), Scenario):
        scenario()
