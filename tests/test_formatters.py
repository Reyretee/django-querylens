"""Tests for django_querylens.formatters module."""

from __future__ import annotations

import pytest

from django_querylens.analyzer import AnalysisResult, N1Detection, SlowQuery
from django_querylens.formatters import (
    BaseFormatter,
    HtmlFormatter,
    TerminalFormatter,
    get_formatter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_result() -> AnalysisResult:
    """An empty analysis result with no queries."""
    return AnalysisResult()


@pytest.fixture
def basic_result() -> AnalysisResult:
    """A basic result with queries but no issues."""
    return AnalysisResult(
        queries=[
            {"sql": "SELECT 1", "time": "0.001"},
            {"sql": "SELECT 2", "time": "0.002"},
        ],
        total_count=2,
        total_time=3.0,
    )


@pytest.fixture
def full_result() -> AnalysisResult:
    """A result with N+1 detections and slow queries."""
    return AnalysisResult(
        queries=[
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
            {"sql": "SELECT * FROM big_table", "time": "0.200"},
        ],
        total_count=4,
        total_time=203.0,
        n_plus_one_detected=[N1Detection(table="auth_user", count=3)],
        slow_queries=[SlowQuery(sql="SELECT * FROM big_table", time_ms=200.0)],
        has_n_plus_one=True,
    )


# ---------------------------------------------------------------------------
# BaseFormatter
# ---------------------------------------------------------------------------


class TestBaseFormatter:
    """Tests for the abstract base class."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            BaseFormatter()  # type: ignore[abstract]

    def test_subclass_must_implement_format(self) -> None:
        class IncompleteFormatter(BaseFormatter):
            pass

        with pytest.raises(TypeError):
            IncompleteFormatter()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# TerminalFormatter
# ---------------------------------------------------------------------------


class TestTerminalFormatter:
    """Tests for the terminal output formatter."""

    def test_format_empty_result(self, empty_result: AnalysisResult) -> None:
        formatter = TerminalFormatter()
        output = formatter.format(empty_result)
        assert "Query Analysis Report" in output
        assert "Total Queries" in output
        assert "0" in output

    def test_format_basic_result(self, basic_result: AnalysisResult) -> None:
        formatter = TerminalFormatter()
        output = formatter.format(basic_result)
        assert "Total Queries" in output
        assert "2" in output
        assert "3.000" in output

    def test_format_with_n_plus_one(self, full_result: AnalysisResult) -> None:
        formatter = TerminalFormatter()
        output = formatter.format(full_result)
        assert "N+1 Detections" in output
        assert "auth_user" in output
        assert "3" in output

    def test_format_with_slow_queries(self, full_result: AnalysisResult) -> None:
        formatter = TerminalFormatter()
        output = formatter.format(full_result)
        assert "Slow Query" in output
        assert "200.0" in output

    def test_no_n_plus_one_detail_section_when_empty(
        self, basic_result: AnalysisResult
    ) -> None:
        """The detail table with table names should not appear."""
        formatter = TerminalFormatter()
        output = formatter.format(basic_result)
        # The summary always has "N+1 Detections" as a label, but the
        # detail table with "Count" header should NOT appear.
        lines = output.split("\n")
        # Count how many times "N+1 Detections" appears — should be
        # exactly once (in the summary row), not twice (with detail table).
        n1_count = sum(1 for line in lines if "N+1 Detections" in line)
        assert n1_count == 1  # only in summary row

    def test_no_slow_queries_detail_section_when_empty(
        self, basic_result: AnalysisResult
    ) -> None:
        """The detail table with SQL should not appear."""
        formatter = TerminalFormatter()
        output = formatter.format(basic_result)
        assert "Slow Query (SQL)" not in output

    def test_uses_box_drawing_characters(
        self, basic_result: AnalysisResult
    ) -> None:
        formatter = TerminalFormatter()
        output = formatter.format(basic_result)
        # Check for Unicode box-drawing chars
        assert "\u2502" in output  # │
        assert "\u2500" in output  # ─

    def test_visible_len_plain_text(self) -> None:
        assert TerminalFormatter._visible_len("hello") == 5

    def test_rpad_plain_text(self) -> None:
        formatter = TerminalFormatter()
        result = formatter._rpad("hi", 5)
        assert len(result) == 5
        assert result == "hi   "

    def test_lpad_plain_text(self) -> None:
        formatter = TerminalFormatter()
        result = formatter._lpad("42", 5)
        assert len(result) == 5
        assert result == "   42"

    def test_long_sql_truncated(self) -> None:
        """SQL longer than column width should be truncated."""
        long_sql = "SELECT " + "x" * 200
        result = AnalysisResult(
            total_count=1,
            total_time=150.0,
            slow_queries=[SlowQuery(sql=long_sql, time_ms=150.0)],
        )
        formatter = TerminalFormatter()
        output = formatter.format(result)
        # The SQL should appear truncated in the output
        assert "Slow Query" in output


# ---------------------------------------------------------------------------
# HtmlFormatter
# ---------------------------------------------------------------------------


class TestHtmlFormatter:
    """Tests for the HTML output formatter."""

    def test_format_empty_result(self, empty_result: AnalysisResult) -> None:
        formatter = HtmlFormatter()
        output = formatter.format(empty_result)
        assert '<div class="querylens-report">' in output
        assert "Query Analysis Report" in output
        assert "<table" in output

    def test_format_basic_result(self, basic_result: AnalysisResult) -> None:
        formatter = HtmlFormatter()
        output = formatter.format(basic_result)
        assert "Total Queries" in output
        assert "2" in output

    def test_format_with_n_plus_one(self, full_result: AnalysisResult) -> None:
        formatter = HtmlFormatter()
        output = formatter.format(full_result)
        assert "N+1 Detections" in output
        assert "auth_user" in output

    def test_format_with_slow_queries(self, full_result: AnalysisResult) -> None:
        formatter = HtmlFormatter()
        output = formatter.format(full_result)
        assert "Slow Queries" in output
        assert "200.0" in output

    def test_contains_css_styles(self, empty_result: AnalysisResult) -> None:
        formatter = HtmlFormatter()
        output = formatter.format(empty_result)
        assert "<style>" in output
        assert "querylens-report" in output

    def test_html_escapes_sql(self) -> None:
        """SQL with HTML special characters should be escaped."""
        malicious_sql = '<script>alert("xss")</script>'
        result = AnalysisResult(
            total_count=1,
            total_time=100.0,
            slow_queries=[SlowQuery(sql=malicious_sql, time_ms=100.0)],
        )
        formatter = HtmlFormatter()
        output = formatter.format(result)
        assert "<script>" not in output
        assert "&lt;script&gt;" in output

    def test_no_n_plus_one_detail_section_when_empty(
        self, basic_result: AnalysisResult
    ) -> None:
        """The h3 detail section for N+1 should not appear."""
        formatter = HtmlFormatter()
        output = formatter.format(basic_result)
        assert "<h3>N+1 Detections</h3>" not in output

    def test_no_slow_queries_detail_section_when_empty(
        self, basic_result: AnalysisResult
    ) -> None:
        """The h3 detail section for slow queries should not appear."""
        formatter = HtmlFormatter()
        output = formatter.format(basic_result)
        assert "<h3>Slow Queries</h3>" not in output

    def test_badge_css_classes(self, full_result: AnalysisResult) -> None:
        formatter = HtmlFormatter()
        output = formatter.format(full_result)
        assert "querylens-badge-error" in output
        assert "querylens-badge-warn" in output


# ---------------------------------------------------------------------------
# get_formatter factory
# ---------------------------------------------------------------------------


class TestGetFormatter:
    """Tests for the formatter factory function."""

    def test_returns_terminal_formatter(self) -> None:
        formatter = get_formatter("terminal")
        assert isinstance(formatter, TerminalFormatter)

    def test_returns_html_formatter(self) -> None:
        formatter = get_formatter("html")
        assert isinstance(formatter, HtmlFormatter)

    def test_case_insensitive(self) -> None:
        formatter = get_formatter("TERMINAL")
        assert isinstance(formatter, TerminalFormatter)

    def test_strips_whitespace(self) -> None:
        formatter = get_formatter("  html  ")
        assert isinstance(formatter, HtmlFormatter)

    def test_raises_on_unknown_type(self) -> None:
        with pytest.raises(ValueError, match="Unknown output type"):
            get_formatter("pdf")

    def test_reads_from_settings(self, settings: object) -> None:
        settings.QUERYLENS = {"OUTPUT": "html"}  # type: ignore[attr-defined]
        formatter = get_formatter()
        assert isinstance(formatter, HtmlFormatter)

    def test_defaults_to_terminal(self, settings: object) -> None:
        settings.QUERYLENS = {}  # type: ignore[attr-defined]
        formatter = get_formatter()
        assert isinstance(formatter, TerminalFormatter)
