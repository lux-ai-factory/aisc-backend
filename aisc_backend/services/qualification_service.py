"""
Client for the headless qualification service.

The qualification "app" is now a stateless compute service: POST the form
answers, get back the AI card (a knowledge graph built from the answers with
the AIRO/VAIR ontology) plus its JSON-LD/Turtle serialisations. It stores
nothing; the backend stores what comes back.

This module is the one place the backend talks to that service, so a test can
stub it (contract-first) without a running service. When the service is not
configured, the client falls back to a deterministic summary card so the
backend works end-to-end in dev and in tests.
"""
from __future__ import annotations

import json
import logging

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is optional at import time
    httpx = None

from config.settings import QUALIFICATION_SERVICE_URL, SERVICE_TOKEN_FOR_APPS

logger = logging.getLogger(__name__)

#: The builtin default questionnaire. User questionnaires live in the backend
#: (versioned there); the default lives on the headless service, mirroring the
#: reference app where the Annex IV default ships with the app itself.
DEFAULT_QUESTIONNAIRE_ID = "annex-iv-default"


class QualificationServiceError(RuntimeError):
    """The service answered something other than a built card."""


def _service_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if SERVICE_TOKEN_FOR_APPS:
        headers["X-AISC-Service-Token"] = SERVICE_TOKEN_FOR_APPS
    return headers


def _summary_card(answers: dict) -> dict:
    """A deterministic card when the service is absent (dev/tests only)."""
    return {
        "format": "airo-min",
        "provider": "backend-fallback",
        "nodes": 0,
        "triples": 0,
        "card": {
            "summary": {
                "label": "qualification summary (no service configured)",
            },
        },
    }


async def build_card(
    project_pid: str,
    questionnaire_id: str,
    questionnaire: dict,
    answers: dict,
    system: dict | None = None,
) -> dict:
    """Ask the headless service to build the AI card for these answers.

    Each call is self-contained: the backend owns the questionnaire, so it
    sends BOTH the selected questionnaire's definition and the answers; the
    stateless service builds a card from them (no catalogue of its own).
    `system` (name/version/company) lets the service name the card; the
    headless service forwards it to the ontology builder when configured.
    Returns the card payload the service produced. Raises when the service is
    configured but refused to answer.
    """
    if not QUALIFICATION_SERVICE_URL:
        return _summary_card(answers)
    if httpx is None:
        raise QualificationServiceError("httpx is not installed")
    payload = {
        "project_pid": str(project_pid),
        "questionnaire_id": questionnaire_id,
        "questionnaire": questionnaire or {},
        "answers": answers or {},
        "system": system or {},
    }
    try:
        resp = await httpx.AsyncClient(timeout=120).post(
            f"{QUALIFICATION_SERVICE_URL}/api/build",
            json=payload,
            headers=_service_headers(),
        )
    except Exception as exc:  # noqa: BLE001 - the caller decides how to degrade
        logger.warning("qualification service unreachable: %s", exc)
        return _summary_card(answers)
    if resp.status_code != 200:
        raise QualificationServiceError(
            f"qualification service answered {resp.status_code}"
        )
    return resp.json()


async def get_default_questionnaire() -> dict:
    """Fetch the builtin default questionnaire definition from the service.

    Returns {} when no service is configured or reachable (the webapp then
    shows no default); raises when a configured service refuses to answer.
    """
    if not QUALIFICATION_SERVICE_URL:
        return {}
    if httpx is None:
        raise QualificationServiceError("httpx is not installed")
    try:
        resp = await httpx.AsyncClient(timeout=30).get(
            f"{QUALIFICATION_SERVICE_URL}/api/default-questionnaire",
            headers=_service_headers(),
        )
    except Exception as exc:  # noqa: BLE001 - the caller decides how to degrade
        logger.warning("qualification service unreachable: %s", exc)
        return {}
    if resp.status_code != 200:
        raise QualificationServiceError(
            f"qualification service answered {resp.status_code}"
        )
    data = resp.json()
    return data if isinstance(data, dict) else {}


async def get_vocabularies() -> dict:
    """The VAIR term lists for the qualification form, per AIRO class.

    {} when no service is configured or reachable (the form degrades to free
    text); raises when a configured service refuses to answer.
    """
    if not QUALIFICATION_SERVICE_URL:
        return {}
    if httpx is None:
        raise QualificationServiceError("httpx is not installed")
    try:
        resp = await httpx.AsyncClient(timeout=30).get(
            f"{QUALIFICATION_SERVICE_URL}/api/vocabularies",
            headers=_service_headers(),
        )
    except Exception as exc:  # noqa: BLE001 - the caller decides how to degrade
        logger.warning("qualification service unreachable: %s", exc)
        return {}
    if resp.status_code != 200:
        raise QualificationServiceError(
            f"qualification service answered {resp.status_code}"
        )
    data = resp.json()
    return data if isinstance(data, dict) else {}


def card_jsonld(card: dict | None) -> str | None:
    """The JSON-LD serialisation of a built card, if the service produced one."""
    if not card:
        return None
    for key in ("jsonld", "ontology_jsonld"):
        value = card.get(key)
        if isinstance(value, str):
            return value
    try:
        return json.dumps(card.get("graph") or card)
    except TypeError:
        return None
