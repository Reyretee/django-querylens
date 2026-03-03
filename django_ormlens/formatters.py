"""Output formatters for django-ormlens analysis results.

This module provides :class:`TerminalFormatter` and :class:`HtmlFormatter`
for rendering :class:`~django_ormlens.analyzer.AnalysisResult` objects into
human-readable strings, plus a :func:`get_formatter` factory that reads the
``ORMLENS["OUTPUT"]`` setting and returns the appropriate formatter instance.

Typical usage::

    from django_ormlens.formatters import get_formatter

    formatter = get_formatter("terminal")
    output = formatter.format(result)

    # Auto-detect from settings:
    formatter = get_formatter()
    output = formatter.format(result)
"""

from __future__ import annotations

import html as html_module
import logging
from abc import ABC, abstractmethod
from typing import Any

from django_ormlens.analyzer import AnalysisResult, get_ormlens_setting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional colorama import — soft dependency via extras_require["color"]
# ---------------------------------------------------------------------------

try:
    import colorama  # type: ignore[import-untyped,unused-ignore]

    colorama.init(autoreset=True)

    _RESET = colorama.Style.RESET_ALL
    _BOLD = colorama.Style.BRIGHT
    _RED = colorama.Fore.RED
    _YELLOW = colorama.Fore.YELLOW
    _GREEN = colorama.Fore.GREEN
    _CYAN = colorama.Fore.CYAN
    _HAS_COLOR = True
except ImportError:
    _RESET = ""
    _BOLD = ""
    _RED = ""
    _YELLOW = ""
    _GREEN = ""
    _CYAN = ""
    _HAS_COLOR = False

# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class BaseFormatter(ABC):
    """Abstract base class for all django-ormlens output formatters.

    Subclasses must implement :meth:`format` to convert an
    :class:`~django_ormlens.analyzer.AnalysisResult` into a string.

    Example::

        class MyFormatter(BaseFormatter):
            def format(self, result: AnalysisResult) -> str:
                return f"Total queries: {result.total_count}"
    """

    @abstractmethod
    def format(self, result: AnalysisResult) -> str:
        """Render an analysis result as a string.

        Args:
            result: The populated
                :class:`~django_ormlens.analyzer.AnalysisResult` to render.

        Returns:
            A formatted string representation of the analysis result.
        """


# ---------------------------------------------------------------------------
# Terminal formatter
# ---------------------------------------------------------------------------


