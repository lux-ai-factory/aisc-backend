"""Every project-scoped route must authorize the project before it answers.

A route whose path carries a project id (`{pid}` or `{project_pid}`) is a door
into one project. Such a route must resolve that project and check the caller's
membership (`role_or_404` / `owner_or_403`, which answer 404 to a non-member), or
be listed below as a deliberate exception with a reason.

This is a static guard: it reads the router sources (no Django, no DB) and fails
if a new project-scoped route forgets the project-authorization dependency. Run it
on its own:

    uv run --no-project --with pytest python -m pytest \
        tests/guards/project_scoped_routes_guard_test.py -q

It lives outside the `aisc_backend` package (and does not match the Django runner's
`test*.py` pattern) so it can run with a bare pytest, no project environment.
"""
import ast
from pathlib import Path

ROUTERS = Path(__file__).resolve().parents[2] / "aisc_backend" / "routers"

VERBS = {"get", "post", "put", "patch", "delete"}

#: Calls that resolve the project and refuse a caller who is not a member (404).
PROJECT_AUTHORIZATION_CALLS = {"role_or_404", "owner_or_403"}

#: Project-scoped routes that deliberately authorize differently, each with why.
#: Keyed by (router file, path template, handler name).
EXCEPTIONS = {
    ("internal.py", "/projects/settings/{project_pid}", "get_project_configs"):
        "internal: service-to-service route behind InternalSharedKeyAuth; the project id "
        "is the payload's subject, not the caller's membership.",
    ("internal.py", "/projects/settings/{project_pid}/by-pid", "get_project_configs_by_pid"):
        "internal: same InternalSharedKeyAuth service route as get_project_configs.",
    ("task.py", "{pid}/status", "get_evaluation_tasks_status"):
        "global: {pid} is an evaluation's opaque celery task id, not a project id.",
    ("task.py", "{pid}/stop", "stop_evaluation"):
        "global: {pid} is an evaluation's opaque celery task id, not a project id.",
}


def _route_path(decorator: ast.expr) -> str | None:
    """The path template of a `@router.<verb>("...")` decorator, if any."""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not (isinstance(func, ast.Attribute) and func.attr in VERBS):
        return None
    if not (isinstance(func.value, ast.Name) and func.value.id == "router"):
        return None
    if decorator.args and isinstance(decorator.args[0], ast.Constant) \
            and isinstance(decorator.args[0].value, str):
        return decorator.args[0].value
    return None


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _same_module_calls(tree: ast.Module) -> dict[str, set[str]]:
    calls: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls.setdefault(node.name, set()).update(_called_names(node))
    return calls


def _transitively_calls(name: str, calls: dict[str, set[str]]) -> set[str]:
    """Every name reachable from `name` through same-module calls."""
    seen: set[str] = set()
    frontier = [name]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        frontier.extend(calls.get(current, set()) - seen)
    return seen


def _project_scoped_routes() -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """(all project-scoped routes, the subset that authorizes the project)."""
    routes: list[tuple[str, str, str]] = []
    authorized: list[tuple[str, str, str]] = []
    for path in sorted(ROUTERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = _same_module_calls(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route = next(
                (p for p in (_route_path(d) for d in node.decorator_list) if p is not None),
                None,
            )
            if route is None or ("{pid}" not in route and "{project_pid}" not in route):
                continue
            key = (path.name, route, node.name)
            routes.append(key)
            if _transitively_calls(node.name, calls) & PROJECT_AUTHORIZATION_CALLS:
                authorized.append(key)
    return routes, authorized


def test_project_scoped_routes_authorize_the_project():
    routes, authorized = _project_scoped_routes()
    assert routes, "no project-scoped routes found — the guard scanned the wrong tree"

    uncovered = [key for key in routes if key not in authorized and key not in EXCEPTIONS]
    assert not uncovered, (
        "project-scoped routes must call role_or_404/owner_or_403 (or be listed in "
        "EXCEPTIONS with a reason):\n"
        + "\n".join(f"  {module} {route} :: {handler}" for module, route, handler in uncovered)
    )

    stale = sorted(set(EXCEPTIONS) - set(routes))
    assert not stale, (
        "EXCEPTIONS names routes that no longer exist (drop the stale entries):\n"
        + "\n".join(f"  {module} {route} :: {handler}" for module, route, handler in stale)
    )
