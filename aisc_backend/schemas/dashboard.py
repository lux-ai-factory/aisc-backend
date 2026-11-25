"""
Schemas for the shared project dashboard (PUT /projects/{pid}/dashboard).

They mirror the webapp's GridWidget + RGL GridItemLayout types so the backend
stops accepting arbitrary widget/layout dictionaries. Validation is strict:
unknown fields, overlong values and out-of-bounds geometry are rejected as
field-level errors (422) — never silently truncated or discarded.
"""
from ninja import Schema, Field
from pydantic import field_validator, model_validator

#: the widget kinds and chart types the frontend supports
WIDGET_KINDS = ("plugin", "chart", "text")
CHART_TYPES = ("line", "bars", "scatter", "kde", "radar", "pie", "table")
#: the responsive breakpoint names, with the column count each fits in
BREAKPOINT_NAMES = ("lg", "md", "sm", "xs", "xxs")
BREAKPOINT_COLS = {"lg": 24, "md": 20, "sm": 12, "xs": 8, "xxs": 4}

MAX_WIDGETS = 200        # widgets per dashboard / layout entries per breakpoint
MAX_COLLECTION = 200     # nested filters / metrics / grouping / legend entries
KEY_LIMIT = 256          # widget keys + identifiers (plugin, dimensions, eval pids...)
TITLE_LIMIT = 200
TEXT_LIMIT = 2000
LEGEND_LIMIT = 2048      # legend labels + overrides


def _check_list(value, label, count_limit=MAX_COLLECTION, item_limit=KEY_LIMIT):
    if len(value) > count_limit:
        raise ValueError(f"{label} has more than {count_limit} entries")
    for item in value:
        if len(str(item)) > item_limit:
            raise ValueError(f"{label} entries are limited to {item_limit} characters")
    return value


class DashboardWidgetIn(Schema):
    """One dashboard widget, exactly the fields the frontend persists."""

    model_config = {"extra": "forbid"}

    key: str = ""
    kind: str = ""
    plugin: str = ""
    evaluations: list[str] = []
    title: str = ""
    comment: str = ""
    chartType: str = ""
    metrics: list[str] = []
    #: filter values follow the plugin contract: dimension keys are strings but
    #: the values a plugin declaration carries may be numbers or booleans
    filters: dict[str, list[str | int | float | bool]] = {}
    groupBy: list[str] = []
    labelDim: str = ""
    facetDim: str = ""
    labelOverrides: dict[str, str] = {}
    labelOrder: list[str] = []
    text: str = ""

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value not in WIDGET_KINDS:
            raise ValueError(f"kind must be one of {', '.join(WIDGET_KINDS)}")
        return value

    @field_validator("chartType")
    @classmethod
    def _chart_type(cls, value: str) -> str:
        if value and value not in CHART_TYPES:
            raise ValueError(f"chartType must be one of {', '.join(CHART_TYPES)}")
        return value

    @model_validator(mode="after")
    def _bounds(self):
        for field in ("key", "plugin", "labelDim", "facetDim"):
            value = str(getattr(self, field) or "")
            if len(value) > KEY_LIMIT:
                raise ValueError(f"{field} is limited to {KEY_LIMIT} characters")
        if len(self.title) > TITLE_LIMIT:
            raise ValueError(f"title is limited to {TITLE_LIMIT} characters")
        for field in ("comment", "text"):
            value = getattr(self, field)
            if len(value) > TEXT_LIMIT:
                raise ValueError(f"{field} is limited to {TEXT_LIMIT} characters")
        _check_list(self.evaluations, "evaluations")
        _check_list(self.metrics, "metrics")
        _check_list(self.groupBy, "groupBy")
        _check_list(self.labelOrder, "labelOrder", item_limit=LEGEND_LIMIT)
        if len(self.filters) > MAX_COLLECTION:
            raise ValueError(f"filters has more than {MAX_COLLECTION} entries")
        for key, values in self.filters.items():
            if len(key) > KEY_LIMIT:
                raise ValueError("filter keys are limited to %d characters" % KEY_LIMIT)
            _check_list(values, f"filter {key!r} values")
        if len(self.labelOverrides) > MAX_COLLECTION:
            raise ValueError(f"labelOverrides has more than {MAX_COLLECTION} entries")
        for key, value in self.labelOverrides.items():
            if len(key) > LEGEND_LIMIT or len(value) > LEGEND_LIMIT:
                raise ValueError(
                    "labelOverride entries are limited to %d characters" % LEGEND_LIMIT)
        return self


class DashboardLayoutItemIn(Schema):
    """One RGL layout item, matching the frontend's GridItemLayout (plus the
    flags RGL persists). Geometry is validated: non-negative positions and
    positive dimensions; the horizontal bound is checked per breakpoint in
    DashboardIn."""

    model_config = {"extra": "forbid"}

    i: str = ""
    x: int = Field(0, ge=0)
    y: int = Field(0, ge=0)
    w: int = Field(1, gt=0)
    h: int = Field(1, gt=0)
    minW: int | None = Field(None, gt=0)
    minH: int | None = Field(None, gt=0)
    maxW: int | None = Field(None, gt=0)
    maxH: int | None = Field(None, gt=0)
    static: bool | None = None
    isDraggable: bool | None = None
    isResizable: bool | None = None
    isBounded: bool | None = None
    moved: bool | None = None

    @field_validator("i")
    @classmethod
    def _key(cls, value: str) -> str:
        if len(value) > KEY_LIMIT:
            raise ValueError(f"layout keys are limited to {KEY_LIMIT} characters")
        return value


class DashboardIn(Schema):
    """The full dashboard save: widgets + per-breakpoint layouts + revision."""

    model_config = {"extra": "forbid"}

    revision: int = Field(0, ge=0)
    widgets: list[DashboardWidgetIn] = []
    layouts: dict[str, list[DashboardLayoutItemIn]] = {}

    @model_validator(mode="after")
    def _dashboard(self):
        if len(self.widgets) > MAX_WIDGETS:
            raise ValueError(f"a dashboard is limited to {MAX_WIDGETS} widgets")
        keys = [w.key for w in self.widgets]
        if len(set(keys)) != len(keys):
            raise ValueError("widget keys must be unique")
        allowed = frozenset(BREAKPOINT_NAMES)
        for name, items in self.layouts.items():
            if name not in allowed:
                raise ValueError(
                    f"layout breakpoint must be one of {', '.join(BREAKPOINT_NAMES)}"
                )
            if len(items) > MAX_WIDGETS:
                raise ValueError(
                    f"layout {name!r} has more than {MAX_WIDGETS} entries")
            item_keys = [item.i for item in items]
            if len(set(item_keys)) != len(item_keys):
                raise ValueError(f"layout {name!r} has duplicate entries")
            missing = [key for key in item_keys if key not in set(keys)]
            if missing:
                shown = ", ".join(sorted(set(missing))[:3])
                raise ValueError(
                    f"layout {name!r} refers to unknown widget(s): {shown}"
                )
            cols = BREAKPOINT_COLS[name]
            for item in items:
                if item.x + item.w > cols:
                    raise ValueError(
                        f"layout {name!r} widget {item.i!r} exceeds the {cols} columns"
                    )
        return self