class TerminalFormatter(BaseFormatter):
    """Renders an AnalysisResult as a box-drawn terminal table.

    When ``colorama`` is installed (via the ``color`` extra) the output uses
    ANSI escape codes to highlight warnings (N+1 detections, slow queries) in
    colour.  When ``colorama`` is absent the output is plain text, suitable
    for CI logs and file redirection.

    The rendered output contains three sections:

    1. **Summary table** — total queries, total time, N+1 count, slow count.
    2. **N+1 detections** — a table of offending table names and repeat counts
       (only shown when at least one detection exists).
    3. **Slow queries** — a table listing each slow query SQL and its
       execution time (only shown when at least one slow query exists).

    Example::

        formatter = TerminalFormatter()
        output = formatter.format(result)
    """

    # Width constants for the summary table columns.
    _COL_LABEL_W: int = 22
    _COL_VALUE_W: int = 12

    # Width constants for the N+1 / slow-query detail tables.
    _DETAIL_TABLE_W: int = 32
    _DETAIL_COUNT_W: int = 10
    _DETAIL_SQL_W: int = 72
    _DETAIL_TIME_W: int = 12

    def format(self, result: AnalysisResult) -> str:
        """Render *result* as a terminal-friendly string.

        Args:
            result: The populated
                :class:`~django_ormlens.analyzer.AnalysisResult` to render.

        Returns:
            A multi-line string with box-drawn tables and optional ANSI colour.
        """
        logger.debug("TerminalFormatter.format() called.")
        parts: list[str] = [self._render_summary(result)]

        if result.n_plus_one_detected:
            parts.append(self._render_n_plus_one(result))

        if result.slow_queries:
            parts.append(self._render_slow_queries(result))

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Private rendering helpers
    # ------------------------------------------------------------------

    def _c(self, code: str, text: str) -> str:
        """Wrap *text* in a colour escape code (no-op when colour unavailable).

        Args:
            code: ANSI colour/style prefix string.
            text: The text to colour.

        Returns:
            Coloured string when colorama is available, else plain *text*.
        """
        if _HAS_COLOR:
            return f"{code}{text}{_RESET}"
        return text

    @staticmethod
    def _visible_len(text: str) -> int:
        """Return the visible (non-ANSI) character count of *text*.

        Args:
            text: A string that may contain ANSI escape codes.

        Returns:
            The number of printable characters in *text*.
        """
        for code in (_RED, _YELLOW, _BOLD, _RESET, _GREEN, _CYAN):
            if code:
                text = text.replace(code, "")
        return len(text)

    def _rpad(self, text: str, width: int) -> str:
        """Right-pad *text* to *width* accounting for invisible ANSI codes.

        Args:
            text: The string to pad (may contain ANSI codes).
            width: The desired visible character width.

        Returns:
            *text* padded with trailing spaces to reach *width*.
        """
        pad = width - self._visible_len(text)
        return text + " " * max(pad, 0)

    def _lpad(self, text: str, width: int) -> str:
        """Left-pad *text* to *width* accounting for invisible ANSI codes.

        Args:
            text: The string to pad (may contain ANSI codes).
            width: The desired visible character width.

        Returns:
            Leading spaces followed by *text* to reach *width*.
        """
        pad = width - self._visible_len(text)
        return " " * max(pad, 0) + text

    def _render_summary(self, result: AnalysisResult) -> str:
        """Render the summary box table.

        Args:
            result: The analysis result.

        Returns:
            A multi-line string showing total queries, time, N+1 count, and
            slow query count inside a box-drawn table.
        """
        lw = self._COL_LABEL_W
        vw = self._COL_VALUE_W
        total_w = lw + vw + 3  # borders + separators

        top = "\u250c" + "\u2500" * (total_w) + "\u2510"
        title_text = " django-ormlens \u2014 Query Analysis Report "
        title_pad = total_w - len(title_text)
        title_l = title_pad // 2
        title_r = title_pad - title_l
        title = (
            "\u2502"
            + " " * title_l
            + self._c(_BOLD + _CYAN, title_text)
            + " " * title_r
            + "\u2502"
        )
        sep = "\u251c" + "\u2500" * (lw + 2) + "\u252c" + "\u2500" * (vw + 2) + "\u2524"
        header = (
            "\u2502 "
            + self._rpad(self._c(_BOLD, "Metric"), lw)
            + " \u2502 "
            + self._lpad(self._c(_BOLD, "Value"), vw)
            + " \u2502"
        )
        sep2 = (
            "\u251c" + "\u2500" * (lw + 2) + "\u253c" + "\u2500" * (vw + 2) + "\u2524"
        )
        bot = "\u2514" + "\u2500" * (lw + 2) + "\u2534" + "\u2500" * (vw + 2) + "\u2518"

        n1_count = len(result.n_plus_one_detected)
        slow_count = len(result.slow_queries)

        n1_val = self._c(_RED + _BOLD, str(n1_count)) if n1_count > 0 else str(n1_count)
        slow_val = (
            self._c(_YELLOW + _BOLD, str(slow_count))
            if slow_count > 0
            else str(slow_count)
        )

        rows: list[tuple[str, str]] = [
            ("Total Queries", str(result.total_count)),
            ("Total Time (ms)", f"{result.total_time:.3f}"),
            ("N+1 Detections", n1_val),
            ("Slow Queries", slow_val),
        ]

        blank = "\u2502" + " " * (lw + 2) + "\u2502" + " " * (vw + 2) + "\u2502"
        rendered_rows: list[str] = []
        for label, value in rows:
            label_cell = f"{label:<{lw}}"
            value_cell = self._lpad(value, vw)
            rendered_rows.append(f"\u2502 {label_cell} \u2502 {value_cell} \u2502")

        lines = [top, title, sep, header, sep2]
        for i, row in enumerate(rendered_rows):
            lines.append(row)
            if i < len(rendered_rows) - 1:
                lines.append(blank)
        lines.append(bot)
        return "\n".join(lines)

    def _render_n_plus_one(self, result: AnalysisResult) -> str:
        """Render the N+1 detections section.

        Args:
            result: The analysis result.

        Returns:
            A multi-line string listing each N+1 detection with table name
            and repeat count.
        """
        tw = self._DETAIL_TABLE_W
        cw = self._DETAIL_COUNT_W

        top = "\u250c" + "\u2500" * (tw + 2) + "\u252c" + "\u2500" * (cw + 2) + "\u2510"
        heading_label = self._c(_RED + _BOLD, "N+1 Detections")
        heading_count = self._c(_BOLD, "Count")
        heading = (
            "\u2502 "
            + self._rpad(heading_label, tw)
            + " \u2502 "
            + self._lpad(heading_count, cw)
            + " \u2502"
        )
        sep = "\u251c" + "\u2500" * (tw + 2) + "\u253c" + "\u2500" * (cw + 2) + "\u2524"
        bot = "\u2514" + "\u2500" * (tw + 2) + "\u2534" + "\u2500" * (cw + 2) + "\u2518"

        lines = [top, heading, sep]
        for detection in result.n_plus_one_detected:
            table_cell = f"{detection.table:<{tw}}"
            count_cell = self._lpad(self._c(_RED, str(detection.count)), cw)
            lines.append(f"\u2502 {table_cell} \u2502 {count_cell} \u2502")
        lines.append(bot)
        return "\n".join(lines)

    def _render_slow_queries(self, result: AnalysisResult) -> str:
        """Render the slow queries section.

        Args:
            result: The analysis result.

        Returns:
            A multi-line string listing each slow query with truncated SQL
            and execution time.
        """
        sw = self._DETAIL_SQL_W
        tw = self._DETAIL_TIME_W

        top = "\u250c" + "\u2500" * (sw + 2) + "\u252c" + "\u2500" * (tw + 2) + "\u2510"
        heading_sql = self._c(_YELLOW + _BOLD, "Slow Query (SQL)")
        heading_time = self._c(_BOLD, "Time (ms)")
        heading = (
            "\u2502 "
            + self._rpad(heading_sql, sw)
            + " \u2502 "
            + self._lpad(heading_time, tw)
            + " \u2502"
        )
        sep = "\u251c" + "\u2500" * (sw + 2) + "\u253c" + "\u2500" * (tw + 2) + "\u2524"
        bot = "\u2514" + "\u2500" * (sw + 2) + "\u2534" + "\u2500" * (tw + 2) + "\u2518"

        lines = [top, heading, sep]
        for slow in result.slow_queries:
            truncated = slow.sql[:sw] if len(slow.sql) > sw else slow.sql
            sql_cell = f"{truncated:<{sw}}"
            time_str = f"{slow.time_ms:.1f}"
            time_cell = self._lpad(self._c(_YELLOW, time_str), tw)
            lines.append(f"\u2502 {sql_cell} \u2502 {time_cell} \u2502")
        lines.append(bot)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML formatter
