"""A report's look, as a template chooses it: font, base font size, colours, logo.

Only fonts installed in the renderer image are offered (the Dockerfile installs them),
because WeasyPrint can only draw what the image has. The browser's preview uses the same
stack and falls back to the next family when the viewer does not have the first.
"""
from __future__ import annotations

import base64
import re

FONTS = [
    {"id": "inter", "label": "Inter", "stack": '"Inter", "Liberation Sans", Arial, sans-serif'},
    {"id": "liberation-sans", "label": "Liberation Sans (Arial)", "stack": '"Liberation Sans", Arial, sans-serif'},
    {"id": "liberation-serif", "label": "Liberation Serif (Times)",
     "stack": '"Liberation Serif", "Times New Roman", serif'},
    {"id": "roboto", "label": "Roboto", "stack": '"Roboto", "Liberation Sans", Arial, sans-serif'},
    {"id": "dejavu-sans", "label": "DejaVu Sans", "stack": '"DejaVu Sans", Verdana, sans-serif'},
    {"id": "dejavu-serif", "label": "DejaVu Serif", "stack": '"DejaVu Serif", Georgia, serif'},
]
_STACKS = {f["id"]: f["stack"] for f in FONTS}

LOGO_MIMES = ["image/png", "image/jpeg", "image/svg+xml"]
#: a logo is at most 1 MB once decoded; base64 adds a third
LOGO_MAX_BASE64 = 1_400_000
HEX = "^#[0-9a-fA-F]{6}$"
#: the marking's printed label (cover and page header); "none" prints nothing
MARKING_LABELS = {"public": "Public", "internal": "Internal", "confidential": "Confidential",
                  "strictly_confidential": "Strictly confidential"}

SCHEMA = {
    "type": "object",
    "required": ["font", "font_size_pt", "primary_color", "accent_color"],
    "properties": {
        "font": {"enum": list(_STACKS)},
        "font_size_pt": {"type": "number", "minimum": 8, "maximum": 16},
        "primary_color": {"type": "string", "pattern": HEX},
        "accent_color": {"type": "string", "pattern": HEX},
        # optional: absent means the default header and footer
        "header_text": {"type": ["string", "null"], "maxLength": 120},
        "footer_text": {"type": ["string", "null"], "maxLength": 120},
        "marking": {"enum": ["none", "public", "internal", "confidential", "strictly_confidential"]},
        "show_document_id": {"type": "boolean"},
        "logo": {
            "type": ["object", "null"],
            "required": ["mime", "data_base64"],
            "properties": {
                "mime": {"enum": LOGO_MIMES},
                "data_base64": {"type": "string", "minLength": 1, "maxLength": LOGO_MAX_BASE64},
            },
        },
    },
}


def css_overrides(style: dict | None) -> str:
    """CSS that puts the template on top of the platform look; empty without a style.

    Every value was checked against SCHEMA, and is checked again here, so nothing but a
    colour, a known font stack and a number reaches the stylesheet."""
    if not style:
        return ""
    primary, accent = style["primary_color"], style["accent_color"]
    if not (re.fullmatch(HEX[1:-1], primary) and re.fullmatch(HEX[1:-1], accent)):
        raise ValueError("colours must be #rrggbb")
    stack = _STACKS[style["font"]]
    size = float(style["font_size_pt"])
    return (":root {"
            f" --l-aif-primary: {primary}; --l-aif-dominant-100: {primary};"
            f" --l-aif-accent: {accent}; --l-aif-bright-secondary-100: {accent};"
            " }\n"
            f"html, body {{ font-family: {stack}; font-size: {size:g}pt; }}\n"
            f"h1, h2, h3 {{ font-family: {stack}; color: {primary}; }}\n")


def logo_of(style: dict | None) -> tuple[str, bytes] | None:
    """The template's logo as (mime, bytes), or None."""
    logo = (style or {}).get("logo")
    if not logo:
        return None
    return logo["mime"], base64.b64decode(logo["data_base64"], validate=True)


#: The most pixels a logo may have: a 1 MB PNG can decode to hundreds of MB. An SVG logo is held to
#: the same cap at SVG_RASTER_SCALE times the size it declares, the size the Word export draws it at.
LOGO_MAX_PIXELS = 16_000_000
#: The Word export draws an SVG (a logo or a chart) as a PNG this many times its declared size,
#: but never with a side longer than SVG_RASTER_MAX_SIDE pixels.
SVG_RASTER_SCALE = 2
SVG_RASTER_MAX_SIDE = 2000
_RASTER = {"image/png": ("PNG", b"\x89PNG\r\n\x1a\n"), "image/jpeg": ("JPEG", b"\xff\xd8\xff")}


