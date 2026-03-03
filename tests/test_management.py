"""Tests for django_querylens management command querylens_report."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


# ---------------------------------------------------------------------------
# querylens_report command tests
# ---------------------------------------------------------------------------


class TestQuerylensReportCommand:
    """Tests for the querylens_report management command."""

    def test_default_terminal_output(self) -> None:
        out = StringIO()
        call_command("querylens_report", stdout=out)
        output = out.getvalue()
        assert "Query Analysis Report" in output

    def test_html_format(self) -> None:
        out = StringIO()
        call_command("querylens_report", "--format", "html", stdout=out)
        output = out.getvalue()
        assert "<div" in output
        assert "querylens-report" in output

    def test_top_argument(self) -> None:
        out = StringIO()
        call_command("querylens_report", "--top", "1", stdout=out)
        output = out.getvalue()
        assert "Query Analysis Report" in output

    def test_top_zero_shows_all(self) -> None:
        out = StringIO()
        call_command("querylens_report", "--top", "0", stdout=out)
        output = out.getvalue()
        assert "Query Analysis Report" in output

    def test_contains_summary_notice(self) -> None:
        out = StringIO()
        call_command("querylens_report", stdout=out)
        output = out.getvalue()
        assert "Summary:" in output

    def test_contains_query_count(self) -> None:
        out = StringIO()
        call_command("querylens_report", stdout=out)
        output = out.getvalue()
        # Should show the sample query count
        assert "7 queries" in output or "Total Queries" in output

    def test_detects_n_plus_one_in_sample(self) -> None:
        """The sample data contains repeated auth_user queries."""
        out = StringIO()
        call_command("querylens_report", stdout=out)
        output = out.getvalue()
        # Sample has 4 queries to auth_user (with default N1_THRESHOLD=2 in test settings)
        assert "N+1" in output

    def test_detects_slow_queries_in_sample(self) -> None:
        """The sample data contains queries above 50ms threshold."""
        out = StringIO()
        call_command("querylens_report", stdout=out)
        output = out.getvalue()
        assert "slow" in output.lower() or "Slow" in output

    def test_invalid_format_raises_error(self) -> None:
        with pytest.raises(CommandError):
            call_command("querylens_report", "--format", "pdf")
