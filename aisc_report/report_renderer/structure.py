"""Chapters, appendix, numbering and the table of contents.

The layout stays a flat list. A chapter groups every following block up to the next chapter, the
appendix or the end; every block after the appendix belongs to the appendix part. `plan` sets on
each section its `number` ("" when none) and `depth` (1 inside a chapter), and builds the TOC.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Plan:
    #: TOC entries (sections, chapters with "children"), or None when there is no TOC
    toc_entries: list | None
    #: index of the section the TOC follows (the cover), or None for "first"
    toc_after: int | None


def letters(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA, 28 -> AB."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _kind(section) -> str:
    kind = section.get("kind")
    if kind:
        return kind
    return "cover" if section["type_id"] == "cover" else "block"


def plan(sections: list[dict], *, toc: str = "on", numbering: bool = False) -> Plan:
    top = 0          # top-level counter of the current part
    sub = 0          # counter inside the current chapter
    in_appendix = False
    chapter_label = None
    entries: list[dict] = []
    current_chapter: dict | None = None

    def label(n: int) -> str:
        return letters(n) if in_appendix else str(n)

    for s in sections:
        kind = _kind(s)
        s["number"] = ""
        s["depth"] = 0
        if kind == "cover":
            continue
        if kind == "appendix":
            in_appendix, top, chapter_label, current_chapter = True, 0, None, None
            entries.append({**s, "children": []})
            continue
        if kind == "chapter":
            top += 1
            sub = 0
            chapter_label = label(top)
            if numbering:
                s["number"] = chapter_label
            current_chapter = {**s, "children": []}
            entries.append(current_chapter)
            continue
        if current_chapter is not None:
            sub += 1
            s["depth"] = 1
            if numbering:
                s["number"] = f"{chapter_label}.{sub}"
            current_chapter["children"].append(dict(s))
        else:
            top += 1
            if numbering:
                s["number"] = label(top)
            entries.append({**s, "children": []})

    toc_entries = None if toc == "off" else entries or None
    toc_after = next((i for i, s in enumerate(sections) if s["type_id"] == "cover"), None)
    return Plan(toc_entries=toc_entries, toc_after=toc_after)
