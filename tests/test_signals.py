"""Tests for django_ormlens.signals module."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from django_ormlens.signals import (
    _is_enabled,
    _local,
    _reset_local,
    _should_sample,
    on_request_finished,
    on_request_started,
)


# ---------------------------------------------------------------------------
# _is_enabled
# ---------------------------------------------------------------------------


class TestIsEnabled:
    """Tests for the signal-level enabled check."""

    def test_enabled_by_default(self) -> None:
        assert _is_enabled() is True

    def test_disabled_when_setting_false(self, settings: object) -> None:
        settings.ORMLENS = {"ENABLED": False}  # type: ignore[attr-defined]
        assert _is_enabled() is False


# ---------------------------------------------------------------------------
# _should_sample
# ---------------------------------------------------------------------------


class TestShouldSample:
    """Tests for signal-level sampling."""

    def test_always_at_rate_1(self, settings: object) -> None:
        settings.ORMLENS = {"SAMPLE_RATE": 1.0}  # type: ignore[attr-defined]
        assert _should_sample() is True

    def test_never_at_rate_0(self, settings: object) -> None:
        settings.ORMLENS = {"SAMPLE_RATE": 0.0}  # type: ignore[attr-defined]
        assert _should_sample() is False


# ---------------------------------------------------------------------------
# _reset_local
# ---------------------------------------------------------------------------


class TestResetLocal:
    """Tests for thread-local state cleanup."""

    def test_clears_all_attributes(self) -> None:
        _local.active = True
        _local.analyzer = "fake"
        _local.result = "fake"

        _reset_local()

        assert _local.active is False
        assert _local.analyzer is None
        assert _local.result is None


# ---------------------------------------------------------------------------
# on_request_started
# ---------------------------------------------------------------------------


class TestOnRequestStarted:
    """Tests for the request_started signal handler."""

    def test_noop_when_disabled(self, settings: object) -> None:
        settings.ORMLENS = {"ENABLED": False}  # type: ignore[attr-defined]
        _reset_local()

        on_request_started(sender=None)

        assert getattr(_local, "active", False) is False

    def test_noop_when_sampling_skipped(self, settings: object) -> None:
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "ENABLED": True,
            "SAMPLE_RATE": 0.0,
        }
        _reset_local()

        on_request_started(sender=None)

        assert getattr(_local, "active", False) is False

    @pytest.mark.django_db
    def test_starts_capture_when_enabled(self, settings: object) -> None:
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "ENABLED": True,
            "SAMPLE_RATE": 1.0,
        }
        _reset_local()

        on_request_started(sender=None)

        assert getattr(_local, "active", False) is True
        assert getattr(_local, "result", None) is not None
        assert getattr(_local, "_ctx_manager", None) is not None

        # Clean up the context manager so it doesn't leak
        ctx = getattr(_local, "_ctx_manager", None)
        if ctx is not None:
            ctx.__exit__(None, None, None)
        _reset_local()


# ---------------------------------------------------------------------------
# on_request_finished
# ---------------------------------------------------------------------------


class TestOnRequestFinished:
    """Tests for the request_finished signal handler."""

    def test_noop_when_not_active(self) -> None:
        _reset_local()
        # Should not raise
        on_request_finished(sender=None)

    @pytest.mark.django_db
    def test_finalizes_capture(self, settings: object) -> None:
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "ENABLED": True,
            "SAMPLE_RATE": 1.0,
        }
        _reset_local()

        # Start a capture
        on_request_started(sender=None)
        assert _local.active is True

        # Finish it
        on_request_finished(sender=None)
        assert _local.active is False

    @pytest.mark.django_db
    def test_resets_state_after_finish(self, settings: object) -> None:
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "ENABLED": True,
            "SAMPLE_RATE": 1.0,
        }
        _reset_local()

        on_request_started(sender=None)
        on_request_finished(sender=None)

        assert _local.active is False
        assert _local.analyzer is None
        assert _local.result is None


# ---------------------------------------------------------------------------
# Integration: start + finish cycle
# ---------------------------------------------------------------------------


class TestSignalIntegration:
    """Integration tests for the full request lifecycle via signals."""

    @pytest.mark.django_db
    def test_full_request_lifecycle(
        self,
        settings: object,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Simulate a full request lifecycle through signals."""
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "ENABLED": True,
            "SAMPLE_RATE": 1.0,
        }
        _reset_local()

        with caplog.at_level("DEBUG", logger="django_ormlens"):
            on_request_started(sender=None)
            # Simulate some query activity
            from django.contrib.auth.models import User

            User.objects.count()
            on_request_finished(sender=None)

        # State should be cleaned up
        assert _local.active is False

    @pytest.mark.django_db
    @patch("django_ormlens.signals._should_sample", return_value=False)
    def test_no_capture_when_not_sampled(
        self, mock_sample: object, settings: object
    ) -> None:
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "ENABLED": True,
            "SAMPLE_RATE": 0.5,
        }
        _reset_local()

        on_request_started(sender=None)
        assert _local.active is False

        on_request_finished(sender=None)
        assert _local.active is False
