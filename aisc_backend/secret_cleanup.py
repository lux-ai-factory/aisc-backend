"""Old plaintext secrets cleared from the engine's stores of one database, by `manage.py clean_old_secrets`.

Three stores held secrets before they were masked at save:

- the outbox (`mask_outbox`): configuration events, delivered or not, get their config masked as a ledger copy;
- the stored plugin configurations (`drop_config_secrets`): secret fields are dropped, so the plugin's default
  applies or the run asks for the field again; a key inside longer text (a prompt) is scrubbed in place and its
  field named to rotate;
- the failure text of plugin runs (`scrub_failures`): key shapes and assignments are redacted.

Each takes the model, the database alias and whether to write, and returns what it found (or changed) by name:
which plugin, which field. None of them logs or returns a value; each logs one WARNING line per field to rotate.
This rewrites stored data, so it is never run by a migration: only by the command, on the operator's word.
"""
from __future__ import annotations

import logging
from collections import Counter

from aisc_backend.config_secrets import masked_for_ledger, plaintext_secret_paths, scrub_text, stored_secret_changes

CONFIGURED = "engine.plugin.configured"

logger = logging.getLogger("aisc_backend.secret_cleanup")


def packages_by_pid(model, using: str, pids: set[str]) -> dict[str, str]:
    """The package name of each plugin pid among `pids`, from the Plugin model next to `model`."""
    if not pids:
        return {}
    plugin_model = model._meta.apps.get_model("aisc_backend", "Plugin")
    found = {}
    for pid, package in plugin_model.objects.using(using).values_list("pid", "package_name"):
        if str(pid) in pids:
            found[str(pid)] = package
    return found


def mask_outbox(outbox_model, using: str, apply: bool) -> tuple[int, list[tuple[str, str]]]:
    """(rows to mask or masked, (item_id, field) per plaintext secret they held). Only the event's
    `content["config"]` changes, and only on configuration events."""
    rows = 0
    rotate: list[tuple[str, str]] = []
    for row in outbox_model.objects.using(using).order_by("id").iterator():
        event = row.event
        if not isinstance(event, dict) or event.get("action") != CONFIGURED:
            continue
        content = event.get("content")
        if not isinstance(content, dict) or "config" not in content:
            continue
        config = content["config"]
        masked = masked_for_ledger(config)
        if masked == config:
            continue
        rows += 1
        item_id = str(event.get("item_id"))
        rotate.extend((item_id, path) for path in plaintext_secret_paths(config))
        if apply:
            row.event = {**event, "content": {**content, "config": masked}}
            row.save(update_fields=["event"])
    packages = packages_by_pid(outbox_model, using, {item_id for item_id, _ in rotate})
    for item_id, path in rotate:
        logger.warning("rotate plugin=%s field=%s", packages.get(item_id, f"unknown:{item_id}"), path)
    logger.info("outbox: %d configuration event(s) %s", rows, "masked" if apply else "to mask")
    return rows, rotate


def drop_config_secrets(config_model, using: str, apply: bool,
                        scrubbed: list | None = None) -> tuple[int, list[tuple[str, str]]]:
    """(configurations to change or changed, (package, field) per dropped secret). When `scrubbed` is a list, it
    gets (package, field) for each kept field a key was scrubbed out of: not dropped, but its key must be rotated."""
    rows = 0
    rotate: list[tuple[str, str]] = []
    cleared: list[tuple[str, str]] = []
    for row in config_model.objects.using(using).select_related("plugin").order_by("id").iterator():
        new, dropped, scrubbed_paths = stored_secret_changes(row.config)
        if new == row.config:
            continue
        rows += 1
        rotate.extend((row.plugin.package_name, path) for path in dropped)
        cleared.extend((row.plugin.package_name, path) for path in scrubbed_paths)
        if apply:
            row.config = new
            row.save(update_fields=["config"])
    for package, path in rotate + cleared:
        logger.warning("rotate plugin=%s field=%s", package, path)
    if scrubbed is not None:
        scrubbed.extend(cleared)
    logger.info("plugin configurations: %d %s, %d field(s) %s", rows, "changed" if apply else "to change",
                len(rotate), "dropped" if apply else "to drop")
    return rows, rotate


def scrub_failures(plugin_run_model, using: str, apply: bool) -> tuple[int, list[str]]:
    """(failure texts to scrub or scrubbed, the plugin package of each)."""
    rows = 0
    packages: list[str] = []
    queryset = (plugin_run_model.objects.using(using).exclude(error_message__isnull=True).exclude(error_message="")
                .select_related("plugin_config__plugin").order_by("id"))
    for row in queryset.iterator():
        text = scrub_text(row.error_message)
        if text == row.error_message:
            continue
        rows += 1
        plugin = row.plugin_config.plugin if row.plugin_config_id else None
        packages.append(plugin.package_name if plugin else "unknown")
        if apply:
            row.error_message = text
            row.save(update_fields=["error_message"])
    for package, failures in sorted(Counter(packages).items()):
        logger.warning("rotate plugin=%s failures=%d", package, failures)
    logger.info("plugin failure texts: %d %s", rows, "scrubbed" if apply else "to scrub")
    return rows, packages
