"""
Schemas for controls / checklists + the catalogue surface.
"""
import uuid

from ninja import Schema


# --- catalogue ------------------------------------------------------------------

class CataloguePluginOut(Schema):
    package_name: str
    # The BaseEvaluationPlugin class inside the package (empty = package-level fallback).
    plugin_name: str = ""
    # The plugin's user-facing display name (empty/falls back to plugin/package name).
    display_name: str = ""
    # The plugin's own material-icon name (empty = the webapp shows a default).
    display_icon: str = ""
    version: str
    source: str
    tags: list[str] = []
    #: The catalogue's trustworthiness-dimension slugs the package's test item
    #: carries (a sub-dimension folded into its parent). The loader carries no
    #: dimension of its own, so this is read from the hosted catalogue's test
    #: tags; empty for a package the catalogue does not list. What "for this
    #: project" narrows a plugin by, exactly as it narrows a checklist.
    dimensions: list[str] = []


class CatalogueControlOut(Schema):
    catalogue_id: str          # the reference's per-checklist slug
    title: str
    source: str
    control_topic: str
    country_ids: list[str] = []
    regulation_ids: list[str] = []
    question_count: int = 0
    tags: list[str] = []
    #: The catalogue's trustworthiness-dimension slugs this item is tagged with
    #: (a sub-dimension folded into its parent). What "for this project" keeps.
    dimensions: list[str] = []
    #: The item's own sub-dimensions: the tree's children under its dimensions.
    subdimensions: list[str] = []


class CatalogueDimensionOut(Schema):
    """One trustworthiness dimension: the objective requirement it comes from."""

    requirement: str            # REQ1 .. REQ11
    slug: str                   # the catalogue's own slug for it
    label: str                  # the catalogue's own label for it


class CatalogueSubdimensionOut(Schema):
    """One child of a dimension in the catalogue's own tree (its sidebar)."""

    slug: str
    label: str
    parent: str = ""            # the dimension slug it hangs under


class CatalogueOut(Schema):
    plugins: list[CataloguePluginOut]
    controls: list[CatalogueControlOut]
    #: The dimension vocabulary, REQ order, so a UI can label the slugs it filters
    dimensions: list[CatalogueDimensionOut] = []
    #: The catalogue's own sub-dimension tree: the children of the dimensions,
    #: read from the catalogue, so the chips can nest them (the pilot's sidebar).
    subdimensions: list[CatalogueSubdimensionOut] = []
    #: The catalogue's own published site (independently published, I9): the
    #: product links to it, never fetches or stores its pages.
    app_catalog_url: str = ""


# --- installed library -----------------------------------------------------------

class ControlQuestionOut(Schema):
    order: int
    text: str
    article: str
    category: str


class ProjectChecklistOut(Schema):
    pid: uuid.UUID
    template: CatalogueControlOut = None
    question_count: int = 0
    #: latest submission summary (0 / "" / None when none exists yet)
    submission_count: int = 0
    latest_version: int = 0
    latest_status: str = ""
    latest_readiness: int | None = None


class ProjectChecklistDetailOut(ProjectChecklistOut):
    questions: list[ControlQuestionOut]


class ControlQuestionIn(Schema):
    text: str
    article: str = ""
    category: str = ""


class CustomChecklistIn(Schema):
    """A user's own checklist: a name plus its questions, created in the project."""

    name: str
    control_topic: str = ""
    questions: list[ControlQuestionIn] = []


# --- submissions -----------------------------------------------------------------

class SubmissionOut(Schema):
    pid: uuid.UUID
    checklist_pid: uuid.UUID
    label: str
    status: str
    version: int
    coverage: dict
    readiness: int | None = None


class SubmissionAnswerIn(Schema):
    question_order: int
    answer: str = ""
    score: int | None = None


class SubmissionAnswerOut(Schema):
    question_order: int
    answer: str
    score: int | None = None


class SubmissionDetailOut(SubmissionOut):
    answers: list[SubmissionAnswerOut]
