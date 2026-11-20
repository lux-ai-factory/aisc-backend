"""A plot saved from the results dashboard, prepared for the PDF engine without changing what it shows.

ECharts writes each label with a halo (a white stroke under the text: paint-order="stroke"), which the PDF engine
paints over the text and so hides it: the halo becomes a text of its own, drawn first. And text is measured where the
plot was saved, in the browser's font: the report sets it in Inter, the family the dashboard shows, and leaves room
at both sides so a wider glyph is never cut. Pure."""
from __future__ import annotations

import re

MARGIN = 0.05                     # of the width, at each side
_TEXT = re.compile(r"<text\b([^>]*)>(.*?)</text>", re.S)
_ATTR = re.compile(r'\s([\w:-]+)="([^"]*)"')


def _halo(match: re.Match) -> str:
    attrs, body = match.group(1), match.group(2)
    if 'paint-order="stroke"' not in attrs:
        return match.group(0)
    halo = re.sub(r'\sfill="[^"]*"', "", attrs).replace(' paint-order="stroke"', "") + ' fill="none"'
    text = re.sub(r'\s(stroke|stroke-width|stroke-miterlimit|paint-order)="[^"]*"', "", attrs)
    return f"<text{halo}>{body}</text><text{text}>{body}</text>"


def _number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _room(svg: str) -> str:
    head = re.match(r"<svg\b[^>]*>", svg)
    if not head:
        return svg
    attrs = dict(_ATTR.findall(head.group(0)))
    width, height = _number(attrs.get("width", "")), _number(attrs.get("height", ""))
    box = [_number(v) for v in attrs.get("viewBox", "").split()] if "viewBox" in attrs else None
    if box is None or len(box) != 4 or None in box:
        if width is None or height is None:
            return svg
        box = [0.0, 0.0, width, height]
    if box[2] <= 0:
        return svg
    pad = round(box[2] * MARGIN, 2)
    view = f"{box[0] - pad:g} {box[1]:g} {box[2] + 2 * pad:g} {box[3]:g}"
    new = re.sub(r'\sviewBox="[^"]*"', "", head.group(0))
    if width is not None:
        new = re.sub(r'\swidth="[^"]*"', f' width="{width + 2 * pad * (width / box[2]):g}"', new)
    new = new[:-1] + f' viewBox="{view}">'
    return new + svg[head.end():]


def for_print(svg: str) -> str:
    svg = _TEXT.sub(_halo, svg)
    svg = svg.replace("font-family:sans-serif", "font-family:Inter, sans-serif")
    return _room(svg)
