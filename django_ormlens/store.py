"""Thread-safe in-memory ring buffer for storing query analysis reports.

This module provides :class:`ReportStore`, a bounded in-memory store backed by
:class:`collections.deque` that keeps the most recent ``MAX_STORED_REPORTS``
analysis results.  A module-level singleton is exposed via :func:`get_store`.

All public methods are protected by a :class:`threading.Lock` so the store is
safe to use from concurrent WSGI/ASGI worker threads.

Typical usage::

    from django_ormlens.store import get_store

    store = get_store()
    store.add(report)
    recent = store.get_all()  # newest-first
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from django_ormlens.analyzer import AnalysisResult, get_ormlens_setting


@dataclass
class StoredReport:
    """A single stored analysis report.

    Attributes:
        id: Unique hex identifier (UUID4).
        timestamp: UTC datetime when the report was created.
        path: The HTTP request path (e.g. ``"/api/users/"``).
        method: The HTTP request method (e.g. ``"GET"``).
        result: The analysis result from the capture session.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    path: str = ""
    method: str = ""
    result: AnalysisResult = field(default_factory=AnalysisResult)


class ReportStore:
    """Thread-safe bounded in-memory store for :class:`StoredReport` instances.

    Uses a :class:`collections.deque` with ``maxlen`` set to
    ``ORMLENS["MAX_STORED_REPORTS"]`` to automatically evict the oldest
    reports when the buffer is full.

    Example::

        store = ReportStore()
        store.add(StoredReport(path="/api/", method="GET", result=result))
        all_reports = store.get_all()  # newest-first
    """

    def __init__(self) -> None:
        max_size: int = int(get_ormlens_setting("MAX_STORED_REPORTS", 1000))
        self._lock = threading.Lock()
        self._reports: deque[StoredReport] = deque(maxlen=max_size)

    def add(self, report: StoredReport) -> None:
        """Add a report to the store.

        If the store is at capacity, the oldest report is automatically evicted.

        Args:
            report: The report to store.
        """
        with self._lock:
            self._reports.append(report)

    def get_all(self) -> list[StoredReport]:
        """Return all stored reports, newest first.

        Returns:
            A list of :class:`StoredReport` instances ordered newest-first.
        """
        with self._lock:
            return list(reversed(self._reports))

    def get_by_id(self, report_id: str) -> StoredReport | None:
        """Look up a single report by its ID.

        Args:
            report_id: The hex UUID of the report.

        Returns:
            The matching :class:`StoredReport`, or ``None`` if not found.
        """
        with self._lock:
            for report in self._reports:
                if report.id == report_id:
                    return report
            return None

    def clear(self) -> None:
        """Remove all reports from the store."""
        with self._lock:
            self._reports.clear()

    @property
    def count(self) -> int:
        """Return the number of stored reports."""
        with self._lock:
            return len(self._reports)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: ReportStore | None = None
_store_lock = threading.Lock()


def get_store() -> ReportStore:
    """Return the module-level :class:`ReportStore` singleton.

    Creates the instance on first call.  Thread-safe.

    Returns:
        The shared :class:`ReportStore` instance.
    """
    global _store  # noqa: PLW0603
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ReportStore()
    return _store
