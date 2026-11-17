"""
The risk mapper: "Suggest with AI" for the risk & control matrix (pilot parity).

The pilot (`docs/appendices/2-control-objectives.md` §3) has an AI assistant
propose, for each risk nobody linked by hand, the objectives of the profile that
mitigate it. Every proposal must **quote the risk's chain** (the risk, its
source, weakness, consequence, impact and controls); unknown or repeated
objectives and wrong quotes are sent back, for at most three attempts in all;
what still fails is dropped. Links made by hand are replaced only when the caller
ticks "Also replace objectives I linked by hand".

Everything here is pure: it decides what to link and what to drop and hands the
caller the rows to create. The model is reached through `resolve_proposer`, which
prefers the project's own helper model (Manage > AI assistants) and falls back to
the stack setting `CONTROL_OBJECTIVES_LLM_*`; with neither configured it raises
`NoModelConfigured`, and the router answers 503 rather than inventing links.
"""
from __future__ import annotations

import json
import logging
import re

from aisc_backend.services import ai_assistant

logger = logging.getLogger(__name__)

#: The pilot's cap: "for at most three attempts in all; what still fails is dropped".
MAX_ATTEMPTS = 3

#: The shared "no model configured" refusal (see `services.ai_assistant`).
NoModelConfigured = ai_assistant.NoModelConfigured


def normalise(text) -> str:
    """Case- and whitespace-insensitive form, for quote matching."""
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def risk_chain_text(risk) -> str:
    """The chain a proposal must quote: the risk and the AIRO text it came from."""
    parts = [getattr(risk, "name", "") or "", getattr(risk, "airo_text", "") or ""]
    return " ".join(part for part in parts if part).strip()


def validate(proposal, offered: set[str], chain: str, taken: set[str]) -> tuple[dict | None, str]:
    """Check one proposal; returns `(clean, "")` or `(None, why it is sent back)`."""
    if not isinstance(proposal, dict):
        return None, "malformed proposal"
    objective_id = str(proposal.get("objective_id") or "").strip()
    if not objective_id:
        return None, "no objective"
    if objective_id not in offered:
        return None, f"objective {objective_id} is not in the profile"
    if objective_id in taken:
        return None, f"objective {objective_id} is already linked to this risk"
    quote = str(proposal.get("quote") or "").strip()
    if not quote:
        return None, "no quote"
    if normalise(quote) not in normalise(chain):
        return None, "the quote is not in the risk chain"
    return {"objective_id": objective_id, "quote": quote}, ""


