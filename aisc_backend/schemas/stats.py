import uuid
from datetime import datetime

from ninja import Schema


class EvaluationStatusBreakdown(Schema):
    status: str
    count: int


class MetricScoreSummary(Schema):
    metric_pid: uuid.UUID
    metric_name: str
    plugin_name: str
    avg_score: float
    min_score: float
    max_score: float
    std_score: float
    measurement_count: int


class PluginUsageSummary(Schema):
    plugin_name: str
    usage_count: int
    artifact_count: int
    avg_duration_seconds: float | None
    successful_runs: int
    failed_runs: int


class ProjectStatsOverview(Schema):
    # Evaluation counts
    total_evaluations: int
    evaluations_by_status: list[EvaluationStatusBreakdown]
    success_rate: float  # percentage of Done evaluations

    # Timing
    avg_evaluation_duration_seconds: float | None
    std_evaluation_duration_seconds: float | None
    last_evaluation_date: datetime | None

    # Measurements
    total_measurements: int
    avg_score: float | None
    avg_uncertainty: float | None
    error_rate: float  # percentage of measurements with error != "N/A"

    # Coverage
    unique_metrics_used: int
    feature_coverage: float  # percentage of features that have measurements
    total_datasets: int
    total_models: int
    datasets_evaluated: int
    models_evaluated: int

    # Plugins
    active_plugins: int
    total_artifacts: int
    num_configs: int


class ProjectStatsMetricBreakdown(Schema):
    metrics: list[MetricScoreSummary]


class PluginRunDuration(Schema):
    plugin_name: str
    run_index: int
    duration_seconds: float


class ProjectStatsPluginDurations(Schema):
    runs: list[PluginRunDuration]


class ProjectStatsPluginUsage(Schema):
    plugins: list[PluginUsageSummary]


# --- Scoped results and the dashboard bridge (ANA-01) ---------------------------


class ResultsVersion(Schema):
    pid: uuid.UUID
    number: int


class ToolHelperOut(Schema):
    role: str
    model: str


class ToolConnectionOut(Schema):
    name: str
    protocol_name: str


class ToolResultOut(Schema):
    plugin_name: str
    label: str
    executed: int
    failed: int
    running: int
    last_run: str | None = None
    local: bool = False
    local_runs: int = 0
    helpers: list[ToolHelperOut] = []
    connections: list[ToolConnectionOut] = []
    dashboard_url: str | None = None


class GreyedToolOut(Schema):
    plugin_name: str
    label: str


class ProjectResultsOut(Schema):
    """One AI-card version's scoped run summary + the analytics dashboard bridge."""

    version: ResultsVersion | None = None
    target: str | None = None
    dashboards_url: str | None = None
    tools: list[ToolResultOut] = []
    greyed: list[GreyedToolOut] = []
