"""Tests for django_querylens.decorators module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import RequestFactory

from django_querylens.analyzer import AnalysisResult
from django_querylens.decorators import _default_output, _should_sample, explain_query


# ---------------------------------------------------------------------------
# _should_sample
# ---------------------------------------------------------------------------


class TestShouldSample:
    """Tests for the sampling helper."""

    def test_always_samples_at_rate_1(self, settings: object) -> None:
        settings.QUERYLENS = {"SAMPLE_RATE": 1.0}  # type: ignore[attr-defined]
        assert _should_sample() is True

    def test_never_samples_at_rate_0(self, settings: object) -> None:
        settings.QUERYLENS = {"SAMPLE_RATE": 0.0}  # type: ignore[attr-defined]
        assert _should_sample() is False

    @patch("django_querylens.decorators.random.random", return_value=0.3)
    def test_samples_below_rate(
        self, mock_random: MagicMock, settings: object
    ) -> None:
        settings.QUERYLENS = {"SAMPLE_RATE": 0.5}  # type: ignore[attr-defined]
        assert _should_sample() is True

    @patch("django_querylens.decorators.random.random", return_value=0.8)
    def test_skips_above_rate(
        self, mock_random: MagicMock, settings: object
    ) -> None:
        settings.QUERYLENS = {"SAMPLE_RATE": 0.5}  # type: ignore[attr-defined]
        assert _should_sample() is False

    def test_defaults_to_rate_1(self, settings: object) -> None:
        settings.QUERYLENS = {}  # type: ignore[attr-defined]
        assert _should_sample() is True


# ---------------------------------------------------------------------------
# _default_output
# ---------------------------------------------------------------------------


class TestDefaultOutput:
    """Tests for the default logging output function."""

    def test_logs_basic_summary(self, caplog: pytest.LogCaptureFixture) -> None:
        result = AnalysisResult(
            queries=[{"sql": "SELECT 1", "time": "0.001"}],
            total_count=1,
            total_time=1.0,
        )
        with caplog.at_level("INFO", logger="django_querylens.decorators"):
            _default_output(result, "my_view")

        assert "my_view" in caplog.text
        assert "1 query" in caplog.text

    def test_logs_plural_queries(self, caplog: pytest.LogCaptureFixture) -> None:
        result = AnalysisResult(total_count=5, total_time=50.0)
        with caplog.at_level("INFO", logger="django_querylens.decorators"):
            _default_output(result, "my_view")

        assert "5 queries" in caplog.text

    def test_logs_n_plus_one_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from django_querylens.analyzer import N1Detection

        result = AnalysisResult(
            total_count=5,
            total_time=5.0,
            has_n_plus_one=True,
            n_plus_one_detected=[N1Detection(table="auth_user", count=5)],
        )
        with caplog.at_level("WARNING", logger="django_querylens.decorators"):
            _default_output(result, "my_view")

        assert "N+1" in caplog.text
        assert "auth_user" in caplog.text


# ---------------------------------------------------------------------------
# explain_query decorator
# ---------------------------------------------------------------------------


class TestExplainQuery:
    """Tests for the @explain_query decorator."""

    @pytest.mark.django_db
    def test_decorator_bare_usage(self, settings: object) -> None:
        """@explain_query without parentheses."""
        settings.QUERYLENS = {"ENABLED": True, "SAMPLE_RATE": 1.0}  # type: ignore[attr-defined]

        @explain_query
        def my_view(request: object) -> str:
            list(User.objects.all())
            return "ok"

        rf = RequestFactory()
        result = my_view(rf.get("/test/"))
        assert result == "ok"

    @pytest.mark.django_db
    def test_decorator_factory_usage(self, settings: object) -> None:
        """@explain_query(output_fn=...) with custom output."""
        settings.QUERYLENS = {"ENABLED": True, "SAMPLE_RATE": 1.0}  # type: ignore[attr-defined]
        captured_results: list[AnalysisResult] = []

        def custom_output(result: AnalysisResult, view_name: str) -> None:
            captured_results.append(result)

        @explain_query(output_fn=custom_output)
        def my_view(request: object) -> str:
            list(User.objects.all())
            return "ok"

        rf = RequestFactory()
        result = my_view(rf.get("/test/"))
        assert result == "ok"
        assert len(captured_results) == 1
        assert captured_results[0].total_count >= 1

    @pytest.mark.django_db
    def test_works_even_when_disabled(self, settings: object) -> None:
        """@explain_query should work even when ENABLED=False."""
        settings.QUERYLENS = {"ENABLED": False, "SAMPLE_RATE": 1.0}  # type: ignore[attr-defined]
        captured: list[AnalysisResult] = []

        def custom_output(result: AnalysisResult, view_name: str) -> None:
            captured.append(result)

        @explain_query(output_fn=custom_output)
        def my_view(request: object) -> str:
            User.objects.count()
            return "ok"

        rf = RequestFactory()
        result = my_view(rf.get("/test/"))
        assert result == "ok"
        assert len(captured) == 1
        assert captured[0].total_count >= 1

    @pytest.mark.django_db
    def test_preserves_function_name(self, settings: object) -> None:
        settings.QUERYLENS = {"ENABLED": True, "SAMPLE_RATE": 1.0}  # type: ignore[attr-defined]

        @explain_query
        def my_awesome_view(request: object) -> str:
            return "ok"

        assert my_awesome_view.__name__ == "my_awesome_view"

    @pytest.mark.django_db
    @patch("django_querylens.decorators._should_sample", return_value=False)
    def test_skips_when_sampling_misses(
        self, mock_sample: MagicMock, settings: object
    ) -> None:
        """When sample rate excludes the request, no capture should occur."""
        settings.QUERYLENS = {"ENABLED": True, "SAMPLE_RATE": 0.5}  # type: ignore[attr-defined]
        captured: list[AnalysisResult] = []

        def custom_output(result: AnalysisResult, view_name: str) -> None:
            captured.append(result)

        @explain_query(output_fn=custom_output)
        def my_view(request: object) -> str:
            return "ok"

        rf = RequestFactory()
        result = my_view(rf.get("/test/"))
        assert result == "ok"
        assert len(captured) == 0  # output_fn was never called

    @pytest.mark.django_db
    def test_output_fn_exception_suppressed(
        self, settings: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If output_fn raises, it should be suppressed, not crash the view."""
        settings.QUERYLENS = {"ENABLED": True, "SAMPLE_RATE": 1.0}  # type: ignore[attr-defined]

        def bad_output(result: AnalysisResult, view_name: str) -> None:
            raise RuntimeError("output exploded")

        @explain_query(output_fn=bad_output)
        def my_view(request: object) -> str:
            return "ok"

        rf = RequestFactory()
        with caplog.at_level("ERROR", logger="django_querylens.decorators"):
            result = my_view(rf.get("/test/"))

        assert result == "ok"  # View response not affected
        assert "output_fn raised" in caplog.text

    @pytest.mark.django_db
    def test_works_with_http_response(self, settings: object) -> None:
        """Ensure it works with actual Django HttpResponse objects."""
        settings.QUERYLENS = {"ENABLED": True, "SAMPLE_RATE": 1.0}  # type: ignore[attr-defined]

        @explain_query
        def my_view(request: object) -> HttpResponse:
            User.objects.count()
            return HttpResponse("hello")

        rf = RequestFactory()
        response = my_view(rf.get("/test/"))
        assert response.status_code == 200
        assert response.content == b"hello"
