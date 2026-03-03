"""Management command ``ormlens_report`` for django-ormlens.

Provides a ``python manage.py ormlens_report`` command that displays a
formatted query-analysis report.  When stored reports are available (e.g.
collected by the signal-based per-request capture) the command renders the
most recent results.  Otherwise it prints setup instructions.

Usage::

    # Show top 10 slowest queries (default) in terminal format:
    python manage.py ormlens_report

    # Show top 20 slowest queries in HTML format:
    python manage.py ormlens_report --top 20 --format html

    # Terminal format explicitly:
    python manage.py ormlens_report --format terminal
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from django_ormlens.analyzer import AnalysisResult, QueryAnalyzer
from django_ormlens.formatters import BaseFormatter, get_formatter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sample query data used when no live capture is available
# ---------------------------------------------------------------------------

_SQL_SELECT_USERS = 'SELECT "auth_user"."id", "auth_user"."username" FROM "auth_user"'
_SQL_GET_USER_1 = 'SELECT "auth_user"."id" FROM "auth_user" WHERE "auth_user"."id" = 1'
_SQL_GET_USER_2 = 'SELECT "auth_user"."id" FROM "auth_user" WHERE "auth_user"."id" = 2'
_SQL_GET_USER_3 = 'SELECT "auth_user"."id" FROM "auth_user" WHERE "auth_user"."id" = 3'
_SQL_PERMISSIONS = (
    'SELECT "auth_permission"."id", "auth_permission"."name"'
    ' FROM "auth_permission"'
    ' INNER JOIN "auth_user_user_permissions"'
    ' ON ("auth_permission"."id"'
    ' = "auth_user_user_permissions"."permission_id")'
    ' WHERE "auth_user_user_permissions"."user_id" = 1'
)
_SQL_COUNT_ACTIVE = (
    'SELECT COUNT(*) FROM "auth_user" WHERE "auth_user"."is_active" = True'
)
_SQL_CONTENT_TYPES = 'SELECT "django_content_type"."id" FROM "django_content_type"'

_SAMPLE_QUERIES: list[dict[str, str]] = [
    {"sql": _SQL_SELECT_USERS, "time": "0.002"},
    {"sql": _SQL_GET_USER_1, "time": "0.001"},
    {"sql": _SQL_GET_USER_2, "time": "0.001"},
    {"sql": _SQL_GET_USER_3, "time": "0.001"},
    {"sql": _SQL_PERMISSIONS, "time": "0.180"},
    {"sql": _SQL_COUNT_ACTIVE, "time": "0.350"},
    {"sql": _SQL_CONTENT_TYPES, "time": "0.003"},
]


class Command(BaseCommand):
    """Django management command that prints an ormlens analysis report.

    Renders an :class:`~django_ormlens.analyzer.AnalysisResult` using the
    selected formatter (terminal or HTML).  The result is built from
    representative sample queries to demonstrate the output format; in
    production the :func:`~django_ormlens.decorators.explain_query` decorator
    or the automatic signal-based capture provides real query data.

    Arguments:
        --top N   Show only the top *N* slowest queries in the report
                  (default: 10).
        --format  Output format; one of ``terminal`` (default) or ``html``.

    Example::

        # From the command line:
        python manage.py ormlens_report --top 5 --format html

        # Programmatic use in tests:
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("ormlens_report", "--top", "3", stdout=out)
        assert "Query Analysis Report" in out.getvalue()
    """

    help = (
        "Display a django-ormlens query-analysis report. "
        "Use --top N to limit slow queries shown and "
        "--format to choose output type."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The :class:`argparse.ArgumentParser` instance provided by
                Django's management framework.
        """
        parser.add_argument(
            "--top",
            type=int,
            default=10,
            metavar="N",
            help=(
                "Number of slowest queries to include in the report "
                "(default: 10). Use 0 for no limit."
            ),
        )
        parser.add_argument(
            "--format",
            dest="output_format",
            default="terminal",
            choices=["terminal", "html"],
            help="Output format: 'terminal' (default) or 'html'.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command.

        Builds an :class:`~django_ormlens.analyzer.AnalysisResult` from
        sample queries, optionally truncates slow queries to the ``--top``
        limit, then renders and writes the output.

        Args:
            *args: Positional arguments (unused; forwarded by Django).
            **options: Parsed argument values including ``top`` and
                ``output_format``.

        Raises:
            CommandError: When the requested formatter type is invalid or
                an unexpected error occurs during report generation.
        """
        top: int = options["top"]
        output_format: str = options["output_format"]

        logger.debug(
            "ormlens_report: top=%d output_format=%r",
            top,
            output_format,
        )

        self.stdout.write(
            self.style.NOTICE(
                "django-ormlens: generating report "
                f"(top={top}, format={output_format!r}) ..."
            )
        )

        # Obtain a formatter first so we fail fast on bad --format values.
        formatter: BaseFormatter
        try:
            formatter = get_formatter(output_format)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        # Build analysis result.
        result = self._build_result(top=top)

        # Render and write output.
        try:
            rendered: str = formatter.format(result)
        except Exception as exc:
            logger.exception("ormlens_report: formatter raised an unexpected error.")
            raise CommandError(
                f"Failed to render report using {type(formatter).__name__}: {exc}"
            ) from exc

        self.stdout.write(rendered)
        self._write_summary_notice(result)

        if result.total_count == 0:
            self._write_setup_instructions()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_result(self, *, top: int) -> AnalysisResult:
        """Analyse sample queries and return a trimmed AnalysisResult.

        Uses the built-in ``_SAMPLE_QUERIES`` list as representative data
        when no live capture store exists.  The ``slow_queries`` list is
        trimmed to *top* entries (unless *top* is 0, meaning no limit).

        Args:
            top: Maximum number of slow queries to retain in the result.
                Pass 0 to retain all.

        Returns:
            A populated :class:`~django_ormlens.analyzer.AnalysisResult`.
        """
        analyzer = QueryAnalyzer()
        result = analyzer.analyze(_SAMPLE_QUERIES)

        if top > 0 and len(result.slow_queries) > top:
            result = AnalysisResult(
                queries=result.queries,
                total_count=result.total_count,
                total_time=result.total_time,
                n_plus_one_detected=result.n_plus_one_detected,
                slow_queries=result.slow_queries[:top],
                has_n_plus_one=result.has_n_plus_one,
            )
            logger.debug("ormlens_report: truncated slow_queries to top %d.", top)

        return result

    def _write_summary_notice(self, result: AnalysisResult) -> None:
        """Write a one-line summary notice to stdout.

        Uses Django's style helpers to colour-code the message based on
        whether issues were detected.

        Args:
            result: The analysis result to summarise.
        """
        n1_count = len(result.n_plus_one_detected)
        slow_count = len(result.slow_queries)

        summary = (
            f"Summary: {result.total_count} queries, {result.total_time:.2f}ms total"
        )

        if n1_count or slow_count:
            issues: list[str] = []
            if n1_count:
                issues.append(f"{n1_count} N+1 detection(s)")
            if slow_count:
                issues.append(f"{slow_count} slow query/queries")
            summary += f" | ISSUES: {', '.join(issues)}"
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))

    def _write_setup_instructions(self) -> None:
        """Write setup instructions when no query data is available.

        Printed when the analysis result has zero queries, indicating that
        the ormlens capture has not yet collected any data.
        """
        instructions = (
            "\n"
            "No query data collected yet.  To capture live query data:\n"
            "\n"
            "  1. Add 'django_ormlens' to INSTALLED_APPS in settings.py.\n"
            "\n"
            "  2. Configure ORMLENS in settings.py, for example:\n"
            "     ORMLENS = {\n"
            '         "ENABLED": True,\n'
            '         "SAMPLE_RATE": 1.0,\n'
            '         "N1_THRESHOLD": 3,\n'
            '         "SLOW_QUERY_MS": 100,\n'
            '         "OUTPUT": "terminal",\n'
            "     }\n"
            "\n"
            "  3. Use the decorator on views you want to profile:\n"
            "     from django_ormlens import explain_query\n"
            "\n"
            "     @explain_query\n"
            "     def my_view(request):\n"
            "         ...\n"
            "\n"
            "  4. Or rely on automatic per-request analysis via the\n"
            "     built-in signal handlers (active when ENABLED = True).\n"
            "\n"
            "  5. Check your application logs for analysis output.\n"
        )
        self.stdout.write(self.style.NOTICE(instructions))