# ---------------------------------------------------------------------------


class HtmlFormatter(BaseFormatter):
    """Renders an AnalysisResult as an HTML fragment with styled tables.

    The output is a self-contained ``<div>`` block with inline ``<style>``
    suitable for embedding in Django templates, admin pages, or standalone
    HTML reports.  All user-facing content (SQL strings, table names) is
    HTML-escaped to prevent XSS injection.

    The rendered output contains:

    * A summary ``<table>`` with total queries, total time, N+1 count, and
      slow query count.
    * An N+1 detections ``<table>`` (only when detections exist).
    * A slow queries ``<table>`` (only when slow queries exist).

    Example::

        formatter = HtmlFormatter()
        html_output = formatter.format(result)
    """

    def format(self, result: AnalysisResult) -> str:
        """Render *result* as an HTML fragment string.

        Args:
            result: The populated
                :class:`~django_ormlens.analyzer.AnalysisResult` to render.

        Returns:
            An HTML string containing styled tables with the analysis data.
            All dynamic content is HTML-escaped.
        """
        logger.debug("HtmlFormatter.format() called.")
        parts: list[str] = [self._styles(), self._render_summary(result)]

        if result.n_plus_one_detected:
            parts.append(self._render_n_plus_one(result))

        if result.slow_queries:
            parts.append(self._render_slow_queries(result))

        inner = "\n".join(parts)
        return f'<div class="ormlens-report">\n{inner}\n</div>'

    # ------------------------------------------------------------------
    # Private rendering helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _styles() -> str:
        """Return the inline CSS style block for the report.

        Returns:
            A ``<style>`` HTML element string with all report styles.
        """
        return (
            "<style>\n"
            ".ormlens-report { font-family: monospace; max-width: 900px; }\n"
            ".ormlens-report h2 { color: #2c3e50;"
            " border-bottom: 2px solid #3498db; }\n"
            ".ormlens-report h3 { color: #2c3e50; }\n"
            ".ormlens-table { border-collapse: collapse;"
            " width: 100%; margin-bottom: 1em; }\n"
            ".ormlens-table th { background: #2c3e50; color: #fff;"
            " padding: 8px 12px; text-align: left; }\n"
            ".ormlens-table td { padding: 6px 12px;"
            " border-bottom: 1px solid #ddd; }\n"
            ".ormlens-table tr:nth-child(even) { background: #f9f9f9; }\n"
            ".ormlens-table tr:hover { background: #eaf4fb; }\n"
            ".ormlens-badge-ok { color: #27ae60; font-weight: bold; }\n"
            ".ormlens-badge-warn { color: #e67e22; font-weight: bold; }\n"
            ".ormlens-badge-error { color: #e74c3c; font-weight: bold; }\n"
            ".ormlens-sql { font-size: 0.85em; word-break: break-all; }\n"
            "</style>"
        )

    @staticmethod
    def _badge(value: int, *, warn_cls: str) -> str:
        """Wrap a numeric *value* in a coloured CSS badge span.

        Args:
            value: The numeric value to display.
            warn_cls: CSS class to apply when *value* is greater than zero.

        Returns:
            An HTML ``<span>`` element with appropriate CSS class.
        """
        css = warn_cls if value > 0 else "ormlens-badge-ok"
        escaped = html_module.escape(str(value))
        return f'<span class="{css}">{escaped}</span>'

    def _render_summary(self, result: AnalysisResult) -> str:
        """Render the summary section as an HTML table.

        Args:
            result: The analysis result.

        Returns:
            An HTML string for the summary section including heading and table.
        """
        n1_badge = self._badge(
            len(result.n_plus_one_detected),
            warn_cls="ormlens-badge-error",
        )
        slow_badge = self._badge(
            len(result.slow_queries),
            warn_cls="ormlens-badge-warn",
        )

        rows: list[tuple[str, str]] = [
            ("Total Queries", html_module.escape(str(result.total_count))),
            ("Total Time (ms)", html_module.escape(f"{result.total_time:.3f}")),
            ("N+1 Detections", n1_badge),
            ("Slow Queries", slow_badge),
        ]

        row_html = "\n".join(
            f"    <tr><td>{html_module.escape(label)}</td><td>{value}</td></tr>"
            for label, value in rows
        )

        return (
            "<h2>django-ormlens \u2014 Query Analysis Report</h2>\n"
            '<table class="ormlens-table">\n'
            "  <thead>\n"
            "    <tr><th>Metric</th><th>Value</th></tr>\n"
            "  </thead>\n"
            "  <tbody>\n"
            f"{row_html}\n"
            "  </tbody>\n"
            "</table>"
        )

    def _render_n_plus_one(self, result: AnalysisResult) -> str:
        """Render the N+1 detections section as an HTML table.

        Args:
            result: The analysis result.

        Returns:
            An HTML string for the N+1 section including heading and table.
        """
        row_html = "\n".join(
            f"    <tr>"
            f"<td>{html_module.escape(d.table)}</td>"
            f'<td class="ormlens-badge-error">'
            f"{html_module.escape(str(d.count))}"
            f"</td>"
            f"</tr>"
            for d in result.n_plus_one_detected
        )

        return (
            "<h3>N+1 Detections</h3>\n"
            '<table class="ormlens-table">\n'
            "  <thead>\n"
            "    <tr><th>Table</th><th>Count</th></tr>\n"
            "  </thead>\n"
            "  <tbody>\n"
            f"{row_html}\n"
            "  </tbody>\n"
            "</table>"
        )

    def _render_slow_queries(self, result: AnalysisResult) -> str:
        """Render the slow queries section as an HTML table.

        Args:
            result: The analysis result.

        Returns:
            An HTML string for the slow queries section including heading
            and table.
        """
        row_html = "\n".join(
            f"    <tr>"
            f'<td class="ormlens-sql">{html_module.escape(sq.sql)}</td>'
            f'<td class="ormlens-badge-warn">'
            f"{html_module.escape(f'{sq.time_ms:.1f}')}"
            f"</td>"
            f"</tr>"
            for sq in result.slow_queries
        )

        return (
            "<h3>Slow Queries</h3>\n"
            '<table class="ormlens-table">\n'
            "  <thead>\n"
            "    <tr><th>SQL</th><th>Time (ms)</th></tr>\n"
            "  </thead>\n"
            "  <tbody>\n"
            f"{row_html}\n"
            "  </tbody>\n"
            "</table>"
        )


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

