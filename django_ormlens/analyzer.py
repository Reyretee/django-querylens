"""Core query analysis engine for django-ormlens.

This module provides the ``QueryAnalyzer`` class and supporting data
structures that capture Django ORM queries, detect N+1 patterns, and
identify slow queries — all in a thread-safe manner.

Typical usage::

    from django_ormlens.analyzer import QueryAnalyzer

    analyzer = QueryAnalyzer()
    with analyzer.capture() as result:
        list(MyModel.objects.all())

    if result.has_n_plus_one:
        # result.n_plus_one_detected contains culprit tables
        ...
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import connection, reset_queries

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings helper
# ---------------------------------------------------------------------------


def get_ormlens_setting(key: str, default: Any = None) -> Any:
    """Retrieve a value from the ``ORMLENS`` settings dictionary.

    Falls back to *default* when the key is absent or the ``ORMLENS``
    setting block itself has not been defined.

    Args:
        key: The setting key to look up (e.g. ``"ENABLED"``).
        default: Value returned when *key* is not present.

    Returns:
        The configured value, or *default*.

    Example:
        >>> get_ormlens_setting("N1_THRESHOLD", 3)
        3
    """
    ormlens_config: dict[str, Any] = getattr(settings, "ORMLENS", {})
    return ormlens_config.get(key, default)


# ---------------------------------------------------------------------------
# Default settings reference (documentation only — not imported at runtime)
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS: dict[str, Any] = {
    "ENABLED": True,
    "SAMPLE_RATE": 1.0,
    "N1_THRESHOLD": 3,
    "SLOW_QUERY_MS": 100,
    "OUTPUT": "terminal",
    "MAX_STORED_REPORTS": 1000,
    "PANEL": False,
}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class N1Detection:
    """A single N+1 detection result.

    Attributes:
        table: Database table name where repeated queries were observed.
        count: Number of queries executed against this table.
    """

    table: str
    count: int


@dataclass
class SlowQuery:
    """A single slow query detection result.

    Attributes:
        sql: The raw SQL string of the slow query.
        time_ms: Execution time in milliseconds.
    """

    sql: str
    time_ms: float


@dataclass
class AnalysisResult:
    """Aggregated result from a single ``QueryAnalyzer.capture()`` session.

    Attributes:
        queries: Raw list of query dicts as returned by ``connection.queries``.
            Each dict contains at minimum ``"sql"`` and ``"time"`` keys.
        total_count: Total number of queries executed in the captured block.
        total_time: Sum of all query execution times in milliseconds.
        n_plus_one_detected: List of :class:`N1Detection` entries for tables
            that exceeded the configured N+1 threshold.
        slow_queries: List of :class:`SlowQuery` entries for queries that
            exceeded the configured slow-query threshold.
        has_n_plus_one: ``True`` if at least one N+1 pattern was found.
    """

    queries: list[dict[str, str]] = field(default_factory=list)
    total_count: int = 0
    total_time: float = 0.0
    n_plus_one_detected: list[N1Detection] = field(default_factory=list)
    slow_queries: list[SlowQuery] = field(default_factory=list)
    has_n_plus_one: bool = False


# ---------------------------------------------------------------------------
# Thread-local storage
# ---------------------------------------------------------------------------

_thread_local = threading.local()


# ---------------------------------------------------------------------------
# QueryAnalyzer
# ---------------------------------------------------------------------------

# Pre-compiled regex for extracting the primary table name from a FROM clause.
_FROM_TABLE_RE = re.compile(r'\bFROM\s+"?(\w+)"?', re.IGNORECASE)


class QueryAnalyzer:
    """Captures and analyses Django ORM queries within a code block.

    ``QueryAnalyzer`` is the primary public interface of django-ormlens.
    It wraps Django's built-in ``connection.queries`` list to collect
    executed SQL statements, then runs heuristic detectors for:

    * **N+1 queries** — repeated hits against the same table.
    * **Slow queries** — queries whose execution time exceeds the threshold.

    The class is thread-safe: each thread maintains its own capture state
    via :mod:`threading.local`.

    Settings (configured via ``ORMLENS`` in ``settings.py``):

    +------------------+----------+--------------------------------------------+
    | Key              | Default  | Description                                |
    +==================+==========+============================================+
    | ``ENABLED``      | ``True`` | Master switch; disables all analysis when  |
    |                  |          | ``False``.                                 |
    +------------------+----------+--------------------------------------------+
    | ``N1_THRESHOLD`` | ``3``    | Minimum repeated table hits to flag N+1.   |
    +------------------+----------+--------------------------------------------+
    | ``SLOW_QUERY_MS``| ``100``  | Query duration threshold (ms) for slow     |
    |                  |          | query detection.                           |
    +------------------+----------+--------------------------------------------+

    Example::

        analyzer = QueryAnalyzer()

        with analyzer.capture() as result:
            list(Article.objects.all())
            for article in Article.objects.all():
                _ = article.author.name  # triggers N+1

        print(result.total_count)        # e.g. 7
        print(result.has_n_plus_one)     # True
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Return whether django-ormlens analysis is currently enabled.

        Reads the ``ORMLENS["ENABLED"]`` setting.  When ``False`` the
        :meth:`capture` context manager becomes a no-op.

        Returns:
            ``True`` if analysis is enabled, ``False`` otherwise.

        Example:
            >>> analyzer = QueryAnalyzer()
            >>> analyzer.is_enabled()
            True
        """
        return bool(get_ormlens_setting("ENABLED", True))

    @contextmanager
    def capture(self) -> Generator[AnalysisResult, None, None]:
        """Context manager that captures all SQL queries executed in its block.

        On entry:

        1. Temporarily forces ``settings.DEBUG = True`` when it is not already
           set, because Django only populates ``connection.queries`` in debug
           mode.  The original value is restored on exit.
        2. Calls :func:`django.db.reset_queries` to clear any previously
           accumulated queries.

        On exit the captured queries are analysed and the shared
        :class:`AnalysisResult` object is populated in place so the caller
        can inspect it after the ``with`` block.

        Yields:
            An :class:`AnalysisResult` instance whose attributes are populated
            *after* the ``with`` block exits.

        Note:
            When :meth:`is_enabled` returns ``False`` the context manager
            yields an empty :class:`AnalysisResult` immediately without
            touching ``connection.queries``.

        Example::

            analyzer = QueryAnalyzer()
            with analyzer.capture() as result:
                list(MyModel.objects.filter(active=True))

            print(f"Captured {result.total_count} queries")
        """
        result = AnalysisResult()

        # Store the result object on thread-local so nested helpers can access
        # it without explicit parameter passing.
        _thread_local.current_result = result

        original_debug: bool = settings.DEBUG
        debug_patched = False

        try:
            if not settings.DEBUG:
                settings.DEBUG = True
                debug_patched = True
                logger.debug(
                    "django-ormlens: DEBUG temporarily enabled to allow "
                    "connection.queries capture."
                )

            reset_queries()
            logger.debug("django-ormlens: query capture started.")

            yield result

        finally:
            # Snapshot queries before restoring DEBUG, because Django clears
            # connection.queries when DEBUG is toggled to False.
            raw_queries: list[dict[str, str]] = list(connection.queries)

            if debug_patched:
                settings.DEBUG = original_debug
                logger.debug("django-ormlens: DEBUG restored to %s.", original_debug)

            # Populate the result in-place so the caller's reference is valid.
            populated = self.analyze(raw_queries)
            result.queries = populated.queries
            result.total_count = populated.total_count
            result.total_time = populated.total_time
            result.n_plus_one_detected = populated.n_plus_one_detected
            result.slow_queries = populated.slow_queries
            result.has_n_plus_one = populated.has_n_plus_one

            logger.debug(
                "django-ormlens: capture finished. "
                "total_count=%d total_time=%.3fms n_plus_one=%s",
                result.total_count,
                result.total_time,
                result.has_n_plus_one,
            )

            # Clean up thread-local state.
            if hasattr(_thread_local, "current_result"):
                del _thread_local.current_result

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def detect_n_plus_one(self, queries: list[dict[str, str]]) -> list[N1Detection]:
        """Identify repeated queries against the same database table.

        The detector extracts the primary table name from each query's
        ``FROM`` clause and counts occurrences.  Any table that appears in
        at least ``ORMLENS["N1_THRESHOLD"]`` queries is flagged.

        Args:
            queries: List of query dicts as returned by
                ``django.db.connection.queries``.  Each dict must contain
                at least the ``"sql"`` key.

        Returns:
            A list of :class:`N1Detection` instances, one per table that
            exceeded the threshold, sorted by count descending.

        Example:
            >>> queries = [
            ...     {"sql": 'SELECT * FROM "myapp_post"', "time": "0.001"},
            ...     {"sql": 'SELECT * FROM "myapp_post"', "time": "0.001"},
            ...     {"sql": 'SELECT * FROM "myapp_post"', "time": "0.001"},
            ... ]
            >>> analyzer = QueryAnalyzer()
            >>> detections = analyzer.detect_n_plus_one(queries)
            >>> detections[0].table
            'myapp_post'
        """
        threshold: int = int(get_ormlens_setting("N1_THRESHOLD", 3))
        table_counts: dict[str, int] = {}

        for query in queries:
            sql = query.get("sql", "")
            match = _FROM_TABLE_RE.search(sql)
            if match:
                table = match.group(1)
                table_counts[table] = table_counts.get(table, 0) + 1

        detections = [
            N1Detection(table=table, count=count)
            for table, count in table_counts.items()
            if count >= threshold
        ]
        # Sort most-frequent first so the caller sees the worst offender at [0].
        detections.sort(key=lambda d: d.count, reverse=True)

        if detections:
            logger.warning(
                "django-ormlens: N+1 detected on table(s): %s",
                ", ".join(f"{d.table} ({d.count}x)" for d in detections),
            )

        return detections

    def detect_slow_queries(self, queries: list[dict[str, str]]) -> list[SlowQuery]:
        """Identify queries whose execution time exceeds the slow-query threshold.

        Args:
            queries: List of query dicts from ``connection.queries``.  Each
                dict must contain ``"sql"`` and ``"time"`` keys.  The
                ``"time"`` value is a string representing seconds
                (e.g. ``"0.123"``).

        Returns:
            A list of :class:`SlowQuery` instances for every query that
            exceeded ``ORMLENS["SLOW_QUERY_MS"]`` milliseconds, sorted
            slowest-first.

        Example:
            >>> queries = [{"sql": "SELECT 1", "time": "0.200"}]
            >>> analyzer = QueryAnalyzer()
            >>> slow = analyzer.detect_slow_queries(queries)
            >>> slow[0].time_ms
            200.0
        """
        threshold_ms: float = float(get_ormlens_setting("SLOW_QUERY_MS", 100))
        slow: list[SlowQuery] = []

        for query in queries:
            sql = query.get("sql", "")
            raw_time = query.get("time", "0")
            try:
                time_ms = float(raw_time) * 1000.0
            except (ValueError, TypeError):
                logger.debug(
                    "django-ormlens: could not parse query time %r; skipping.",
                    raw_time,
                )
                continue

            if time_ms >= threshold_ms:
                slow.append(SlowQuery(sql=sql, time_ms=time_ms))
                logger.warning(
                    "django-ormlens: slow query detected (%.1fms): %.120s",
                    time_ms,
                    sql,
                )

        slow.sort(key=lambda q: q.time_ms, reverse=True)
        return slow

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def analyze(self, queries: list[dict[str, str]]) -> AnalysisResult:
        """Run all detectors over a list of queries and return an AnalysisResult.

        This is the primary aggregation entry point.  It calls
        :meth:`detect_n_plus_one` and :meth:`detect_slow_queries`
        internally and computes summary statistics.

        Args:
            queries: List of query dicts from ``connection.queries``.

        Returns:
            A fully populated :class:`AnalysisResult` with:

            * ``queries`` — the raw input list.
            * ``total_count`` — number of queries.
            * ``total_time`` — sum of execution times in milliseconds.
            * ``n_plus_one_detected`` — detections from
              :meth:`detect_n_plus_one`.
            * ``slow_queries`` — detections from
              :meth:`detect_slow_queries`.
            * ``has_n_plus_one`` — ``True`` when at least one N+1 was found.

        Example:
            >>> from django_ormlens.analyzer import QueryAnalyzer
            >>> analyzer = QueryAnalyzer()
            >>> result = analyzer.analyze([])
            >>> result.total_count
            0
        """
        n_plus_one = self.detect_n_plus_one(queries)
        slow = self.detect_slow_queries(queries)

        total_time: float = 0.0
        for query in queries:
            raw_time = query.get("time", "0")
            try:
                total_time += float(raw_time) * 1000.0
            except (ValueError, TypeError):
                pass

        return AnalysisResult(
            queries=list(queries),
            total_count=len(queries),
            total_time=total_time,
            n_plus_one_detected=n_plus_one,
            slow_queries=slow,
            has_n_plus_one=bool(n_plus_one),
        )
