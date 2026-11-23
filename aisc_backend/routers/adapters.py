"""The adapter and preset catalogue (ADR 0009).

The webapp renders the simple preset forms and the "Custom" adapter-plus-config
form from this. Manifests come from the plugin-manager, which discovers adapters
in plugin packages alongside the standard library.
"""
from ninja import Router

from aisc_plugins.routers.plugin import plugin_loader
from aisc_plugin_interface.adapters import ADAPTER_ABI_VERSION

router = Router(tags=["adapters"])


@router.get("/adapters", response=dict)
async def list_adapters(request):
    """Every known adapter, keyed by package and version, with its config schema."""
    return {
        "abi_version": ADAPTER_ABI_VERSION,
        "packages": plugin_loader.list_adapters(refresh=True),
    }


@router.get("/presets", response=list[dict])
async def list_presets(request):
    """The preset forms (llm, agent, model-api, dataset, model-file) and any a
    plugin package ships."""
    seen: dict[str, dict] = {}
    for versions in plugin_loader.list_adapters(refresh=True).values():
        for entry in versions.values():
            for preset in entry.get("presets", []):
                seen.setdefault(preset["id"], preset)
    return list(seen.values())
