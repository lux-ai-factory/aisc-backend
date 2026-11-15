"""What the engine shows of a plugin, answered from the manifest stored on its row: the engine never imports
plugin code.

The worker reads a manifest once, in its sandbox (aisc_eval.celery_tasks.read_manifest): it installs the plugin
from a hashed lock that must name the recorded wheel and has the plugin describe itself as a run user with no
network. An install stores it on each of the package's rows with the wheel's hash, the lock and, for a local
plugin, its folder's digest. A row installed before manifests existed gets its manifest the first time a page
needs it, and keeps it; a local plugin whose folder changed since gets it again. The three form hooks a plugin
may override (aisc_plugin_interface.manifest.HOOKS) are asked of the worker the same way, one call at a time,
and only when the manifest says the plugin overrides them.
"""

import base64
import copy
import logging
import os

from asgiref.sync import sync_to_async
from ninja.errors import HttpError

from aisc_backend.config_secrets import scrub_text
from aisc_backend.models import Plugin
from aisc_plugin_interface import InputDefinition, MetricVisualization, PluginFeatureFlags
from aisc_plugin_interface.models.project_config_definition import ProjectConfigDefinition

logger = logging.getLogger(__name__)

READ_MANIFEST_TASK = "aisc_eval.celery_tasks.read_manifest"
PLUGIN_HOOK_TASK = "aisc_eval.celery_tasks.plugin_hook"
#: the worker's queue for both (aisc_eval.celery_app.PLUGIN_DECLARATIONS_QUEUE): a worker process of its own serves
#: it, so a configure page never waits behind the runs
DECLARATIONS_QUEUE = "plugin-declarations"
#: seconds the engine waits for the worker: a first read installs the plugin
WAIT_SETTING = "PLUGIN_MANIFEST_TIMEOUT_S"
WAIT_DEFAULT_S = 900
NOT_READ = "the plugin's declarations could not be read: {reason}"
#: the largest dataset sent to the worker for the plugin to read a configuration from (it travels in the task)
DATASET_HOOK_MAX_BYTES = 64 * 2**20
HOOK_FAILED = "the plugin could not answer ({hook}): {reason}"
FORM_NOT_BUILT = "the plugin's configuration form could not be built"


class FormNotBuilt(ValueError):
    """The manifest says the plugin's form could not be built."""


def _wait_s() -> int:
    raw = os.environ.get(WAIT_SETTING, "").strip()
    try:
        return int(raw) if raw else WAIT_DEFAULT_S
    except ValueError:
        return WAIT_DEFAULT_S


def _ask_worker(task: str, args: list):
    from aisc_backend.services.celery_service import celery  # the celery app reads the broker settings

    sent = celery.send_task(task, args=args, queue=DECLARATIONS_QUEUE)
    return sent.get(timeout=_wait_s(), propagate=True, disable_sync_subtasks=False)


def fetch_manifest(package_name: str, version: str, wheel_sha256: str | None) -> dict:
    """{"wheel_sha256", "dependency_lock", "manifest": {"plugins": {class name: manifest}}, "code"}, read by the
    worker. wheel_sha256 is the hash the wheel must have (None: the index's)."""
    return _ask_worker(READ_MANIFEST_TASK, [package_name, version, wheel_sha256])


def plugin_hook(row: Plugin, hook: str, args: dict):
    """One form hook of the row's plugin, run by the worker in the environment of the row's recorded wheel."""
    return _ask_worker(PLUGIN_HOOK_TASK, [row.package_name, row.version, row.name, hook, args,
                                          {"wheel_sha256": row.wheel_sha256}])


def _reason(exc: BaseException) -> str:
    text = " ".join(scrub_text(str(exc) or type(exc).__name__).split())
    return text[:300]


