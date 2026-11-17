"""
REP-02 backend tests: the report-plugin ABI (per-tool renderers) and the pilot renderer's
fidelity — WeasyPrint's page rule, the vendored CSS, SVG logos and the Word export.

Hermetic: no report service, no network. Auth is the dev-bypass mode.
"""
import base64

from django.test import TestCase

from aisc_backend.models.ai_system import AIComponent, AISystem
from aisc_backend.models.evaluation import Evaluation
from aisc_backend.models.measure import Measurement
from aisc_backend.models.metric import Metric
from aisc_backend.models.observation import Observation
from aisc_backend.models.plugin import EvaluationInput, EvaluationPlugin, Plugin, PluginConfig
from aisc_backend.models.project import Project
from aisc_backend.report_renderer import document as renderer_document
from aisc_backend.report_renderer import svg_print, tools_abi
from aisc_backend.services import report_render

SVG_LOGO = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><rect width="40" height="20"/></svg>'
SVG_LOGO_WITH_RESOURCE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<image href="http://example.com/x.png" width="10" height="10"/></svg>'
)


async def _project(name="plugin-project"):
    return await Project.objects.acreate(name=name, description="", status="Ready")


async def _component(project):
    system, _ = await AISystem.objects.aget_or_create(
        project=project, defaults={"name": "assessed", "description": ""}
    )
    return await AIComponent.objects.acreate(
        system=system, name="the model", description="", data="s3://m", component_type="model"
    )


async def _evaluation_with_run(project, plugin_name, *, package_name=None, metrics=(), local=False):
    """An evaluation with one plugin run and its measurements."""
    plugin = await Plugin.objects.acreate(
        project=project,
        name=plugin_name,
        package_name=package_name or f"aisc-plugin-{plugin_name.lower()}",
        version="1.0",
        display_name=plugin_name,
        description="",
    )
    config = await PluginConfig.objects.acreate(plugin=plugin, name="c", description="", config={})
    evaluation = await Evaluation.objects.acreate(project=project, status="Done")
    run = await EvaluationPlugin.objects.acreate(
        evaluation=evaluation, plugin_config=config, status="Done"
    )
    component = await _component(project)
    await EvaluationInput.objects.acreate(
        evaluation_plugin=run, component=component, name="target", description="",
        value={"local_model": {"path": "/m"}} if local else {"target": "card"},
    )
    observation = await Observation.objects.acreate(
        observer="AISC System", tool=str(plugin), evaluation=evaluation
    )
    for name, score, dimensions in metrics:
        metric = await Metric.objects.acreate(name=name, description="", type_spec="Direct")
        await Measurement.objects.acreate(
            observation=observation, metric=metric, name=name, description="",
            score=score, dimensions=dimensions, time="2026-01-01T00:00:00Z",
        )
    return evaluation


# --- the plugin ABI -------------------------------------------------------------


class PluginRegistryTest(TestCase):

    def test_the_bundled_renderers_are_registered(self):
        by_renderer = {item["renderer"]: item for item in tools_abi.inventory()}
        for name in ("LANGBITEToolRenderer", "MLAREJECTToolRenderer", "PROMPTFOOToolRenderer",
                     "STRONGREJECTToolRenderer"):
            self.assertIn(name, by_renderer)
        self.assertEqual(by_renderer["LANGBITEToolRenderer"]["tool_names"], ["LangBiTe"])
        self.assertEqual(by_renderer["PROMPTFOOToolRenderer"]["tool_names"], ["Promptfoo", "promptfoo"])
        self.assertEqual(by_renderer["LANGBITEToolRenderer"]["contract_version"], 2)

    def test_a_renderer_is_matched_by_normalised_names(self):
        self.assertEqual(
            type(tools_abi.renderer_for({"display_name": "promptfoo"})).__name__, "PROMPTFOOToolRenderer"
        )
        self.assertEqual(
            type(tools_abi.renderer_for({"name": "Strong-Reject"})).__name__, "STRONGREJECTToolRenderer"
        )
        self.assertEqual(
            type(tools_abi.renderer_for({"package_name": "aisc-plugin-langbite"})).__name__,
            "LANGBITEToolRenderer",
        )
        self.assertIsNone(tools_abi.renderer_for({"display_name": "Something else"}))


