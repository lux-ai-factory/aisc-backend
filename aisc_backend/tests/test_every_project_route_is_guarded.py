"""Every route that reaches one project's work asks whether the caller is in it.

Wave 4 put the check at the door of a project and stopped there. What it did
not cover were the routes addressed by a child object's id: an evaluation, a
dataset, a model, an artifact, a project setting. The door checked the project
in the path; these had no project in their path and checked nothing, so any
signed-in account could read another project's results by id, and
`GET /evaluations?status=` handed out the ids.

So this does not test one route. It enumerates the API and fails when a
project-scoped route does not consult membership, which is the only version of
this that survives somebody adding a route next month.
"""
import inspect

from django.test import SimpleTestCase

from config.urls import api

#: Routes that legitimately touch nobody's project.
#:
#: Each one needs a reason, and the reason is here rather than in a commit
#: message, because this list is the only way past the check.
NOT_PROJECT_SCOPED = {
    "/api/v1/app/app-name": "the application's own name",
    "/api/v1/me": "who the caller is",
    "/api/v1/me/admin": "admin only, by role",
    "/api/v1/audit": "admin only, by role",
    "/api/v1/plugins": "installed plugins are the same for everyone; writing takes admin",
    "/api/v1/plugins/refresh": "admin only, by role",
    "/api/v1/projects": "lists what the caller is in, filters rather than refuses",
    "/api/v1/projects/for-platform/{platform_project_id}": "guarded, but by the platform id itself",
}


def operations_of(ninja_api):
    """Every (method, path, view) the API serves, including nested routers."""
    found = []
    for prefix, router in ninja_api._routers:
        for path, path_view in router.path_operations.items():
            for operation in path_view.operations:
                # ninja keeps prefixes without a leading slash; join explicitly
                # rather than by concatenation, which silently produced
                # "/apiv1/..." and exempted everything.
                parts = [p for p in ("api", prefix.strip("/"), path.strip("/")) if p]
                full = "/" + "/".join(parts)
                for method in operation.methods:
                    found.append((method, full, operation.view_func))
    return found


def looks_project_scoped(path: str) -> bool:
    """Does this route reach one project's work?

    Either it names a project, or it names something that belongs to one. The
    second half is the part that was missed: an evaluation pid is not a project
    pid, and the row behind it still belongs to somebody.
    """
    owned = ("{pid}", "{project_pid}", "{evaluation_pid}", "{dataset_pid}",
             "{model_pid}", "{file_name}", "{plugin_pid}", "{setting_pid}",
             # the AI system's parts and the project's configs, since the
             # AISystem merge: a component or a config belongs to one project
             "{component_pid}", "{project_config_pid}")
    if any(token in path for token in owned):
        return True
    # A listing with no id in it is project-scoped when it lists rows that
    # belong to projects.
    return path in ("/api/v1/evaluations", "/api/v1/datasets", "/api/v1/models")


class EveryProjectRouteIsGuardedTestCase(SimpleTestCase):
    maxDiff = None

    def test_the_enumeration_finds_the_api(self):
        operations = operations_of(api)
        self.assertGreater(len(operations), 20, "the enumeration is broken, not the API")
        paths = {path for _, path, _ in operations}
        self.assertIn("/api/v1/evaluations", paths)
        self.assertIn("/api/v1/files/dataset/{file_name}", paths)

    def test_nothing_reaches_a_project_without_asking(self):
        unguarded = []
        for method, path, view in operations_of(api):
            if path.startswith("/api/v1/internal"):
                continue  # the worker, on a shared key of its own
            if path in NOT_PROJECT_SCOPED or not looks_project_scoped(path):
                continue
            try:
                source = inspect.getsource(view)
            except OSError:  # pragma: no cover - only for a view with no source
                continue
            if "membership." not in source:
                unguarded.append(f"{method} {path}")
        self.assertEqual(
            [],
            sorted(unguarded),
            "these reach one project's work without asking whether the caller is in it",
        )

    def test_the_exemptions_are_all_real_routes(self):
        """An exemption for a path that no longer exists is a hole waiting for
        the path to come back under a different meaning."""
        paths = {path for _, path, _ in operations_of(api)}
        self.assertEqual([], sorted(set(NOT_PROJECT_SCOPED) - paths))
