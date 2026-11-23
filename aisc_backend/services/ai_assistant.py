"""
Which model a project's AI assistants talk to — one rule for all of them.

A project registers its own model as a project-level endpoint component (ADR 0009
D-8); a deployment may also set a stack-wide fallback. The risk mapper and the card
agent both resolve their model here, so there is exactly one answer to "which model
does an assistant use" and exactly one way a missing model is reported
(`NoModelConfigured`), which the endpoints turn into a 503 that says what to
configure. No assistant invents an answer without a model.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class NoModelConfigured(RuntimeError):
    """No model is configured for this assistant (project helper or stack setting)."""


class DestinationRefused(RuntimeError):
    """The model's endpoint is one the allowed-hosts door refuses for this project."""


#: The project config key naming the component an assistant should use. A
#: ``variables`` entry whose value is the component's pid (ADR 0009 D-8).
ASSISTANT_SETTING_KEY = "ai_assistant"


async def _chosen_component(project):
    """The component the project named as its assistant model, if any.

    A project names one by setting the ``ai_assistant`` project config to its pid;
    it must be a project-level ``openai-chat`` endpoint (a judge or the assistant's
    own model), so a stray value never points a run at the assessed system.
    """
    from aisc_backend.models.ai_system import AIComponent, AIComponentType
    from aisc_backend.models.project_config import ProjectConfig, ProjectConfigCategory

    row = await ProjectConfig.objects.filter(
        project=project,
        category=ProjectConfigCategory.VARIABLES,
        key=ASSISTANT_SETTING_KEY,
    ).afirst()
    pid = (row.json_value or {}).get("value") if row is not None else None
    if not pid:
        return None
    return await AIComponent.objects.filter(
        pid=pid,
        project=project,
        system__isnull=True,
        component_type=AIComponentType.ENDPOINT,
        protocol="openai-chat",
    ).afirst()


def make_openai_chat(base_url: str, model: str, api_key: str | None):
    """A chat call against an OpenAI-compatible endpoint, returning the reply text."""

    async def chat(messages: list[dict], temperature: float = 0) -> str:
        import httpx

        payload = {"model": model, "messages": messages, "temperature": temperature}
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers
            )
        if resp.status_code != 200:
            raise RuntimeError(f"the model answered {resp.status_code}")
        choices = (resp.json() or {}).get("choices") or []
        if not choices:
            return ""
        return ((choices[0] or {}).get("message") or {}).get("content") or ""

    return chat


async def resolve_chat(
    project,
    *,
    purpose: str,
    base_url_setting: str,
    model_setting: str,
    api_key_setting: str,
):
    """The chat call an assistant uses: the project's own model first, the stack second.

    The project's model is a **project-level** ``openai-chat`` endpoint component
    (ADR 0009 D-8) — one not attached to the assessed system. Raises
    `NoModelConfigured` when neither is set up, so the endpoint can say so instead
    of silently doing nothing.
    """
    from aisc_backend.models.ai_system import AIComponent, AIComponentType
    from aisc_backend.services import allowed_hosts, component_secrets

    component = await _chosen_component(project)
    if component is None:
        component = await (
            AIComponent.objects.filter(
                project=project,
                system__isnull=True,
                component_type=AIComponentType.ENDPOINT,
                protocol="openai-chat",
            )
            .order_by("name")
            .afirst()
        )
    if component is not None:
        # A project-level component is the project's own endpoint, so it goes
        # through the same door as a component test (CON-01) — read now.
        refusal = await allowed_hosts.guard_url(project, component.url)
        if refusal is not None:
            raise DestinationRefused(
                f"the model {component.name!r} is at {refusal['detail']}"
            )
        key_name = component.secret_key or component_secrets.component_key_name(component.pid)
        key = await component_secrets.get_secret(component.project_id, key_name)
        model = (component.adapter_config or {}).get("model") or "gpt-4o-mini"
        return make_openai_chat(component.url, model, key)

    base_url = getattr(settings, base_url_setting, "") or ""
    if not base_url:
        raise NoModelConfigured(
            f"no model is configured for {purpose}: add a project-level LLM "
            f"component, or the stack's {base_url_setting}"
        )
    return make_openai_chat(
        base_url,
        getattr(settings, model_setting, "") or "mistral",
        getattr(settings, api_key_setting, "") or None,
    )
