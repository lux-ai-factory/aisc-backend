"""A test's plugin class handed to the engine the way the worker hands it: as the manifest the sandbox reads
from it, never imported by the engine. Its form hooks, when it overrides them, answer as the worker would run
them (the class is test code, run here in its place)."""
import base64
from unittest import mock

from aisc_backend import plugin_declarations
from aisc_plugin_interface import BaseEvaluationPlugin, describe
from aisc_plugin_interface.manifest import HOOKS, manifest_of

WHEEL = "0" * 64


class _EveryClass(dict):
    """A package whose every class name is the given plugin: the row asks for its own name."""

    def __init__(self, manifest: dict):
        super().__init__()
        self.manifest = manifest

    def __missing__(self, name):
        return self.manifest

    def get(self, name, default=None):
        return self.manifest


def _class(plugin) -> type:
    """The plugin class: a fixture that leaves evaluate abstract gets one, as a package's class would."""
    cls = plugin if isinstance(plugin, type) else type(plugin)
    if issubclass(cls, BaseEvaluationPlugin) and getattr(cls.evaluate, "__isabstractmethod__", False):
        cls = type(cls.__name__, (cls,), {"evaluate": lambda self, config_data: None,
                                          "plugin_name": cls.plugin_name})
    return cls


def _manifest(plugin) -> dict:
    """manifest_of a plugin class; a stand-in object that only answers what a test needs (not a plugin class)
    gives what it answers, its form hooks marked overridden when it has them."""
    cls = _class(plugin)
    if issubclass(cls, BaseEvaluationPlugin):
        return manifest_of(cls)
    obj = plugin() if isinstance(plugin, type) else plugin
    try:
        schema, ui = obj.get_full_schema()
    except Exception:  # noqa: BLE001 - as the sandbox says a form it cannot build
        schema, ui = None, None
    try:
        declaration = describe(cls) if not isinstance(obj, mock.Mock) else {}
    except Exception:  # noqa: BLE001 - a stand-in without the declarations
        declaration = {}
    if isinstance(obj, mock.Mock):
        declaration = {"target_kinds": list(getattr(obj, "target_kinds", ()) or ())}
    return {"name": cls.__name__, "display_name": getattr(obj, "display_name", cls.__name__),
            "help_text": getattr(obj, "help_text", ""), "display_icon": "", "feature_flags": {},
            "input_definitions": [], "project_config_definitions": [],
            "form_schema": schema, "form_ui_schema": ui,
            "metrics": list(obj.get_metrics()) if hasattr(type(obj), "get_metrics") else [],
            "metric_visualizations": [],
            "declaration": declaration, "adopted": getattr(obj, "target_kinds", None) is not None
            and not isinstance(obj, mock.Mock),
            "hooks": [hook for hook in HOOKS if hasattr(type(obj), hook)]}


def read_of(plugin=None, *, package: dict | None = None) -> dict:
    """What the worker's read_manifest answers: for `plugin` under whatever class name a row asks, or for a
    package of {class name: plugin}."""
    if package is not None:
        plugins = {name: _manifest(cls) for name, cls in package.items()}
    else:
        plugins = _EveryClass(_manifest(plugin))
    return {"wheel_sha256": WHEEL, "dependency_lock": "", "manifest": {"plugins": plugins}, "code": None}


def _run_hook(plugin_or_class):
    def run(row, hook, args):
        plugin = plugin_or_class() if isinstance(plugin_or_class, type) else plugin_or_class
        if hook == "parse_config_from_dataset":
            return plugin.parse_config_from_dataset(base64.b64decode(args.get("content_b64") or ""))
        if hook == "on_config_change":
            return list(plugin.on_config_change(args.get("config")))
        return [v.model_dump(mode="json") for v in plugin.get_metric_visualizations(args.get("config") or {})]
    return run


class reads:
    """Within it, the worker reads `plugin` (or `package`), and runs its form hooks."""

    def __init__(self, plugin=None, *, package: dict | None = None, side_effect=None):
        if side_effect is not None:
            fetch = mock.patch.object(plugin_declarations, "fetch_manifest", side_effect=side_effect)
        else:
            fetch = mock.patch.object(plugin_declarations, "fetch_manifest",
                                      return_value=read_of(plugin, package=package))
        self.patches = [fetch]
        if plugin is not None:
            self.patches.append(mock.patch.object(plugin_declarations, "plugin_hook",
                                                  side_effect=_run_hook(plugin if not isinstance(plugin, type)
                                                                        else _class(plugin))))

    def __enter__(self):
        mocks = [p.start() for p in self.patches]
        return mocks[0]

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        return False

    # as a patcher started in setUp
    start = __enter__

    def stop(self):
        self.__exit__(None, None, None)

    # as a decorator of a test method too
    def __call__(self, func):
        import functools
        import inspect

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def wrapped(*a, **k):
                with self:
                    return await func(*a, **k)
        else:
            @functools.wraps(func)
            def wrapped(*a, **k):
                with self:
                    return func(*a, **k)
        return wrapped


def declared_of(plugin, row=None) -> plugin_declarations.Declared:
    """`plugin` as the engine sees it once its manifest is stored."""
    return plugin_declarations.Declared(row, _manifest(plugin))


class as_declared:
    """Within it, every plugin row is declared as `plugin` (each call, as a stored manifest of it would answer),
    and its form hooks run as the worker would run them; with side_effect, no manifest can be read: the pages
    answer 503 and the code that reads only stored manifests finds none."""

    def __init__(self, plugin=None, *, side_effect=None):
        async def declared(row):
            if side_effect is not None:
                raise plugin_declarations.HttpError(503, plugin_declarations.NOT_READ.format(
                    reason=plugin_declarations._reason(side_effect)))
            return declared_of(plugin, row)

        def stored(row):
            return None if side_effect is not None else declared_of(plugin, row)

        self.patches = [mock.patch.object(plugin_declarations, "declared", side_effect=declared),
                        mock.patch.object(plugin_declarations, "stored", side_effect=stored)]
        if plugin is not None:
            self.patches.append(mock.patch.object(plugin_declarations, "plugin_hook",
                                                  side_effect=_run_hook(plugin if not isinstance(plugin, type)
                                                                        else _class(plugin))))

    __enter__ = reads.__enter__
    __exit__ = reads.__exit__
    __call__ = reads.__call__
    start = reads.start
    stop = reads.stop