class Declared:
    """A plugin as its stored manifest declares it: the attributes and methods the routers read of a plugin
    object, none of which runs plugin code except the three form hooks, which the worker runs."""

    def __init__(self, row: Plugin, manifest: dict | None = None):
        self.row = row
        self.manifest = manifest if manifest is not None else (row.manifest or {})

    @property
    def display_name(self) -> str:
        return self.manifest.get("display_name") or self.row.display_name

    @property
    def help_text(self) -> str:
        return self.manifest.get("help_text") or ""

    @property
    def display_icon(self) -> str:
        return self.manifest.get("display_icon") or "extension"

    @property
    def feature_flags(self) -> PluginFeatureFlags:
        return PluginFeatureFlags.model_validate(self.manifest.get("feature_flags") or {})

    @property
    def input_definitions(self) -> list[InputDefinition]:
        return [InputDefinition.model_validate(d) for d in self.manifest.get("input_definitions") or []]

    @property
    def project_config_definitions(self) -> list[ProjectConfigDefinition]:
        return [ProjectConfigDefinition.model_validate(d)
                for d in self.manifest.get("project_config_definitions") or []]

    @property
    def declaration(self) -> dict:
        return self.manifest.get("declaration") or {}

    @property
    def adopted(self) -> bool:
        return bool(self.manifest.get("adopted"))

    @property
    def local_model_input(self) -> str | None:
        return self.declaration.get("local_model_input")

    @property
    def hooks(self) -> list[str]:
        return list(self.manifest.get("hooks") or [])

    def get_full_schema(self) -> tuple[dict, dict]:
        """The form as the plugin first shows it, from the manifest; FormNotBuilt when the sandbox could not
        build it."""
        schema = self.manifest.get("form_schema")
        if not isinstance(schema, dict):
            raise FormNotBuilt(FORM_NOT_BUILT)
        return copy.deepcopy(schema), copy.deepcopy(self.manifest.get("form_ui_schema") or {})

    def get_metrics(self) -> list[str]:
        return list(self.manifest.get("metrics") or [])

    async def _hook(self, hook: str, args: dict):
        try:
            return await sync_to_async(plugin_hook, thread_sensitive=False)(self.row, hook, args)
        except Exception as exc:  # noqa: BLE001 - the person is told the plugin could not answer, and why
            logger.warning("plugin %s: its %s hook failed: %s", self.row, hook, _reason(exc))
            raise HttpError(503, HOOK_FAILED.format(hook=hook, reason=_reason(exc))) from None

    async def on_config_change(self, config) -> tuple:
        """(config, schema, ui schema) of the form for `config`: the plugin's hook when it overrides it,
        else the stored form."""
        if "on_config_change" not in self.hooks:
            try:
                return (config, *self.get_full_schema())
            except FormNotBuilt as exc:
                raise HttpError(503, str(exc)) from None
        answer = await self._hook("on_config_change", {"config": config})
        if not isinstance(answer, list) or len(answer) != 3:
            raise HttpError(503, HOOK_FAILED.format(hook="on_config_change", reason="not a form"))
        new_config, schema, ui = answer
        return new_config, (schema if isinstance(schema, dict) else {}), (ui if isinstance(ui, dict) else {})

    async def parse_config_from_dataset(self, content: bytes) -> dict | None:
        """A configuration read from a dataset by the plugin's hook; None when it has none."""
        if "parse_config_from_dataset" not in self.hooks:
            return None
        if len(content) > DATASET_HOOK_MAX_BYTES:
            raise HttpError(413, f"the dataset is larger than {DATASET_HOOK_MAX_BYTES // 2**20} MB: the plugin is "
                                 "not asked to read a configuration from it")
        return await self._hook("parse_config_from_dataset", {"content_b64": base64.b64encode(content).decode()})

    async def get_metric_visualizations(self, config) -> list[MetricVisualization]:
        """The charts for a configuration: the plugin's hook when it overrides it, else the stored ones. A hook
        that fails leaves the stored charts: the results are shown anyway."""
        stored = [MetricVisualization.model_validate(v) for v in self.manifest.get("metric_visualizations") or []]
        if "get_metric_visualizations" not in self.hooks:
            return stored
        try:
            answer = await self._hook("get_metric_visualizations", {"config": config or {}})
            return [MetricVisualization.model_validate(v) for v in answer]
        except Exception:  # noqa: BLE001 - logged by _hook or a chart the engine cannot read
            logger.warning("plugin %s: its charts for this configuration could not be read; the stored ones are "
                           "shown", self.row)
            return stored


def _local_code_changed(row: Plugin) -> bool:
    """A local plugin whose folder's digest is no longer the one its manifest was read from."""
    if row.wheel_sha256 or not isinstance(row.code_identity, dict):
        return False
    from aisc_backend.routers.plugin import plugin_loader

    try:
        now = plugin_loader.local_provenance(row.package_name, row.version)
    except Exception:  # noqa: BLE001 - a folder that cannot be read keeps the manifest it has
        logger.warning("plugin %s: its local folder could not be read", row)
        return False
    return bool(now and now.get("digest") and now.get("digest") != row.code_identity.get("digest"))


def manifest_fields(read: dict, name: str) -> dict:
    """The row fields a manifest read gives the plugin class `name`; KeyError when the package has no such class."""
    plugins = (read.get("manifest") or {}).get("plugins") or {}
    return {"manifest": plugins[name], "wheel_sha256": read.get("wheel_sha256"),
            "dependency_lock": read.get("dependency_lock") or "", "code_identity": read.get("code")}


async def read_manifest(package_name: str, version: str, wheel_sha256: str | None) -> dict:
    """fetch_manifest, from async code; a read that fails is a 503 with its reason."""
    try:
        return await sync_to_async(fetch_manifest, thread_sensitive=False)(package_name, version, wheel_sha256)
    except Exception as exc:  # noqa: BLE001 - the person is told why the plugin cannot be shown
        logger.warning("plugin %s %s: its manifest could not be read: %s", package_name, version, _reason(exc))
        raise HttpError(503, NOT_READ.format(reason=_reason(exc))) from None


async def declared(row: Plugin) -> Declared:
    """The row's plugin as its manifest declares it. A row without one (installed before manifests), or a local
    plugin whose folder changed, has it read by the worker first, and stored."""
    if row.manifest is None or await sync_to_async(_local_code_changed)(row):
        read = await read_manifest(row.package_name, row.version, row.wheel_sha256)
        try:
            fields = manifest_fields(read, row.name)
        except KeyError:
            raise HttpError(503, NOT_READ.format(
                reason=f"{row.package_name} {row.version} has no plugin class {row.name}")) from None
        await Plugin.objects.filter(pk=row.pk).aupdate(**fields)
        for name, value in fields.items():
            setattr(row, name, value)
    return Declared(row)


def stored(row: Plugin) -> Declared | None:
    """The row's plugin as its stored manifest declares it, None when it has none yet (sync code cannot ask the
    worker)."""
    return Declared(row) if row.manifest is not None else None
