"""HTML to PDF with WeasyPrint.

Nothing is fetched while rendering: only `data:` URIs are read, every other URL is refused.
No temporary file is written.
"""
from __future__ import annotations

import base64
import urllib.parse

import weasyprint
from weasyprint.urls import URLFetcherResponse


class RefusedURL(ValueError):
    """The PDF renderer fetches nothing but data: URIs."""


class _DataResponse(URLFetcherResponse):
    def __init__(self, url, body, headers):
        super().__init__(url, body=body, headers=headers)
        self.string = body


def url_fetcher(url, headers=None, **_kwargs):
    if not isinstance(url, str) or not url.startswith("data:"):
        raise RefusedURL("only data: URIs are allowed in a report")
    try:
        meta, payload = url[5:].split(",", 1)
    except ValueError:
        raise RefusedURL("a malformed data: URI") from None
    parts = meta.split(";")
    mime = parts[0] or "text/plain"
    if "base64" in parts[1:]:
        body = base64.b64decode(payload)
    else:
        body = urllib.parse.unquote_to_bytes(payload)
    return _DataResponse(url, body, {"Content-Type": mime})


class _DataOnly(weasyprint.urls.URLFetcher):
    """WeasyPrint 70's fetcher interface around url_fetcher: data: URIs only, and a refused URL skips
    the resource (it is not fatal), as before."""

    def __init__(self):
        super().__init__(allowed_protocols=("data",), allow_redirects=False, fail_on_errors=False)

    def fetch(self, url, headers=None):
        return url_fetcher(url, headers)


def html_to_pdf(html: str) -> bytes:
    return weasyprint.HTML(string=html, url_fetcher=_DataOnly(), base_url=None).write_pdf()
