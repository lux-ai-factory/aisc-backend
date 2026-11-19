"""The report renderer (REP-02), the one engine (ADR 0008).

Re-expressed on the unified app's ORM snapshot: the pilot's Jinja templates and CSS
(templates/), WeasyPrint (pdf.py), the per-tool section builder (tools_abi.py) and the
document builder (document.py) render a real PDF, Word document or HTML page. The report
ABI a plugin implements (BaseToolRenderer, ToolRun) lives in the published plugin
interface, `aisc_plugin_interface.report`.
"""
