"""The form's VAIR lists, for reading a document's terms.

The form offers VAIR wherever VAIR has a vocabulary for a field. A document names a term by its VAIR
id ("DataPoisoning") or its label ("Data Poisoning"); either is matched whatever the case and spacing,
and anything else is left for the person to choose. The lists are src/data/vair_vocab.json, generated
from vair.ttl by services/ontology, so the upload proposes exactly the terms the form accepts.

The file is looked for where fields.py looks for its mapping: $PREFILL_VAIR_VOCAB_PATH, beside the
package (where the image puts it), then the repo layout.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from functools import lru_cache

_FILE = "vair_vocab.json"


def candidate_paths(module_file: pathlib.Path | None = None) -> list[pathlib.Path]:
    here = (module_file or pathlib.Path(__file__)).resolve()
    places = []
    configured = os.environ.get("PREFILL_VAIR_VOCAB_PATH", "").strip()
    if configured:
        places.append(pathlib.Path(configured))
    places.append(here.parent / _FILE)
    parents = here.parents
    if len(parents) > 3:
        places.append(parents[3] / "src" / "data" / _FILE)
    return places


@lru_cache(maxsize=1)
def _classes() -> dict[str, list[dict]]:
    for candidate in candidate_paths():
        if candidate.is_file():
            return json.loads(candidate.read_text())["classes"]
    raise FileNotFoundError(
        f"{_FILE} is not beside this package and no PREFILL_VAIR_VOCAB_PATH is set; "
        f"looked in {', '.join(str(p) for p in candidate_paths())}"
    )


def _fold(text: str) -> str:
    return re.sub(r"[\s_-]+", "", text).casefold()


def terms_of(cls: str) -> list[str]:
    """The ids VAIR defines under `cls`, in the form's order."""
    return [t["id"] for t in _classes()[cls]]


@lru_cache(maxsize=None)
def _names(cls: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for term in _classes()[cls]:
        names[_fold(term["id"])] = term["id"]
        names[_fold(term["label"])] = term["id"]
    return names


def match_term(cls: str, said: str) -> str:
    """The id of the `cls` term `said` names by id or label; "" when it names none."""
    return _names(cls).get(_fold(said or ""), "")