def logo_problem(style: dict | None) -> str | None:
    """Why the template's logo can't be used, or None. It must be base64 of what its mime says: Pillow,
    which WeasyPrint and the DOCX export use, reads PSD, EPS, JPEG 2000 and FITS whatever the declared
    type, so a raster logo is opened as its declared format only, and its size is capped."""
    import binascii
    import io

    logo = (style or {}).get("logo")
    if not isinstance(logo, dict) or not isinstance(logo.get("data_base64"), str):
        return None                                                  # the schema says what is missing
    try:
        data = base64.b64decode(logo["data_base64"], validate=True)
    except (binascii.Error, ValueError):
        return "is not base64"
    mime = logo.get("mime")
    if mime == "image/svg+xml":
        head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        if not (head.startswith(b"<") and b"<svg" in data[:65536].lower()):
            return "is not an SVG document"
        if svg_has_resource(data):
            return "has an embedded or linked resource (<image>, <feImage>, <foreignObject> or href)"
        size = svg_size(data)
        if size is None:
            return "has no usable width and height (nor viewBox)"
        if (SVG_RASTER_SCALE * size[0]) * (SVG_RASTER_SCALE * size[1]) > LOGO_MAX_PIXELS:
            return f"has more than {LOGO_MAX_PIXELS} pixels at {SVG_RASTER_SCALE} times the size it declares"
        return None
    if mime not in _RASTER:
        return None
    fmt, magic = _RASTER[mime]
    if not data.startswith(magic):
        return f"is not a {fmt} image"
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data), formats=[fmt]) as img:
            if img.width * img.height > LOGO_MAX_PIXELS:
                return f"has more than {LOGO_MAX_PIXELS} pixels"
            img.verify()
    except Exception:                                                # noqa: BLE001 (any unreadable image)
        return f"is not a readable {fmt} image"
    return None


#: Elements that draw a picture or document of their own inside an SVG; the size cap above reads only
#: the root's declared size, so a picture inside one is not held to it.
_SVG_RESOURCE_TAGS = {"image", "feimage", "foreignobject"}


def svg_has_resource(data: bytes) -> bool:
    """Whether an SVG document holds an <image>, <feImage> or <foreignObject> (in any namespace) or any
    href / xlink:href attribute. A logo needs none of them. A document that can't be parsed counts as
    having one. Parsing is defusedxml's (no entities, no DTD fetch), the parser cairosvg itself uses."""
    from defusedxml import ElementTree

    def local(name: str) -> str:
        return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()

    try:
        root = ElementTree.fromstring(data)
    except Exception:                                                # noqa: BLE001 (any unreadable SVG)
        return True
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        if local(node.tag) in _SVG_RESOURCE_TAGS or any(local(a) == "href" for a in node.attrib):
            return True
    return False


def svg_size(data: bytes) -> tuple[float, float] | None:
    """The size in px that cairosvg gives an SVG document (its width and height, else its viewBox),
    or None when the size is missing, not finite or not positive, or the document can't be parsed.
    Parsing is cairosvg's safe mode: no external resources, no entities."""
    import math
    from types import SimpleNamespace

    try:
        from cairosvg.helpers import node_format, size
        from cairosvg.parser import Tree

        tree = Tree(bytestring=data)
        # what cairosvg's Surface sets before it reads the root's size; a percentage then counts as 0
        surface = SimpleNamespace(dpi=96, context_width=None, context_height=None, font_size=0)
        surface.font_size = size(surface, "12pt")
        width, height, _ = node_format(surface, tree)
    except Exception:                                                # noqa: BLE001 (any unreadable SVG)
        return None
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (width, height)):
        return None
    return float(width), float(height)


def svg_raster_scale(data: bytes) -> float:
    """The scale the Word export draws an SVG at: SVG_RASTER_SCALE, lowered so that the longer side
    is at most SVG_RASTER_MAX_SIDE pixels. ValueError when the SVG has no usable size."""
    size = svg_size(data)
    if size is None:
        raise ValueError("the SVG has no usable size")
    return min(SVG_RASTER_SCALE, SVG_RASTER_MAX_SIDE / max(size))
