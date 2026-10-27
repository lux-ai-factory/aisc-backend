"""`manage.py move_helper_models`: the `llm` components older runs used as helpers become helper models
of the platform (configurator only; start runs it after its other upgrade steps).

Before helper models lived on the platform, a judge (an attacker, a grader...) was an `llm` component
of the engine's AI system, its key an engine project secret. For each project, every `llm` component
an evaluation input named `helper:<role>` points at is handed to the platform's create route with its
decrypted key and the roles it was used for, named after the component (`-2`, `-3`... when the name is
taken), then marked `hidden_as_helper` with the reference it moved to. The row is never deleted: the
runs that used it keep pointing at it and read as before. Every other `llm` component is left as it is
and named in the output, for the user to decide. A component already moved is skipped, so a second run
moves nothing.

Prints `moved <n>` and `left <n>: <project>/<component>, ...`; a component the platform refused is a
`not moved` line and makes the exit code 1. The platform not answering stops the command at once with
exit code 1, before anything else is changed. Output never holds a key. The standalone engine has no
platform: it prints "standalone engine: nothing to move" and changes nothing.
"""
from __future__ import annotations

import re
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections

from aisc_backend import deployment, platform_helpers, projectdb
from aisc_backend.management.commands.deliver_ledger_events import Command as DeliverCommand
from aisc_backend.models import AIComponent, AIComponentType, EvaluationInput, ProjectConfig, ProjectConfigCategory
from aisc_backend.utils.encryption import decrypt_value

HELPER_PREFIX = "helper:"
#: The platform's helper names: lowercase letters, digits and dashes, up to 63.
NAME_LENGTH = 63
NOT_IN_A_NAME = re.compile(r"[^a-z0-9]+")


def helper_name(component_name: str, attempt: int) -> str:
    """The component's name as a helper name; from the second attempt on, with `-<attempt>`."""
    suffix = f"-{attempt}" if attempt > 1 else ""
    slug = NOT_IN_A_NAME.sub("-", component_name.lower()).strip("-") or "helper"
    return slug[:NAME_LENGTH - len(suffix)].rstrip("-") + suffix


class Command(BaseCommand):
    help = "Move the llm components older runs used as helpers to the platform's helper models, and hide them."

    def handle(self, *args, **options):
        if not deployment.is_configurator():
            self.stdout.write("standalone engine: nothing to move")
            return
        moved, left, refused = 0, [], []
        for alias in DeliverCommand._databases():
            try:
                if alias != DEFAULT_DB_ALIAS:
                    projectdb.open_alias(alias)
                token = projectdb.admitted.set(alias)
                try:
                    n, project_left, project_refused = self._move(alias)
                finally:
                    projectdb.admitted.reset(token)
            except (projectdb.NoSuchProjectDatabase, projectdb.SchemaNotProvisioned):
                continue
            except platform_helpers.HelpersUnavailable as exc:
                self.stdout.write(f"moved {moved}")
                self.stderr.write(exc.message)
                sys.exit(1)
            finally:
                if alias != DEFAULT_DB_ALIAS and alias in connections.settings:
                    connections[alias].close()
            moved += n
            left += project_left
            refused += project_refused
        self.stdout.write(f"moved {moved}")
        self.stdout.write(f"left {len(left)}" + (": " + ", ".join(left) if left else ""))
        for line in refused:
            self.stdout.write(line)
        if refused:
            raise CommandError(f"{len(refused)} helper component(s) not moved")

    def _move(self, alias: str) -> tuple[int, list[str], list[str]]:
        roles_of: dict[int, list[str]] = {}
        for component_id, name in (EvaluationInput.objects
                                   .filter(name__startswith=HELPER_PREFIX,
                                           component__component_type=AIComponentType.LLM)
                                   .values_list("component_id", "name").order_by("pk")):
            roles = roles_of.setdefault(component_id, [])
            role = name[len(HELPER_PREFIX):]
            if role not in roles:
                roles.append(role)
        moved, left, refused = 0, [], []
        components = (AIComponent.objects.filter(component_type=AIComponentType.LLM, hidden_as_helper=None)
                      .select_related("system__project").order_by("pk"))
        for component in components:
            project = component.system.project
            where = f"{project.name}/{component.name}"
            if component.pk not in roles_of:
                left.append(where)
                continue
            platform_pid = project.platform_project_id or (
                projectdb.pid_of(alias) if alias != DEFAULT_DB_ALIAS else None)
            if platform_pid is None:
                refused.append(f"not moved {where}: the project is not a platform project")
                continue
            try:
                api_key = self._key(project, component)
            except Exception as exc:  # noqa: BLE001 - reported by type only: the value is a secret
                refused.append(f"not moved {where}: its key cannot be read ({type(exc).__name__})")
                continue
            try:
                ref = self._create(str(platform_pid), component, roles_of[component.pk], api_key)
            except platform_helpers.HelperRefused as exc:
                refused.append(f"not moved {where}: {exc}")
                continue
            AIComponent.objects.filter(pk=component.pk).update(hidden_as_helper=ref)
            moved += 1
        return moved, left, refused

    @staticmethod
    def _key(project, component) -> str | None:
        secret_key = (component.json_value or {}).get("secret_key")
        if not secret_key:
            return None
        row = ProjectConfig.objects.filter(project=project, key=secret_key,
                                           category=ProjectConfigCategory.SECRETS).first()
        if row is None or not row.encrypted_value:
            return None
        return decrypt_value(row.encrypted_value)

    @staticmethod
    def _create(platform_pid: str, component, roles: list[str], api_key: str | None) -> str:
        """The reference of the helper made from `component`, under the first free name."""
        attempt = 1
        while True:
            name = helper_name(component.name, attempt)
            made = platform_helpers.create_helper(
                platform_pid, name=name, label=component.name,
                endpoint_url=(component.json_value or {}).get("endpoint_url") or "",
                roles=roles, api_key=api_key)
            if made is not None:
                return made.get("ref") or f"helper:{platform_pid}/{name}"
            attempt += 1
