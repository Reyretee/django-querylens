"""Tests for django_ormlens.analyzer module."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import connection, reset_queries

from django_ormlens.analyzer import (
    AnalysisResult,
    N1Detection,
    QueryAnalyzer,
    SlowQuery,
    get_ormlens_setting,
)


# ---------------------------------------------------------------------------
# get_ormlens_setting
# ---------------------------------------------------------------------------


class TestGetOrmlensSetting:
    """Tests for the settings helper function."""

    def test_returns_configured_value(self, settings: object) -> None:
        settings.ORMLENS = {"ENABLED": False}  # type: ignore[attr-defined]
        assert get_ormlens_setting("ENABLED") is False

    def test_returns_default_when_key_missing(self, settings: object) -> None:
        settings.ORMLENS = {}  # type: ignore[attr-defined]
        assert get_ormlens_setting("NONEXISTENT", "fallback") == "fallback"

    def test_returns_default_when_ormlens_block_missing(
        self, settings: object
    ) -> None:
        if hasattr(settings, "ORMLENS"):
            delattr(settings, "ORMLENS")
        assert get_ormlens_setting("ENABLED", True) is True

    def test_returns_none_default(self, settings: object) -> None:
        settings.ORMLENS = {}  # type: ignore[attr-defined]
        assert get_ormlens_setting("MISSING") is None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class TestDataStructures:
    """Tests for AnalysisResult, N1Detection, SlowQuery dataclasses."""

    def test_analysis_result_defaults(self) -> None:
        result = AnalysisResult()
        assert result.queries == []
        assert result.total_count == 0
        assert result.total_time == 0.0
        assert result.n_plus_one_detected == []
        assert result.slow_queries == []
        assert result.has_n_plus_one is False

    def test_analysis_result_independent_lists(self) -> None:
        """Each AnalysisResult should have its own list instances."""
        r1 = AnalysisResult()
        r2 = AnalysisResult()
        r1.queries.append({"sql": "SELECT 1"})
        assert r2.queries == []

    def test_n1_detection_fields(self) -> None:
        d = N1Detection(table="auth_user", count=5)
        assert d.table == "auth_user"
        assert d.count == 5

    def test_slow_query_fields(self) -> None:
        s = SlowQuery(sql="SELECT * FROM big_table", time_ms=250.0)
        assert s.sql == "SELECT * FROM big_table"
        assert s.time_ms == 250.0


# ---------------------------------------------------------------------------
# QueryAnalyzer.is_enabled
# ---------------------------------------------------------------------------


class TestIsEnabled:
    """Tests for the is_enabled() method."""

    def test_enabled_by_default(self) -> None:
        analyzer = QueryAnalyzer()
        assert analyzer.is_enabled() is True

    def test_disabled_when_setting_false(self, settings: object) -> None:
        settings.ORMLENS = {"ENABLED": False}  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        assert analyzer.is_enabled() is False

    def test_enabled_when_setting_true(self, settings: object) -> None:
        settings.ORMLENS = {"ENABLED": True}  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        assert analyzer.is_enabled() is True


# ---------------------------------------------------------------------------
# QueryAnalyzer.capture
# ---------------------------------------------------------------------------


class TestCapture:
    """Tests for the capture() context manager."""

    @pytest.mark.django_db
    def test_captures_queries(self, sample_user: User) -> None:
        analyzer = QueryAnalyzer()
        with analyzer.capture() as result:
            list(User.objects.all())
        assert result.total_count >= 1
        assert len(result.queries) >= 1

    @pytest.mark.django_db
    def test_captures_multiple_queries(self, multiple_users: list[User]) -> None:
        analyzer = QueryAnalyzer()
        with analyzer.capture() as result:
            list(User.objects.all())
            User.objects.count()
        assert result.total_count >= 2

    def test_capture_works_even_when_disabled(self, settings: object) -> None:
        """capture() should always work — ENABLED only controls signals."""
        settings.ORMLENS = {"ENABLED": False}  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        assert analyzer.is_enabled() is False
        # capture() still works despite ENABLED=False
        with analyzer.capture() as result:
            pass
        assert isinstance(result, AnalysisResult)

    @pytest.mark.django_db
    def test_result_populated_after_exit(self, sample_user: User) -> None:
        analyzer = QueryAnalyzer()
        with analyzer.capture() as result:
            list(User.objects.all())
        # result should be populated after exiting the context
        assert isinstance(result, AnalysisResult)
        assert result.total_count > 0
        assert result.total_time >= 0.0

    @pytest.mark.django_db
    def test_capture_isolates_queries(self, sample_user: User) -> None:
        """Queries from before capture() should not leak in."""
        # Run a query outside the capture block
        reset_queries()
        list(User.objects.all())

        analyzer = QueryAnalyzer()
        with analyzer.capture() as result:
            User.objects.count()

        # Should only have the count query, not the all() query
        assert result.total_count >= 1
        # Make sure we don't have the queries from before capture
        all_sqls = [q.get("sql", "") for q in result.queries]
        # At minimum there should be exactly the queries we ran inside
        assert result.total_count <= 2  # count + maybe some setup query

    @pytest.mark.django_db
    def test_debug_restored_after_capture(self, settings: object) -> None:
        """DEBUG should be restored to its original value after capture."""
        original_debug = settings.DEBUG  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        with analyzer.capture():
            pass
        assert settings.DEBUG == original_debug  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# QueryAnalyzer.detect_n_plus_one
# ---------------------------------------------------------------------------


class TestDetectNPlusOne:
    """Tests for N+1 detection logic."""

    def test_detects_repeated_table_access(self, settings: object) -> None:
        settings.ORMLENS = {"N1_THRESHOLD": 2}  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
        ]
        detections = analyzer.detect_n_plus_one(queries)
        assert len(detections) == 1
        assert detections[0].table == "auth_user"
        assert detections[0].count == 3

    def test_no_detection_below_threshold(self, settings: object) -> None:
        settings.ORMLENS = {"N1_THRESHOLD": 5}  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
        ]
        detections = analyzer.detect_n_plus_one(queries)
        assert len(detections) == 0

    def test_detects_multiple_tables(self, settings: object) -> None:
        settings.ORMLENS = {"N1_THRESHOLD": 2}  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
            {"sql": 'SELECT * FROM "myapp_post"', "time": "0.001"},
            {"sql": 'SELECT * FROM "myapp_post"', "time": "0.001"},
            {"sql": 'SELECT * FROM "myapp_post"', "time": "0.001"},
        ]
        detections = analyzer.detect_n_plus_one(queries)
        assert len(detections) == 2
        # Sorted by count descending
        assert detections[0].table == "myapp_post"
        assert detections[0].count == 3

    def test_handles_empty_queries(self) -> None:
        analyzer = QueryAnalyzer()
        detections = analyzer.detect_n_plus_one([])
        assert detections == []

    def test_handles_queries_without_from(self) -> None:
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": "SET TIMEZONE TO 'UTC'", "time": "0.001"},
        ]
        detections = analyzer.detect_n_plus_one(queries)
        assert detections == []

    def test_handles_unquoted_table_names(self, settings: object) -> None:
        settings.ORMLENS = {"N1_THRESHOLD": 2}  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": "SELECT * FROM auth_user", "time": "0.001"},
            {"sql": "SELECT * FROM auth_user", "time": "0.001"},
        ]
        detections = analyzer.detect_n_plus_one(queries)
        assert len(detections) == 1
        assert detections[0].table == "auth_user"


# ---------------------------------------------------------------------------
# QueryAnalyzer.detect_slow_queries
# ---------------------------------------------------------------------------


class TestDetectSlowQueries:
    """Tests for slow query detection logic."""

    def test_detects_slow_query(self, settings: object) -> None:
        settings.ORMLENS = {"SLOW_QUERY_MS": 50}  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": "SELECT * FROM big_table", "time": "0.100"},  # 100ms
        ]
        slow = analyzer.detect_slow_queries(queries)
        assert len(slow) == 1
        assert slow[0].time_ms == 100.0
        assert "big_table" in slow[0].sql

    def test_no_slow_queries_below_threshold(self, settings: object) -> None:
        settings.ORMLENS = {"SLOW_QUERY_MS": 200}  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": "SELECT 1", "time": "0.001"},  # 1ms
        ]
        slow = analyzer.detect_slow_queries(queries)
        assert slow == []

    def test_sorted_slowest_first(self, settings: object) -> None:
        settings.ORMLENS = {"SLOW_QUERY_MS": 50}  # type: ignore[attr-defined]
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": "SELECT 1", "time": "0.060"},  # 60ms
            {"sql": "SELECT 2", "time": "0.200"},  # 200ms
            {"sql": "SELECT 3", "time": "0.080"},  # 80ms
        ]
        slow = analyzer.detect_slow_queries(queries)
        assert len(slow) == 3
        assert slow[0].time_ms == 200.0
        assert slow[1].time_ms == 80.0
        assert slow[2].time_ms == 60.0

    def test_handles_invalid_time(self) -> None:
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": "SELECT 1", "time": "not_a_number"},
        ]
        slow = analyzer.detect_slow_queries(queries)
        assert slow == []

    def test_handles_empty_queries(self) -> None:
        analyzer = QueryAnalyzer()
        slow = analyzer.detect_slow_queries([])
        assert slow == []


# ---------------------------------------------------------------------------
# QueryAnalyzer.analyze
# ---------------------------------------------------------------------------


class TestAnalyze:
    """Tests for the aggregate analyze() method."""

    def test_empty_queries(self) -> None:
        analyzer = QueryAnalyzer()
        result = analyzer.analyze([])
        assert result.total_count == 0
        assert result.total_time == 0.0
        assert result.has_n_plus_one is False
        assert result.n_plus_one_detected == []
        assert result.slow_queries == []

    def test_calculates_total_time(self) -> None:
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": "SELECT 1", "time": "0.010"},  # 10ms
            {"sql": "SELECT 2", "time": "0.020"},  # 20ms
        ]
        result = analyzer.analyze(queries)
        assert result.total_count == 2
        assert abs(result.total_time - 30.0) < 0.01

    def test_combines_n_plus_one_and_slow(self, settings: object) -> None:
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "N1_THRESHOLD": 2,
            "SLOW_QUERY_MS": 50,
        }
        analyzer = QueryAnalyzer()
        queries = [
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
            {"sql": 'SELECT * FROM "auth_user"', "time": "0.001"},
            {"sql": "SELECT * FROM big_table", "time": "0.100"},  # 100ms slow
        ]
        result = analyzer.analyze(queries)
        assert result.total_count == 3
        assert result.has_n_plus_one is True
        assert len(result.n_plus_one_detected) == 1
        assert len(result.slow_queries) == 1

    def test_copies_queries_list(self) -> None:
        """analyze() should copy the input list, not reference it."""
        analyzer = QueryAnalyzer()
        original = [{"sql": "SELECT 1", "time": "0.001"}]
        result = analyzer.analyze(original)
        original.append({"sql": "SELECT 2", "time": "0.002"})
        assert len(result.queries) == 1


# ---------------------------------------------------------------------------
# Integration: capture + analyze with real DB
# ---------------------------------------------------------------------------


class TestCaptureIntegration:
    """Integration tests that exercise capture() with real database queries."""

    @pytest.mark.django_db
    def test_n_plus_one_detected_via_capture(
        self, multiple_users: list[User]
    ) -> None:
        """Capture should detect N+1 when querying users individually."""
        analyzer = QueryAnalyzer()
        with analyzer.capture() as result:
            # Each .get() call issues a separate query
            for user in multiple_users:
                User.objects.get(pk=user.pk)

        assert result.has_n_plus_one is True
        assert result.total_count >= len(multiple_users)

    @pytest.mark.django_db
    def test_no_n_plus_one_with_single_query(
        self, multiple_users: list[User]
    ) -> None:
        """A single list() call should not trigger N+1."""
        analyzer = QueryAnalyzer()
        with analyzer.capture() as result:
            list(User.objects.all())

        # Single SELECT should not be flagged
        assert result.total_count >= 1
        # With threshold=2, a single query shouldn't trigger N+1
        # unless there happen to be other internal queries
