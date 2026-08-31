from __future__ import annotations

"""
Enforces "APScheduler is a pure resumption driver" as a real, running check
rather than just a docstring promise: static analysis of
scheduling/scheduler.py's own source, not its behavior — no job function may
import an agent or a Publisher, and Post must never be imported into the
module at all (if it were, nothing would stop a job from querying it
directly).

_run_startup_diagnostics() is a deliberate, documented exception (see that
function's docstring in scheduler.py) — it's not registered via
scheduler.add_job(), so it isn't a "job" under this constraint, and this
test excludes it explicitly rather than by accident.
"""

import ast
from pathlib import Path

SCHEDULER_PATH = Path(__file__).resolve().parent.parent / "scheduling" / "scheduler.py"
FORBIDDEN_JOB_MODULES = ("agents", "publishing")


def _parse_scheduler():
    source = SCHEDULER_PATH.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(SCHEDULER_PATH))


def _job_function_defs(tree) -> list[ast.FunctionDef]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("job_")
    ]


def _imported_module_roots(node) -> list[str]:
    roots = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            roots.extend(alias.name.split(".")[0] for alias in sub.names)
        elif isinstance(sub, ast.ImportFrom) and sub.module:
            roots.append(sub.module.split(".")[0])
    return roots


def test_job_functions_exist():
    tree = _parse_scheduler()
    job_functions = _job_function_defs(tree)
    job_names = {f.name for f in job_functions}
    assert job_names == {"job_inbox_poll", "job_slot_sweep", "job_reminders_and_expiry"}


def test_no_job_function_imports_agents_or_publishing():
    tree = _parse_scheduler()
    for func in _job_function_defs(tree):
        roots = _imported_module_roots(func)
        forbidden = [r for r in roots if r in FORBIDDEN_JOB_MODULES]
        assert not forbidden, f"{func.name} imports forbidden module(s): {forbidden}"


def test_post_model_is_never_imported_anywhere_in_scheduler():
    tree = _parse_scheduler()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "database" in node.module:
            names = {alias.name for alias in node.names}
            assert "Post" not in names, "scheduling/scheduler.py must never import Post"


def test_startup_diagnostics_is_not_registered_as_a_job():
    """Confirms _run_startup_diagnostics (which legitimately imports
    publishing.factory) is called directly in main(), not via
    scheduler.add_job — i.e. it really isn't "a job" under this constraint."""
    source = SCHEDULER_PATH.read_text(encoding="utf-8")
    assert "scheduler.add_job(_run_startup_diagnostics" not in source
    assert "_run_startup_diagnostics()" in source  # called directly


def test_scheduler_module_imports_only_orchestrator_for_business_logic():
    """The module-level import surface should route all business logic
    through orchestrator.* — this is the "no job imports an agent or
    Publisher, or mutates a Post row" constraint checked at the whole-module
    level, for defense in depth beyond the per-job check above."""
    tree = _parse_scheduler()
    module_level_roots = []
    for node in tree.body:  # top-level only, not nested in functions
        if isinstance(node, ast.Import):
            module_level_roots.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level_roots.append(node.module.split(".")[0])

    forbidden = [r for r in module_level_roots if r in FORBIDDEN_JOB_MODULES]
    assert not forbidden, f"module-level imports include forbidden module(s): {forbidden}"
