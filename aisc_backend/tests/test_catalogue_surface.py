"""One catalogue surface: one route module, one store, no second path (CAT-01).

CAT-01 is "one catalogue surface": a project discovers tests and checklists in
one place and installs from it. That is a property of the *shape* of the code, so
it is asserted on the code rather than on one flow:

* every catalogue route is declared in `routers/catalogue.py`, and no other router
  module declares one (a second module adding `/catalogue` fails here);
* an installed item lands in the project's own tables (`ProjectChecklist`, or a
  `Plugin` row) and the only table that mentions a catalogue id is the read-through
  template — there is no private catalogue store, no per-project catalogue copy;
* the listing is one endpoint answering both kinds (`kind` narrows it), so no
  second listing path exists for the UI to drift onto.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

ROUTERS = Path(__file__).resolve().parents[1] / "routers"
#: the one module allowed to declare catalogue routes
CATALOGUE_ROUTER = "catalogue.py"
#: a route decorator, and its path argument
ROUTE = re.compile(r"@router\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']")
#: what makes a path a catalogue path: it lists, installs from or exports it
CATALOGUE_PATH = re.compile(r"catalogue")
#: `control-objectives/catalogue` is the objectives catalogue (QUAL-01), a
#: different catalogue of a different thing: the AI Act objectives, not the
#: installable tests and checklists. It is expected, and named here so it cannot
#: quietly become a second install path.
OBJECTIVES_CATALOGUE = "control-objectives/catalogue"


def _routes(path: Path) -> list[tuple[str, str]]:
    return ROUTE.findall(path.read_text())


class OneCatalogueRouterTest(SimpleTestCase):

    def test_only_the_catalogue_router_declares_a_catalogue_route(self):
        holders: dict[str, list[str]] = {}
        for module in sorted(ROUTERS.glob("*.py")):
            found = [route for _method, route in _routes(module)
                     if CATALOGUE_PATH.search(route)]
            if found:
                holders[module.name] = found
        self.assertIn(CATALOGUE_ROUTER, holders)
        others = {name: routes for name, routes in holders.items() if name != CATALOGUE_ROUTER}
        # the objectives catalogue is not the installable one; anything else is a
        # second surface and fails here
        for name, routes in others.items():
            self.assertEqual(
                [r for r in routes if OBJECTIVES_CATALOGUE not in r], [],
                f"{name} declares a second catalogue surface",
            )

    def test_the_catalogue_router_is_the_only_place_that_installs_from_it(self):
        install = [(method, route) for module in ROUTERS.glob("*.py")
                   for method, route in _routes(module) if "install" in route]
        modules = {module.name for module in ROUTERS.glob("*.py") if _routes(module)
                   and any("install" in route for _m, route in _routes(module))}
        self.assertTrue(install)
        self.assertEqual(modules, {CATALOGUE_ROUTER})

    def test_the_registered_api_paths_match_that_one_router(self):
        """What the server actually serves, not just what the files say."""
        from config.urls import api  # noqa: PLC0415 - the built NinjaAPI

        declared = {
            f"/{route.lstrip('/')}" for _m, route in _routes(ROUTERS / CATALOGUE_ROUTER)
            if CATALOGUE_PATH.search(route) and not route.startswith("/projects")
        }
        registered = set()
        for prefix, router in getattr(api, "_routers", []):
            for path in router.path_operations:
                full = f"{prefix}/{path}".replace("//", "/")
                if CATALOGUE_PATH.search(full):
                    registered.add(full)
        self.assertTrue(declared, "no catalogue route was read from the router module")
        self.assertTrue(registered, "the API registers no catalogue route")
        for path in registered:
            self.assertTrue(
                any(declared_route in path or path in declared_route
                    for declared_route in declared),
                f"{path} is served but declared nowhere in {CATALOGUE_ROUTER}",
            )


class OneCatalogueStoreTest(SimpleTestCase):
    """Where a catalogue item goes when it is installed: the project's own tables."""

    def test_no_model_is_a_second_catalogue(self):
        """Only the read-through template carries a catalogue id."""
        holding = set()
        for model in apps.get_models():
            names = {field.name for field in model._meta.get_fields()}
            if {"catalogue_id"} & names:
                holding.add(model.__name__)
        self.assertEqual(holding, {"ControlTemplate"})

    def test_the_pilot_private_catalogue_was_not_ported(self):
        """No model, and so no table, holds a per-project copy of the catalogue."""
        names = {model.__name__.lower() for model in apps.get_models()}
        for forbidden in ("privatecatalogue", "projectcatalogue", "cataloguecopy",
                          "catalogueitem", "cataloguesnapshot"):
            self.assertNotIn(forbidden, {n.replace("_", "") for n in names},
                             f"{forbidden} would be a second catalogue store")
