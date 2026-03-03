"""Debug panel middleware for django-querylens.

This module provides :class:`QueryLensMiddleware` which automatically injects
a collapsible HTML debug panel at the bottom of every HTML response.  The panel
shows query count, total time, N+1 detections, and slow queries — all powered
by the existing :class:`~django_querylens.analyzer.QueryAnalyzer` infrastructure.

Setup::

    # settings.py
    MIDDLEWARE = [
        ...
        'django_querylens.middleware.QueryLensMiddleware',
    ]

    QUERYLENS = {
        'PANEL': True,   # Enable the debug panel
        ...
    }

The panel is only injected when **all** of the following conditions are met:

* ``QUERYLENS["PANEL"]`` is ``True``
* ``settings.DEBUG`` is ``True``
* The response ``Content-Type`` contains ``text/html``
* The response body contains a ``</body>`` tag
* The response is not a streaming response
"""

from __future__ import annotations

import html as html_module
import logging
from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse

from django_querylens.analyzer import AnalysisResult, QueryAnalyzer, get_querylens_setting

logger = logging.getLogger(__name__)


class QueryLensMiddleware:
    """Django middleware that injects an HTML debug panel into responses.

    The panel provides a fixed bottom bar showing query count and total time,
    with an expandable detail view containing N+1 detections, slow queries,
    and a full query list.

    Example::

        # settings.py
        MIDDLEWARE = [
            ...
            'django_querylens.middleware.QueryLensMiddleware',
        ]

        QUERYLENS = {
            'PANEL': True,
        }
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Initialize the middleware.

        Args:
            get_response: The next middleware or view in the chain.
        """
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse | StreamingHttpResponse:
        """Process the request/response cycle.

        When gate checks pass, wraps the response generation in a
        :meth:`QueryAnalyzer.capture` session and injects the debug panel
        HTML before the closing ``</body>`` tag.

        Args:
            request: The incoming HTTP request.

        Returns:
            The (possibly modified) HTTP response.
        """
        if not self._should_inject():
            return self.get_response(request)

        analyzer = QueryAnalyzer()
        with analyzer.capture() as result:
            response = self.get_response(request)

        # Post-response gate checks
        if isinstance(response, StreamingHttpResponse):
            return response

        content_type = response.get("Content-Type", "")
        if "text/html" not in content_type:
            return response

        try:
            content = response.content.decode(response.charset or "utf-8")
        except (UnicodeDecodeError, AttributeError):
            return response

        # Case-insensitive search for </body>
        body_close_lower = content.lower().find("</body>")
        if body_close_lower == -1:
            return response

        panel_html = self._build_panel(result)
        # Insert before the actual </body> tag (preserve original casing)
        new_content = (
            content[:body_close_lower] + panel_html + content[body_close_lower:]
        )
        response.content = new_content.encode(response.charset or "utf-8")
        if "Content-Length" in response:
            response["Content-Length"] = str(len(response.content))

        return response

    # ------------------------------------------------------------------
    # Gate checks
    # ------------------------------------------------------------------

    @staticmethod
    def _should_inject() -> bool:
        """Determine whether the panel should be injected.

        Returns:
            ``True`` only when ``QUERYLENS["PANEL"]`` and ``settings.DEBUG``
            are both truthy.
        """
        if not settings.DEBUG:
            return False
        if not get_querylens_setting("PANEL", False):
            return False
        return True

    # ------------------------------------------------------------------
    # Panel HTML builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_panel(result: AnalysisResult) -> str:
        """Build the self-contained HTML debug panel.

        Args:
            result: The populated analysis result from the capture session.

        Returns:
            A self-contained HTML string with inline CSS and JS.
        """
        n1_count = len(result.n_plus_one_detected)
        slow_count = len(result.slow_queries)

        # Determine status color
        if n1_count > 0:
            status_color = "#e74c3c"  # red
            bar_bg = "#2d1114"
        elif slow_count > 0:
            status_color = "#e67e22"  # orange
            bar_bg = "#2d2011"
        else:
            status_color = "#27ae60"  # green
            bar_bg = "#112d18"

        # Build status indicator for the bar
        indicators: list[str] = []
        if n1_count > 0:
            indicators.append(
                f'<span class="querylens-bar-badge querylens-bar-error">'
                f"N+1: {n1_count}</span>"
            )
        if slow_count > 0:
            indicators.append(
                f'<span class="querylens-bar-badge querylens-bar-warn">'
                f"Slow: {slow_count}</span>"
            )

        indicator_html = " ".join(indicators)

        # Build N+1 section
        n1_section = ""
        if result.n_plus_one_detected:
            n1_rows = "".join(
                f"<tr>"
                f"<td>{html_module.escape(d.table)}</td>"
                f'<td class="querylens-panel-error">{d.count}</td>'
                f"</tr>"
                for d in result.n_plus_one_detected
            )
            n1_section = (
                f'<div class="querylens-panel-section">'
                f'<h3 class="querylens-panel-error">N+1 Detections</h3>'
                f'<table class="querylens-panel-table">'
                f"<thead><tr><th>Table</th><th>Count</th></tr></thead>"
                f"<tbody>{n1_rows}</tbody>"
                f"</table></div>"
            )

        # Build slow queries section
        slow_section = ""
        if result.slow_queries:
            slow_rows = "".join(
                f"<tr>"
                f'<td class="querylens-panel-sql">'
                f"{html_module.escape(sq.sql)}</td>"
                f'<td class="querylens-panel-warn">{sq.time_ms:.1f}ms</td>'
                f"</tr>"
                for sq in result.slow_queries
            )
            slow_section = (
                f'<div class="querylens-panel-section">'
                f'<h3 class="querylens-panel-warn">Slow Queries</h3>'
                f'<table class="querylens-panel-table">'
                f"<thead><tr><th>SQL</th><th>Time</th></tr></thead>"
                f"<tbody>{slow_rows}</tbody>"
                f"</table></div>"
            )

        # Build all queries section (collapsible)
        all_query_rows = "".join(
            f"<tr>"
            f'<td class="querylens-panel-sql">'
            f"{html_module.escape(q.get('sql', ''))}</td>"
            f"<td>{float(q.get('time', 0)) * 1000:.1f}ms</td>"
            f"</tr>"
            for q in result.queries
        )
        all_queries_section = (
            f'<div class="querylens-panel-section">'
            f"<details>"
            f"<summary>All Queries ({result.total_count})</summary>"
            f'<table class="querylens-panel-table">'
            f"<thead><tr><th>SQL</th><th>Time</th></tr></thead>"
            f"<tbody>{all_query_rows}</tbody>"
            f"</table>"
            f"</details></div>"
        )

        return (
            f"\n<!-- django-querylens debug panel -->\n"
            f"<style>\n"
            f".querylens-bar {{\n"
            f"  position: fixed; bottom: 0; left: 0; right: 0;\n"
            f"  background: {bar_bg}; color: #e0e0e0;\n"
            f"  font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;\n"
            f"  font-size: 13px; z-index: 999999;\n"
            f"  border-top: 2px solid {status_color};\n"
            f"  cursor: pointer; user-select: none;\n"
            f"}}\n"
            f".querylens-bar-summary {{\n"
            f"  padding: 6px 16px;\n"
            f"  display: flex; align-items: center; gap: 12px;\n"
            f"}}\n"
            f".querylens-bar-title {{\n"
            f"  color: {status_color}; font-weight: bold;\n"
            f"}}\n"
            f".querylens-bar-badge {{\n"
            f"  padding: 1px 8px; border-radius: 3px;\n"
            f"  font-size: 11px; font-weight: bold;\n"
            f"}}\n"
            f".querylens-bar-error {{\n"
            f"  background: #5c1a1a; color: #ff6b6b;\n"
            f"}}\n"
            f".querylens-bar-warn {{\n"
            f"  background: #5c3d1a; color: #ffa94d;\n"
            f"}}\n"
            f".querylens-bar-toggle {{\n"
            f"  margin-left: auto; color: #888; font-size: 11px;\n"
            f"}}\n"
            f".querylens-panel {{\n"
            f"  display: none; max-height: 50vh; overflow-y: auto;\n"
            f"  background: #1a1a2e; padding: 12px 16px;\n"
            f"  border-top: 1px solid #333;\n"
            f"}}\n"
            f".querylens-panel.querylens-open {{ display: block; }}\n"
            f".querylens-panel-section {{\n"
            f"  margin-bottom: 12px;\n"
            f"}}\n"
            f".querylens-panel-section h3 {{\n"
            f"  margin: 0 0 6px; font-size: 13px;\n"
            f"}}\n"
            f".querylens-panel-error {{ color: #ff6b6b; }}\n"
            f".querylens-panel-warn {{ color: #ffa94d; }}\n"
            f".querylens-panel-table {{\n"
            f"  width: 100%; border-collapse: collapse;\n"
            f"  font-size: 12px; color: #ccc;\n"
            f"}}\n"
            f".querylens-panel-table th {{\n"
            f"  text-align: left; padding: 4px 8px;\n"
            f"  background: #16213e; color: #8899aa;\n"
            f"  font-weight: normal; font-size: 11px;\n"
            f"  text-transform: uppercase;\n"
            f"}}\n"
            f".querylens-panel-table td {{\n"
            f"  padding: 4px 8px;\n"
            f"  border-bottom: 1px solid #2a2a3e;\n"
            f"}}\n"
            f".querylens-panel-table tr:hover td {{\n"
            f"  background: #16213e;\n"
            f"}}\n"
            f".querylens-panel-sql {{\n"
            f"  word-break: break-all; max-width: 70vw;\n"
            f"}}\n"
            f".querylens-panel details summary {{\n"
            f"  cursor: pointer; color: #8899aa;\n"
            f"  font-size: 12px; margin-bottom: 6px;\n"
            f"}}\n"
            f".querylens-panel details summary:hover {{ color: #ccc; }}\n"
            f"</style>\n"
            f'<div class="querylens-bar" id="querylens-bar">\n'
            f'  <div class="querylens-bar-summary" '
            f'onclick="document.getElementById(\'querylens-detail\')'
            f".classList.toggle('querylens-open')\">\n"
            f'    <span class="querylens-bar-title">django-querylens</span>\n'
            f"    <span>{result.total_count} "
            f"{'query' if result.total_count == 1 else 'queries'}</span>\n"
            f"    <span>{result.total_time:.1f}ms</span>\n"
            f"    {indicator_html}\n"
            f'    <span class="querylens-bar-toggle">'
            f"click to expand</span>\n"
            f"  </div>\n"
            f'  <div class="querylens-panel" id="querylens-detail">\n'
            f"    {n1_section}\n"
            f"    {slow_section}\n"
            f"    {all_queries_section}\n"
            f"  </div>\n"
            f"</div>\n"
            f"<!-- /django-querylens debug panel -->\n"
        )
