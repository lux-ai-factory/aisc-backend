"""
The card agent: talking to the AI card (pilot parity).

On a completed qualification the person can ask the card agent about the card
and ask it to correct it. The agent never writes: it **proposes** node changes
(`{node_id, property, value, quote}`), and a proposal only stands if it quotes
the person's own words or the card, so a correction cannot appear from nowhere.
The client applies what the person accepts through the existing node-correction
endpoint (`PUT .../qualification/{pid}/nodes/{node_id}`), which rebuilds the
card and stores it as a new version — so there stays exactly one write path.

The model comes from `services.ai_assistant`: a project helper, else the stack
setting `CARD_AGENT_LLM_*`. With neither, the endpoint answers 503 rather than
making something up.
"""
from __future__ import annotations

import json
import re

from aisc_backend.services import ai_assistant

#: The properties a correction may set (the node-correction endpoint's own set).
PROPERTIES = ("label", "type", "value")

PROPOSAL_KEYS = ("changes", "proposals", "corrections", "nodes")


def normalise(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def card_digest(card: dict) -> str:
    """The card as text for the prompt: its nodes and their values, one per line."""
    lines: list[str] = []
    nodes = (card or {}).get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if isinstance(node, dict):
                node_id = node.get("id") or node.get("node_id") or ""
                label = node.get("label") or node.get("name") or ""
                lines.append(f"- {node_id}: {label}")
    if not lines:
        summary = (card or {}).get("card")
        if summary:
            lines.append(json.dumps(summary, sort_keys=True)[:4000])
    return "\n".join(lines)


def build_prompt(card: dict, message: str, patch: dict | None = None) -> str:
    """The agent's instruction: answer, and propose only quoted corrections."""
    lines = [
        "You are the assistant for an AI Act qualification card. The person may ask",
        "about the card or ask you to correct it.",
        'Answer with a JSON object: {"text": <what you say>, "changes": [...]} and',
        'nothing else. Each change is {"node_id": ..., "property": "label"|"type"|',
        '"value", "value": ..., "quote": ...}. Quote, word for word, the part of the',
        "person's message or of the card that justifies the change. Propose no change",
        "you cannot quote; an empty list is a fine answer.",
        "",
        "CARD:",
        card_digest(card) or "(the card is empty)",
    ]
    if patch:
        lines += ["", "CORRECTIONS ALREADY RECORDED:", json.dumps(patch, sort_keys=True)]
    lines += ["", f"THE PERSON SAYS: {message}"]
    return "\n".join(lines)


def extract_reply(reply) -> tuple[str, list[dict]]:
    """The agent's answer text and its proposed changes, out of a model reply."""
    if isinstance(reply, dict):
        data = reply
        text = str(data.get("text") or data.get("answer") or "")
    else:
        raw = str(reply or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
        text = raw
        data = {}
        match = re.search(r"[\[{].*[\]}]", raw, re.S)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
                text = str(parsed.get("text") or "")
    for key in PROPOSAL_KEYS:
        changes = data.get(key)
        if isinstance(changes, list):
            return text, [c for c in changes if isinstance(c, dict)]
    return text, []


def validate(change: dict, evidence: str) -> tuple[dict | None, str]:
    """Check one proposed change; `(clean, "")` or `(None, why it is dropped)`."""
    if not isinstance(change, dict):
        return None, "malformed change"
    node_id = str(change.get("node_id") or change.get("node") or "").strip()
    if not node_id:
        return None, "no node"
    prop = str(change.get("property") or "").strip()
    if prop not in PROPERTIES:
        return None, f"property {prop or '(none)'} is not correctable"
    value = change.get("value")
    if value is None or str(value).strip() == "":
        return None, "no value"
    quote = str(change.get("quote") or "").strip()
    if not quote:
        return None, "no quote"
    if normalise(quote) not in evidence:
        return None, "the quote is neither the person's words nor in the card"
    return {
        "node_id": node_id,
        "property": prop,
        "value": str(value),
        "quote": quote,
        "reason": str(change.get("reason") or ""),
    }, ""


def review(*, card: dict, message: str, reply, patch: dict | None = None) -> dict:
    """One agent turn, decided (nothing here touches the database).

    Returns `{"text", "proposals", "dropped", "card"}`; `card` is the card with
    the proposals applied, a preview the caller may show but must not store —
    applying stores nothing either way, the node endpoint does that.
    """
    text, changes = extract_reply(reply)
    evidence = normalise(
        " ".join([message or "", card_digest(card), json.dumps(patch or {})])
    )
    proposals: list[dict] = []
    dropped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for change in changes:
        clean, reason = validate(change, evidence)
        if clean is None:
            dropped.append({
                "node_id": (change or {}).get("node_id") if isinstance(change, dict) else None,
                "reason": reason,
            })
            continue
        key = (clean["node_id"], clean["property"])
        if key in seen:
            dropped.append({"node_id": clean["node_id"], "reason": "proposed twice in one turn"})
            continue
        seen.add(key)
        proposals.append(clean)
    return {
        "text": text,
        "proposals": proposals,
        "dropped": dropped,
        "card": apply_preview(card, proposals),
    }


def apply_preview(card: dict, proposals: list[dict]) -> dict:
    """A copy of the card with the proposals applied in place (a preview only)."""
    preview = json.loads(json.dumps(card or {}))
    nodes = preview.get("nodes")
    if not isinstance(nodes, list):
        return preview
    by_id = {
        str(n.get("id") or n.get("node_id")): n
        for n in nodes
        if isinstance(n, dict)
    }
    for proposal in proposals:
        node = by_id.get(proposal["node_id"])
        if node is None:
            continue
        if proposal["property"] == "label":
            node["label"] = proposal["value"]
        elif proposal["property"] == "type":
            node["type"] = proposal["value"]
        else:
            node["value"] = proposal["value"]
    return preview


async def resolve_agent(project):
    """The card agent's model, from `services.ai_assistant`."""
    chat = await ai_assistant.resolve_chat(
        project,
        purpose="the card agent",
        base_url_setting="CARD_AGENT_LLM_BASE_URL",
        model_setting="CARD_AGENT_LLM_MODEL",
        api_key_setting="CARD_AGENT_LLM_API_KEY",
    )

    async def ask(card: dict, message: str, patch: dict | None = None):
        return await chat([
            {"role": "user", "content": build_prompt(card, message, patch)}
        ])

    return ask