#: Mapping from output type string to formatter class.
_FORMATTER_REGISTRY: dict[str, type[BaseFormatter]] = {
    "terminal": TerminalFormatter,
    "html": HtmlFormatter,
}


def get_formatter(output_type: str | None = None) -> BaseFormatter:
    """Return a formatter instance for the requested *output_type*.

    When *output_type* is ``None`` the value is read from
    ``ORMLENS["OUTPUT"]`` in Django settings, defaulting to ``"terminal"``
    if the setting is absent.

    Args:
        output_type: One of ``"terminal"`` or ``"html"``.  Pass ``None`` to
            auto-detect from ``ORMLENS["OUTPUT"]``.

    Returns:
        A :class:`BaseFormatter` subclass instance matching *output_type*.

    Raises:
        ValueError: When *output_type* is not a recognised formatter key.

    Example::

        formatter = get_formatter("html")
        html_output = formatter.format(result)

        # Auto-detect from settings:
        formatter = get_formatter()
        output = formatter.format(result)
    """
    resolved: str
    if output_type is None:
        setting_value: Any = get_ormlens_setting("OUTPUT", "terminal")
        resolved = str(setting_value).lower().strip()
    else:
        resolved = output_type.lower().strip()

    formatter_cls = _FORMATTER_REGISTRY.get(resolved)
    if formatter_cls is None:
        valid = ", ".join(sorted(_FORMATTER_REGISTRY))
        msg = f"Unknown output type {resolved!r}. Valid options are: {valid}."
        logger.error("get_formatter: %s", msg)
        raise ValueError(msg)

    logger.debug(
        "get_formatter: using %s for output_type=%r.",
        formatter_cls.__name__,
        resolved,
    )
    return formatter_cls()