class ToolRunTest(TestCase):

    async def test_a_tool_run_is_built_from_the_orm_and_drawn_by_its_renderer(self):
        project = await _project()
        evaluation = await _evaluation_with_run(
            project,
            "LangBiTe",
            metrics=[("score", 0.5, {"language": "en", "concern": "Bias"})],
        )
        runs = await tools_abi.tool_runs(evaluation)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run.tool["display_name"], "LangBiTe")
        self.assertEqual(run.tool["package_name"], "aisc-plugin-langbite")
        self.assertEqual(run.measurements[0]["metric"], "score")
        section, renderer = tools_abi.render_run(run, tools_abi.make_ctx())
        self.assertEqual(renderer, "LANGBITEToolRenderer")
        # the tool's own template, not the generic measurements table
        self.assertIn("tool-langbite", section)
        self.assertNotIn('class="measurements"', section)

    async def test_a_tool_without_a_renderer_falls_back_to_the_generic_table(self):
        project = await _project()
        evaluation = await _evaluation_with_run(project, "Unknown Tool", metrics=[("score", 0.25, {})])
        runs = await tools_abi.tool_runs(evaluation)
        section, renderer = tools_abi.render_run(runs[0], tools_abi.make_ctx())
        self.assertEqual(renderer, "generic")
        self.assertIn("measurements", section)
        self.assertIn("score", section)

    async def test_local_runs_are_left_out_of_the_tool_runs(self):
        project = await _project()
        evaluation = await _evaluation_with_run(project, "LangBiTe", local=True, metrics=[("score", 0.5, {})])
        self.assertEqual(await tools_abi.tool_runs(evaluation), [])

    async def test_the_test_results_block_draws_the_tool_section(self):
        project = await _project()
        evaluation = await _evaluation_with_run(
            project, "LangBiTe", metrics=[("score", 0.5, {"language": "en"})]
        )
        snapshot = {
            "project": {"name": "Demo"},
            "system": {"name": "Demo", "version": {"number": 1, "provider": "aisc"}},
            "layout": {"name": "Summary", "revision": 0, "show_index": False, "numbering": False},
            "coverage": [],
            "controls": [],
            "objectives": {"priorities": []},
            "evaluations": [
                {"pid": str(evaluation.pid), "status": "Done", "created_at": "2026-01-01T00:00:00+00:00",
                 "key_results": {"observations": 1, "mean_score": 0.5}},
            ],
            "blocks": [{"instance_id": "b1", "block_type": "test_results", "options": {}}],
            "generated_at": "2026-11-17T00:00:00+00:00",
        }
        html = await renderer_document.render_html(snapshot)
        self.assertIn("tool-langbite", html)


# --- renderer fidelity ----------------------------------------------------------


def _snapshot(blocks=None, style=None):
    options = {"style": style} if style else {}
    return {
        "project": {"pid": "p", "name": "Demo", "description": "A system"},
        "system": {"name": "Demo", "version": {"number": 3, "provider": "aisc"}, "card": None},
        "controls": [],
        "objectives": {"priorities": [], "key_objectives": [], "risks": []},
        "coverage": [],
        "evaluations": [],
        "layout": {"name": "Summary", "revision": 0, "show_index": True, "numbering": True},
        "blocks": blocks
        or [{"instance_id": "c1", "block_type": "cover", "options": options}],
        "generated_at": "2026-11-17T00:00:00+00:00",
    }


class RendererFidelityTest(TestCase):

    async def test_the_page_rule_is_the_pilots(self):
        html = await renderer_document.render_html(_snapshot())
        self.assertIn("@page { size: A4; margin: 22mm 16mm 20mm 16mm;", html)
        self.assertIn("counter(page)", html)
        self.assertIn("Demo, Version 3", html)
        # the pilot's stylesheet is inlined, not just the overrides
        self.assertIn("--l-aif-dominant-100", html)

    async def test_the_style_overrides_the_look(self):
        style = {"font": "inter", "font_size_pt": 11, "primary_color": "#123456",
                 "accent_color": "#654321", "marking": "confidential"}
        html = await renderer_document.render_html(_snapshot(style=style))
        self.assertIn("--l-aif-primary: #123456", html)
        self.assertIn("CONFIDENTIAL", html)
        # the marking feature CSS is inlined with the document
        self.assertIn("the confidentiality marking", html)

    async def test_a_svg_logo_is_embedded_and_drawn(self):
        style = {"font": "inter", "font_size_pt": 10, "primary_color": "#000FDF",
                 "accent_color": "#FF007E",
                 "logo": {"mime": "image/svg+xml", "data_base64": base64.b64encode(SVG_LOGO).decode()}}
        html = await renderer_document.render_html(_snapshot(style=style))
        self.assertIn("data:image/svg+xml;base64,", html)

    async def test_a_logo_with_an_embedded_resource_is_refused(self):
        style = {"font": "inter", "font_size_pt": 10, "primary_color": "#000FDF",
                 "accent_color": "#FF007E",
                 "logo": {"mime": "image/svg+xml",
                          "data_base64": base64.b64encode(SVG_LOGO_WITH_RESOURCE).decode()}}
        html = await renderer_document.render_html(_snapshot(style=style))
        self.assertNotIn("data:image/svg+xml", html)

    async def test_for_print_gives_an_svg_a_size(self):
        printed = svg_print.for_print(SVG_LOGO.decode())
        self.assertIn("width=", printed)
        self.assertIn("height=", printed)

    async def test_pdf_and_docx_are_real_documents(self):
        pdf, ctype = await report_render.render_document(_snapshot(), "pdf")
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(ctype, "application/pdf")
        docx, ctype = await report_render.render_document(_snapshot(), "docx")
        self.assertTrue(docx.startswith(b"PK\x03\x04"))
        self.assertIn("wordprocessingml", ctype)

    async def test_the_word_export_carries_the_report_text(self):
        import io
        import zipfile

        snapshot = _snapshot(
            blocks=[{"instance_id": "b1", "block_type": "free_text", "options": {"text": "A finding."}}]
        )
        blob, _ = await report_render.render_document(snapshot, "docx")
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("A finding.", document_xml)
