"""Verified PDF path for 3Pass.

Converts a PDF to Markdown/HTML (text, tables, figures) and verifies every block against the
PDF's own text layer and page geometry. Agents and the source router consume only the
verified view; anything a check cannot confirm is held for the researcher.

Modules
  pdf_to_md_html     conversion (Docling; optional heavy dependency)
  verify_conversion  independent checks against the PDF (pypdfium2 + Pillow only)
  agent_tool         verified view, quote check, CLI and MCP server
"""
