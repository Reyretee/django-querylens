"""Tests for django_ormlens.store module."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from django_ormlens.analyzer import AnalysisResult
from django_ormlens.store import ReportStore, StoredReport, get_store

# ---------------------------------------------------------------------------
# StoredReport dataclass
# ---------------------------------------------------------------------------


class TestStoredReport:
    """Tests for the StoredReport dataclass."""

    def test_default_fields(self) -> None:
        report = StoredReport()
        assert isinstance(report.id, str)
        assert len(report.id) == 32  # uuid4 hex
        assert isinstance(report.timestamp, datetime)
        assert report.path == ""
        assert report.method == ""
        assert isinstance(report.result, AnalysisResult)

    def test_custom_fields(self) -> None:
        result = AnalysisResult(total_count=5, total_time=42.0)
        report = StoredReport(
            id="abc123",
            path="/api/users/",
            method="GET",
            result=result,
        )
        assert report.id == "abc123"
        assert report.path == "/api/users/"
        assert report.method == "GET"
        assert report.result.total_count == 5

    def test_timestamp_is_utc(self) -> None:
        report = StoredReport()
        assert report.timestamp.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# ReportStore
# ---------------------------------------------------------------------------


class TestReportStore:
    """Tests for the ReportStore class."""

    @pytest.fixture(autouse=True)
    def _small_store(self, settings: object) -> None:
        """Configure a small store for testing."""
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "MAX_STORED_REPORTS": 5,
        }

    def _make_report(self, path: str = "/test/") -> StoredReport:
        return StoredReport(
            path=path,
            method="GET",
            result=AnalysisResult(total_count=1),
        )

    def test_add_and_get_all(self) -> None:
        store = ReportStore()
        r1 = self._make_report("/first/")
        r2 = self._make_report("/second/")
        store.add(r1)
        store.add(r2)

        reports = store.get_all()
        assert len(reports) == 2
        # Newest first
        assert reports[0].path == "/second/"
        assert reports[1].path == "/first/"

    def test_newest_first_ordering(self) -> None:
        store = ReportStore()
        for i in range(3):
            store.add(self._make_report(f"/path/{i}/"))

        reports = store.get_all()
        assert reports[0].path == "/path/2/"
        assert reports[1].path == "/path/1/"
        assert reports[2].path == "/path/0/"

    def test_ring_buffer_eviction(self) -> None:
        store = ReportStore()
        # Add 7 items to a buffer with maxlen=5
        for i in range(7):
            store.add(self._make_report(f"/path/{i}/"))

        reports = store.get_all()
        assert len(reports) == 5
        # Oldest two (0, 1) should be evicted
        paths = [r.path for r in reports]
        assert "/path/0/" not in paths
        assert "/path/1/" not in paths
        # Newest should be first
        assert reports[0].path == "/path/6/"

    def test_get_by_id_found(self) -> None:
        store = ReportStore()
        report = self._make_report()
        store.add(report)

        found = store.get_by_id(report.id)
        assert found is not None
        assert found.id == report.id
        assert found.path == report.path

    def test_get_by_id_not_found(self) -> None:
        store = ReportStore()
        store.add(self._make_report())

        assert store.get_by_id("nonexistent") is None

    def test_clear(self) -> None:
        store = ReportStore()
        store.add(self._make_report())
        store.add(self._make_report())
        assert store.count == 2

        store.clear()
        assert store.count == 0
        assert store.get_all() == []

    def test_count_property(self) -> None:
        store = ReportStore()
        assert store.count == 0

        store.add(self._make_report())
        assert store.count == 1

        store.add(self._make_report())
        assert store.count == 2

    def test_thread_safety(self) -> None:
        store = ReportStore()

        def add_reports(n: int) -> None:
            for i in range(n):
                store.add(self._make_report(f"/thread/{i}/"))

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(add_reports, 3) for _ in range(4)]
            for f in futures:
                f.result()

        # 4 threads * 3 reports = 12, but maxlen=5
        assert store.count == 5


# ---------------------------------------------------------------------------
# get_store singleton
# ---------------------------------------------------------------------------


class TestGetStore:
    """Tests for the module-level singleton."""

    def test_returns_report_store(self) -> None:
        store = get_store()
        assert isinstance(store, ReportStore)

    def test_returns_same_instance(self) -> None:
        s1 = get_store()
        s2 = get_store()
        assert s1 is s2
