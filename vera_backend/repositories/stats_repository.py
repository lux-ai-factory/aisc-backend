import logging
import uuid

from django.db.models import (
    Count,
    Avg,
    Min,
    Max,
    StdDev,
    Q,
    F,
)
from django.db.models.functions import Coalesce
from django.contrib.contenttypes.models import ContentType
from asgiref.sync import sync_to_async

from vera_backend.models.evaluation import Evaluation, EvaluationStatus
from vera_backend.models.observation import Observation
from vera_backend.models.measure import Measurement
from vera_backend.models.plugin import (
    Plugin,
    EvaluationPlugin,
    EvaluationPluginInputFile,
)
from vera_backend.models.artifact import Artifact
from vera_backend.models.feature import Feature
from vera_backend.models.dataset import Dataset
from vera_backend.models.model import Model

logger = logging.getLogger(__name__)


class StatsRepository:
    async def get_overview(self, project_pid: uuid.UUID) -> dict:
        evaluations = Evaluation.objects.filter(project__pid=project_pid)

        # Evaluation counts by status
        status_counts_qs = evaluations.values("status").annotate(count=Count("id"))
        status_counts = [item async for item in status_counts_qs]

        total = sum(s["count"] for s in status_counts)
        done = next(
            (s["count"] for s in status_counts if s["status"] == EvaluationStatus.Done),
            0,
        )
        success_rate = (done / total * 100) if total > 0 else 0.0

        # Measurements scoped to project
        measurements = Measurement.objects.filter(
            observation__evaluation__project__pid=project_pid
        )

        total_measurements = await measurements.acount()

        agg = await measurements.aaggregate(
            avg_score=Avg("score"),
            avg_uncertainty=Avg("uncertainty"),
        )

        error_count = await measurements.exclude(error="N/A").acount()
        error_rate = (
            (error_count / total_measurements * 100) if total_measurements > 0 else 0.0
        )

        # Unique metrics used
        unique_metrics = await measurements.values("metric").distinct().acount()

        # Feature coverage
        observations = Observation.objects.filter(evaluation__project__pid=project_pid)
        total_features = await Feature.objects.filter(
            datashape__dataset__project__pid=project_pid
        ).acount()
        measured_features = (
            await measurements.filter(feature__isnull=False)
            .values("feature")
            .distinct()
            .acount()
        )
        feature_coverage = (
            (measured_features / total_features * 100) if total_features > 0 else 0.0
        )

        # Avg duration across all individual plugin runs
        timed_plugins = EvaluationPlugin.objects.filter(
            evaluation__project__pid=project_pid,
            started_at__isnull=False,
            finished_at__isnull=False,
        )
        durations = []
        async for ep in timed_plugins.only("started_at", "finished_at"):
            durations.append((ep.finished_at - ep.started_at).total_seconds())

        avg_duration = (sum(durations) / len(durations)) if durations else None
        if durations and len(durations) > 1:
            mean = avg_duration
            std_duration = (
                sum((d - mean) ** 2 for d in durations) / (len(durations) - 1)
            ) ** 0.5
        else:
            std_duration = None

        # Last evaluation date
        last_obs = (
            await observations.order_by("-whenObserved").values("whenObserved").afirst()
        )
        last_evaluation_date = last_obs["whenObserved"] if last_obs else None

        # Datasets and models evaluated (via EvaluationPluginInputFile generic FK)
        eval_plugins = EvaluationPlugin.objects.filter(
            evaluation__project__pid=project_pid
        )
        input_files = EvaluationPluginInputFile.objects.filter(
            evaluation_plugin__in=eval_plugins
        )

        dataset_ct = await sync_to_async(ContentType.objects.get_for_model)(Dataset)
        model_ct = await sync_to_async(ContentType.objects.get_for_model)(Model)

        datasets_evaluated = (
            await input_files.filter(content_type=dataset_ct)
            .values("object_id")
            .distinct()
            .acount()
        )

        models_evaluated = (
            await input_files.filter(content_type=model_ct)
            .values("object_id")
            .distinct()
            .acount()
        )

        # Total datasets and models in project
        total_datasets = await Dataset.objects.filter(project__pid=project_pid).acount()
        total_models = await Model.objects.filter(project__pid=project_pid).acount()

        # Plugins and artifacts
        active_plugins = await Plugin.objects.filter(project__pid=project_pid).acount()
        total_artifacts = await Artifact.objects.filter(
            evaluation_plugin__evaluation__project__pid=project_pid
        ).acount()

        # Plugin configs count
        from vera_backend.models.plugin import PluginConfig

        num_configs = await PluginConfig.objects.filter(
            plugin__project__pid=project_pid
        ).acount()

        return {
            "total_evaluations": total,
            "evaluations_by_status": [
                {"status": s["status"], "count": s["count"]} for s in status_counts
            ],
            "success_rate": round(success_rate, 2),
            "avg_evaluation_duration_seconds": round(avg_duration, 2)
            if avg_duration is not None
            else None,
            "std_evaluation_duration_seconds": round(std_duration, 2)
            if std_duration is not None
            else None,
            "last_evaluation_date": last_evaluation_date,
            "total_measurements": total_measurements,
            "avg_score": round(agg["avg_score"], 4)
            if agg["avg_score"] is not None
            else None,
            "avg_uncertainty": round(agg["avg_uncertainty"], 4)
            if agg["avg_uncertainty"] is not None
            else None,
            "error_rate": round(error_rate, 2),
            "unique_metrics_used": unique_metrics,
            "feature_coverage": round(feature_coverage, 2),
            "datasets_evaluated": datasets_evaluated,
            "models_evaluated": models_evaluated,
            "total_datasets": total_datasets,
            "total_models": total_models,
            "active_plugins": active_plugins,
            "total_artifacts": total_artifacts,
            "num_configs": num_configs,
        }

    async def get_metric_breakdown(self, project_pid: uuid.UUID) -> list[dict]:
        """Per-metric score statistics, grouped by plugin and metric."""
        qs = (
            Measurement.objects.filter(
                observation__evaluation__project__pid=project_pid
            )
            .values(
                "metric__pid",
                "metric__name",
                plugin_name=F("observation__tool"),
            )
            .annotate(
                avg_score=Avg("score"),
                min_score=Min("score"),
                max_score=Max("score"),
                std_score=Coalesce(StdDev("score"), 0.0),
                measurement_count=Count("id"),
            )
        )

        results = []
        async for row in qs:
            results.append(
                {
                    "metric_pid": row["metric__pid"],
                    "metric_name": row["metric__name"],
                    "plugin_name": row["plugin_name"] or "Unknown",
                    "avg_score": round(row["avg_score"], 4),
                    "min_score": round(row["min_score"], 4),
                    "max_score": round(row["max_score"], 4),
                    "std_score": round(row["std_score"], 4),
                    "measurement_count": row["measurement_count"],
                }
            )
        return results

    async def get_plugin_usage(self, project_pid: uuid.UUID) -> list[dict]:
        """Per-plugin usage count, artifact count, avg duration, and success/fail counts."""
        qs = (
            EvaluationPlugin.objects.filter(evaluation__project__pid=project_pid)
            .values(plugin_name=F("plugin_config__plugin__name"))
            .annotate(
                usage_count=Count("id"),
                artifact_count=Count("artifacts"),
                successful_runs=Count(
                    "id", filter=Q(evaluation__status=EvaluationStatus.Done)
                ),
                failed_runs=Count(
                    "id",
                    filter=~Q(
                        evaluation__status__in=[
                            EvaluationStatus.Done,
                            EvaluationStatus.Pending,
                            EvaluationStatus.Processing,
                        ]
                    ),
                ),
            )
        )

        results = []
        async for row in qs:
            # Compute avg duration for this plugin
            timed = EvaluationPlugin.objects.filter(
                evaluation__project__pid=project_pid,
                plugin_config__plugin__name=row["plugin_name"],
                started_at__isnull=False,
                finished_at__isnull=False,
            )
            durations = []
            async for ep in timed.only("started_at", "finished_at"):
                durations.append((ep.finished_at - ep.started_at).total_seconds())

            avg_dur = (sum(durations) / len(durations)) if durations else None

            results.append(
                {
                    "plugin_name": row["plugin_name"],
                    "usage_count": row["usage_count"],
                    "artifact_count": row["artifact_count"],
                    "avg_duration_seconds": round(avg_dur, 2)
                    if avg_dur is not None
                    else None,
                    "successful_runs": row["successful_runs"],
                    "failed_runs": row["failed_runs"],
                }
            )
        return results

    async def get_plugin_durations(self, project_pid: uuid.UUID) -> list[dict]:
        """Per-run duration for each plugin, ordered by start time."""
        qs = (
            EvaluationPlugin.objects.filter(
                evaluation__project__pid=project_pid,
                started_at__isnull=False,
                finished_at__isnull=False,
            )
            .order_by("plugin_config__plugin__name", "started_at")
            .annotate(plugin_name=F("plugin_config__plugin__name"))
            .values("plugin_name", "started_at", "finished_at")
        )

        # Group by plugin name and assign run index
        counters: dict[str, int] = {}
        results = []
        async for row in qs:
            name = row["plugin_name"]
            counters[name] = counters.get(name, 0) + 1
            duration = (row["finished_at"] - row["started_at"]).total_seconds()
            results.append(
                {
                    "plugin_name": name,
                    "run_index": counters[name],
                    "duration_seconds": round(duration, 2),
                }
            )
        return results
