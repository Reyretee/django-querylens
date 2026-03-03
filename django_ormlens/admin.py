"""Django admin views for the django-ormlens live query dashboard.

This module provides three admin-protected views:

* **Dashboard** (``/admin/ormlens/``) — a table of recent request reports with
  colour-coded rows indicating N+1 (red), slow (orange), or clean (green).
* **Detail** (``/admin/ormlens/<report_id>/``) — the full HTML-formatted
  analysis for a single report.
* **API** (``/admin/ormlens/api/reports/``) — a JSON endpoint for
  auto-refresh.

All views require staff access and ``DEBUG = True``.

Setup happens automatically via :meth:`~django_ormlens.apps.DjangoOrmLensConfig.ready`
which patches ``AdminSite.get_urls`` to include the ormlens URL patterns.
"""

from __future__ import annotations

import html as html_module
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotFound,
    JsonResponse,
)
from django.middleware.csrf import get_token
from django.urls import URLPattern, path

from django_ormlens.formatters import HtmlFormatter
from django_ormlens.store import get_store

# Type alias for view functions.
_ViewFunc = Callable[..., HttpResponse]

# ---------------------------------------------------------------------------
# Shared dark-theme CSS
# ---------------------------------------------------------------------------

_DARK_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', 'Consolas',
               monospace;
  background: #0d1117; color: #c9d1d9; padding: 24px;
  line-height: 1.5;
}
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
h1 {
  font-size: 20px; color: #f0f6fc;
  border-bottom: 1px solid #21262d; padding-bottom: 12px;
  margin-bottom: 20px;
}
h1 .accent { color: #58a6ff; }
.actions {
  margin-bottom: 16px; display: flex; gap: 8px; align-items: center;
}
.btn {
  padding: 6px 16px; border: 1px solid #30363d; border-radius: 6px;
  cursor: pointer; font-size: 13px; font-family: inherit;
  text-decoration: none; display: inline-block; transition: 0.15s;
}
.btn-refresh { background: #21262d; color: #c9d1d9; }
.btn-refresh:hover { background: #30363d; text-decoration: none; }
.btn-clear {
  background: transparent; color: #f85149;
  border-color: #f8514930;
}
.btn-clear:hover { background: #f8514915; text-decoration: none; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 12px;
  font-size: 11px; font-weight: 600;
}
.badge-n1 { background: #f8514920; color: #f85149; }
.badge-slow { background: #d2992220; color: #d29922; }
.badge-ok { background: #3fb95020; color: #3fb950; }
table {
  width: 100%; border-collapse: collapse;
  background: #161b22; border: 1px solid #21262d;
  border-radius: 6px; overflow: hidden;
}
thead th {
  background: #161b22; color: #8b949e; padding: 10px 14px;
  text-align: left; font-size: 12px; font-weight: 500;
  text-transform: uppercase; letter-spacing: 0.5px;
  border-bottom: 1px solid #21262d;
}
tbody td {
  padding: 8px 14px; font-size: 13px;
  border-bottom: 1px solid #21262d;
}
tbody tr:hover { background: #1c2128; }
tbody tr:last-child td { border-bottom: none; }
.row-n1 { border-left: 3px solid #f85149; }
.row-slow { border-left: 3px solid #d29922; }
.row-ok { border-left: 3px solid #3fb950; }
.empty-state {
  text-align: center; color: #484f58; padding: 40px 14px;
  font-size: 14px;
}
.meta {
  color: #8b949e; font-size: 13px; margin-bottom: 20px;
}
.back {
  display: inline-block; margin-bottom: 16px; color: #8b949e;
  font-size: 13px;
}
.back:hover { color: #c9d1d9; }
.detail-report {
  background: #161b22; border: 1px solid #21262d;
  border-radius: 6px; padding: 20px; margin-top: 12px;
}
.detail-report .ormlens-report { max-width: 100%; }
.detail-report h2 {
  color: #f0f6fc; border-bottom-color: #21262d; font-size: 18px;
}
.detail-report h3 { color: #c9d1d9; font-size: 15px; }
.detail-report .ormlens-table {
  background: #0d1117; border: 1px solid #21262d;
}
.detail-report .ormlens-table th {
  background: #21262d; color: #8b949e;
}
.detail-report .ormlens-table td {
  border-bottom-color: #21262d; color: #c9d1d9;
}
.detail-report .ormlens-table tr:nth-child(even) {
  background: #161b22;
}
.detail-report .ormlens-table tr:hover { background: #1c2128; }
.count { font-variant-numeric: tabular-nums; }
"""


def _require_debug(view_func: _ViewFunc) -> _ViewFunc:
    """Wrap a view to return 403 when ``DEBUG`` is ``False``."""

    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not settings.DEBUG:
            return HttpResponseForbidden("ormlens dashboard requires DEBUG=True.")
        return view_func(request, *args, **kwargs)

    wrapper.__name__ = getattr(view_func, "__name__", "wrapped")
    wrapper.__module__ = getattr(view_func, "__module__", __name__)
    return wrapper


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------


@_require_debug
def ormlens_dashboard_view(request: HttpRequest) -> HttpResponse:
    """Render the main dashboard showing recent request reports.

    Args:
        request: The incoming HTTP request.

    Returns:
        An HTML response with the dashboard table.
    """
    store = get_store()

    if request.method == "POST" and request.POST.get("action") == "clear":
        store.clear()

    reports = store.get_all()

    rows_html = ""
    for report in reports:
        result = report.result
        n1_count = len(result.n_plus_one_detected)
        slow_count = len(result.slow_queries)

        if n1_count > 0:
            row_cls = "row-n1"
        elif slow_count > 0:
            row_cls = "row-slow"
        else:
            row_cls = "row-ok"

        n1_badge = (
            f'<span class="badge badge-n1">{n1_count}</span>'
            if n1_count > 0
            else f'<span class="count">{n1_count}</span>'
        )
        slow_badge = (
            f'<span class="badge badge-slow">{slow_count}</span>'
            if slow_count > 0
            else f'<span class="count">{slow_count}</span>'
        )

        ts = report.timestamp.strftime("%H:%M:%S")
        esc = html_module.escape
        rows_html += (
            f'<tr class="{row_cls}">'
            f"<td>{esc(ts)}</td>"
            f"<td>{esc(report.method)}</td>"
            f"<td>{esc(report.path)}</td>"
            f'<td class="count">{result.total_count}</td>'
            f'<td class="count">{result.total_time:.1f}</td>'
            f"<td>{n1_badge}</td>"
            f"<td>{slow_badge}</td>"
            f'<td><a href="/admin/ormlens/{esc(report.id)}/">'
            f"view</a></td>"
            f"</tr>\n"
        )

    if not reports:
        rows_html = (
            '<tr><td colspan="8" class="empty-state">'
            "No reports yet. Browse your app to start capturing queries."
            "</td></tr>"
        )

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<title>django-ormlens Dashboard</title>
<style>{_DARK_CSS}</style>
</head>
<body>
<h1><span class="accent">django-ormlens</span> &mdash; Query Dashboard</h1>
<div class="actions">
  <a href="/admin/ormlens/" class="btn btn-refresh">Refresh</a>
  <form method="post" style="display:inline">
    <input type="hidden" name="csrfmiddlewaretoken"
           value="{get_token(request)}">
    <input type="hidden" name="action" value="clear">
    <button type="submit" class="btn btn-clear">Clear All</button>
  </form>
  <span style="color:#484f58;font-size:12px;margin-left:auto">
    {store.count} report{'s' if store.count != 1 else ''}
  </span>
</div>
<table>
<thead>
<tr>
  <th>Time</th><th>Method</th><th>Path</th><th>Queries</th>
  <th>Time (ms)</th><th>N+1</th><th>Slow</th><th></th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</body>
</html>"""
    return HttpResponse(html_content)


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------


@_require_debug
def ormlens_detail_view(
    request: HttpRequest, report_id: str
) -> HttpResponse:
    """Render the detail view for a single report.

    Args:
        request: The incoming HTTP request.
        report_id: The hex UUID of the report.

    Returns:
        An HTML response with the formatted analysis, or 404 if not found.
    """
    store = get_store()
    report = store.get_by_id(report_id)

    if report is None:
        return HttpResponseNotFound("Report not found.")

    formatter = HtmlFormatter()
    analysis_html = formatter.format(report.result)

    esc = html_module.escape
    ts = report.timestamp.strftime("%Y-%m-%d %H:%M:%S")
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<title>django-ormlens &mdash; {esc(report.path)}</title>
<style>{_DARK_CSS}</style>
</head>
<body>
<a href="/admin/ormlens/" class="back">&larr; Back to Dashboard</a>
<h1>{esc(report.method)} <span class="accent">{esc(report.path)}</span></h1>
<div class="meta">{esc(report.id)} &middot; {esc(ts)}</div>
<div class="detail-report">
{analysis_html}
</div>
</body>
</html>"""
    return HttpResponse(html_content)


# ---------------------------------------------------------------------------
# API view
# ---------------------------------------------------------------------------


@_require_debug
def ormlens_api_reports_view(request: HttpRequest) -> JsonResponse:
    """Return all stored reports as JSON for auto-refresh.

    Args:
        request: The incoming HTTP request.

    Returns:
        A :class:`JsonResponse` with a ``reports`` list.
    """
    store = get_store()
    reports = store.get_all()

    data = [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat(),
            "path": r.path,
            "method": r.method,
            "total_count": r.result.total_count,
            "total_time": round(r.result.total_time, 3),
            "n_plus_one_count": len(r.result.n_plus_one_detected),
            "slow_query_count": len(r.result.slow_queries),
        }
        for r in reports
    ]

    return JsonResponse({"reports": data})


# ---------------------------------------------------------------------------
# URL patterns
# ---------------------------------------------------------------------------


def get_admin_urls() -> list[URLPattern]:
    """Return URL patterns for the ormlens admin views.

    These patterns are designed to be prepended to ``AdminSite.get_urls()``
    so they are served under ``/admin/ormlens/``.

    Returns:
        A list of :class:`~django.urls.URLPattern` instances.
    """
    from django.contrib.admin import site

    return [
        path(
            "ormlens/api/reports/",
            site.admin_view(ormlens_api_reports_view),
            name="ormlens_api_reports",
        ),
        path(
            "ormlens/<str:report_id>/",
            site.admin_view(ormlens_detail_view),
            name="ormlens_detail",
        ),
        path(
            "ormlens/",
            site.admin_view(ormlens_dashboard_view),
            name="ormlens_dashboard",
        ),
    ]