async def suggest(
    *,
    risks: list[dict],
    offered: list[dict],
    existing: dict[str, list[dict]],
    propose,
    also_replace_manual: bool = False,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """Decide the links one mapping run makes. Nothing here touches the database.

    `risks`: `[{"pid": str, "chain": str}, ...]` — the risks to consider.
    `offered`: `[{"id": str, "text": str}, ...]` — the profile's objectives.
    `existing`: risk pid -> `[{"objective_id": str, "source": "person"|"ai"}, ...]`.
    `propose`: `async (risk, offered) -> list[dict] | None`; `None` (or a raise)
        means the model failed that attempt.

    Returns `{"attempts": int, "created": [{"risk", "objective_id", "quote"}],
    "dropped": [{"risk", "objective_id", "reason"}]}`.
    """
    offered_ids = [o["id"] for o in offered]
    offered_set = set(offered_ids)

    todo = []
    for risk in risks:
        links = existing.get(risk["pid"], [])
        if any(link["source"] == "person" for link in links) and not also_replace_manual:
            continue
        todo.append(risk)

    #: objective ids a risk already carries and keeps (a hand link is kept
    #: unless the caller asked to replace hand links).
    kept = {
        risk["pid"]: {
            link["objective_id"]
            for link in existing.get(risk["pid"], [])
            if link["source"] == "person" and not also_replace_manual
        }
        for risk in todo
    }
    accepted: dict[str, list[dict]] = {risk["pid"]: [] for risk in todo}
    dropped: list[dict] = []
    resolved: set[str] = set()
    attempts = 0

    while attempts < max_attempts and len(resolved) < len(todo):
        attempts += 1
        for risk in todo:
            pid = risk["pid"]
            if pid in resolved:
                continue
            taken = set(kept[pid]) | {p["objective_id"] for p in accepted[pid]}
            try:
                proposals = await propose(risk, offered)
            except Exception as exc:  # noqa: BLE001 - a failed attempt is retried
                logger.warning("risk mapper attempt %s failed for %s: %s", attempts, pid, exc)
                continue
            if not proposals:
                continue
            for proposal in proposals:
                clean, reason = validate(proposal, offered_set, risk["chain"], taken)
                if clean is None:
                    dropped.append({
                        "risk": pid,
                        "objective_id": (proposal or {}).get("objective_id")
                        if isinstance(proposal, dict) else None,
                        "reason": reason,
                    })
                    continue
                taken.add(clean["objective_id"])
                accepted[pid].append(clean)
            if accepted[pid]:
                resolved.add(pid)

    created = [
        {"risk": pid, "objective_id": link["objective_id"], "quote": link["quote"]}
        for pid, links in accepted.items()
        for link in links
    ]
    for risk in todo:
        if risk["pid"] not in resolved:
            dropped.append({
                "risk": risk["pid"],
                "objective_id": None,
                "reason": f"no valid proposal within {attempts} attempt(s)",
            })
    return {"attempts": attempts, "created": created, "dropped": dropped}


def extract_json(reply) -> list[dict]:
    """The proposal list out of a model reply (a JSON array, fenced or bare)."""
    if isinstance(reply, list):
        return [item for item in reply if isinstance(item, dict)]
    if isinstance(reply, dict):
        for key in ("proposals", "mappings", "links", "results"):
            if isinstance(reply.get(key), list):
                return [item for item in reply[key] if isinstance(item, dict)]
        return [reply]
    text = str(reply or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    match = re.search(r"[\[{].*[\]}]", text, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    if isinstance(data, dict):
        for key in ("proposals", "mappings", "links", "results"):
            if isinstance(data.get(key), list):
                return [item for item in data[key] if isinstance(item, dict)]
        return [data]
    return [item for item in data if isinstance(item, dict)]


def build_prompt(risk: dict, offered: list[dict]) -> str:
    """The mapper's instruction: propose objectives, quoting the chain."""
    lines = [
        "You map a risk of an AI system to the control objectives that mitigate it.",
        "Answer with a JSON array of {\"objective_id\": ..., \"quote\": ...} and nothing else.",
        "Only use the objective ids offered. Quote, word for word, the part of the",
        "risk's chain that justifies each link.",
        "",
        f"RISK CHAIN: {risk['chain']}",
        "",
        "OBJECTIVES:",
    ]
    lines += [f"- {o['id']}: {o.get('text', '')}" for o in offered]
    return "\n".join(lines)


def make_openai_proposer(base_url: str, model: str, api_key: str | None):
    """A proposer that asks an OpenAI-compatible endpoint for proposals."""
    chat = ai_assistant.make_openai_chat(base_url, model, api_key)

    async def propose(risk, offered):
        reply = await chat([{"role": "user", "content": build_prompt(risk, offered)}])
        return extract_json(reply)

    return propose


async def resolve_proposer(project):
    """The mapper's model: the project's helper first, the stack setting second.

    Raises `NoModelConfigured` when neither is set up, so the endpoint can say so
    instead of silently doing nothing.
    """
    chat = await ai_assistant.resolve_chat(
        project,
        purpose="the risk mapper",
        base_url_setting="CONTROL_OBJECTIVES_LLM_BASE_URL",
        model_setting="CONTROL_OBJECTIVES_LLM_MODEL",
        api_key_setting="CONTROL_OBJECTIVES_LLM_API_KEY",
    )

    async def propose(risk, offered):
        reply = await chat([{"role": "user", "content": build_prompt(risk, offered)}])
        return extract_json(reply)

    return propose
