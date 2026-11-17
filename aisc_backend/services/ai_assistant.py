"""
Which model a project's AI assistants talk to — one rule for all of them.

A project registers its own models as helpers (Manage > AI assistants, the
`Helper` rows ported with CON-01); a deployment may also set a stack-wide
fallback. The risk mapper and the card agent both resolve their model here, so
there is exactly one answer to "which model does an assistant use" and exactly
one way a missing model is reported (`NoModelConfigured`), which the endpoints
turn into a 503 that says what to configure. No assistant invents an answer
without a model.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

#: The helper roles an assistant prefers when a project has several models.
PREFERRED_ROLES = ("generator", "judge")


class NoModelConfigured(RuntimeError):
    """No model is configured for this assistant (project helper or stack setting)."""


class DestinationRefused(RuntimeError):
    """The model's endpoint is one the allowed-hosts door refuses for this project."""


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
    """The chat call an assistant uses: the project's helper first, the stack second.

    Raises `NoModelConfigured` when neither is set up, so the endpoint can say so
    instead of silently doing nothing.
    """
    from aisc_backend.models.connection import Helper
    from aisc_backend.services import allowed_hosts, connection_secrets

    helpers = [h async for h in Helper.objects.filter(project=project).order_by("name")]
    if helpers:
        preferred = next(
            (h for role in PREFERRED_ROLES for h in helpers if role in (h.roles or [])),
            helpers[0],
        )
        # A helper is the project's own endpoint, so it goes through the same door
        # as a connection test (CON-01) — read now, before the call.
        refusal = await allowed_hosts.guard_url(project, preferred.endpoint_url)
        if refusal is not None:
            raise DestinationRefused(
                f"the model {preferred.name!r} is at {refusal['detail']}"
            )
        key = await connection_secrets.get_secret(
            preferred.project_id, connection_secrets.helper_key_name(preferred.name)
        )
        return make_openai_chat(
            preferred.endpoint_url, preferred.label or "gpt-4o-mini", key
        )

    base_url = getattr(settings, base_url_setting, "") or ""
    if not base_url:
        raise NoModelConfigured(
            f"no model is configured for {purpose}: set up a helper model in "
            f"Manage > AI assistants, or the stack's {base_url_setting}"
        )
    return make_openai_chat(
        base_url,
        getattr(settings, model_setting, "") or "mistral",
        getattr(settings, api_key_setting, "") or None,
    )
