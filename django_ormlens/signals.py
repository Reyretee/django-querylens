"""Django signal handlers for automatic per-request query analysis.

This module connects to Django's ``request_started`` and ``request_finished``
signals to provide transparent, zero-configuration query analysis across every
HTTP request.  All per-request state is stored in :mod:`threading.local`
storage so concurrent requests in multi-threaded WSGI/ASGI servers do not
interfere with one another.

The module is imported by :class:`~django_ormlens.apps.DjangoOrmLensConfig`
inside its ``ready()`` method, which guarantees that signal registration
happens exactly once, after the full Django application registry is populated.

Settings (``ORMLENS`` dict in ``settings.py``):

* ``ENABLED`` — master switch; when ``False`` no signal handlers are active.
* ``SAMPLE_RATE`` — float in ``[0.0, 1.0]``; fraction of requests to analyse.

Thread-safety guarantee:
    Every mutable attribute written during a request is stored under a key on
    a :class:`threading.local` instance.  Each OS thread (WSGI worker) has its
    own independent copy of these attributes.  No locks are required because
    the attributes are never shared across threads.
"""

from __future__ import annotations

import logging
import random
import threading
from typing import Any

from django.core.signals import request_finished, request_started

from django_ormlens.analyzer import AnalysisResult, QueryAnalyzer, get_ormlens_setting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread-local state
# ---------------------------------------------------------------------------

#: Thread-local container.  Attributes written during a request:
#:
#: * ``active`` (bool) — whether capture is running for this request.
#: * ``analyzer`` (QueryAnalyzer) — the active analyzer instance.
#: * ``result`` (AnalysisResult | None) — populated after capture exits.
_local: threading.local = threading.local()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_enabled() -> bool:
    """Return whether global ormlens analysis is enabled.

    Returns:
        ``True`` when ``ORMLENS["ENABLED"]`` is set to a truthy value (or
        absent, defaulting to ``True``).
    """
    return bool(get_ormlens_setting("ENABLED", True))


def _should_sample() -> bool:
    """Determine whether the current request should be sampled.

    Reads ``ORMLENS["SAMPLE_RATE"]`` and performs a Bernoulli trial.

    Returns:
        ``True`` when this request should be analysed.
    """
    rate: float = float(get_ormlens_setting("SAMPLE_RATE", 1.0))
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate  # noqa: S311 — intentionally non-cryptographic


def _reset_local() -> None:
    """Clear all ormlens attributes from the current thread's local storage.

    Defensive helper called at the start of each request to ensure stale
    state from a previous request (e.g. after an unhandled exception that
    prevented ``request_finished`` from firing) cannot bleed into the next
    request served by the same thread.
    """
    _local.active = False
    _local.analyzer = None
    _local.result = None
    _local.request_path = None
    _local.request_method = None


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------


def on_request_started(sender: Any, **kwargs: Any) -> None:  # noqa: ANN401
    """Handle the ``request_started`` signal.

    Initialises thread-local state and, when sampling decides this request
    should be observed, begins a query-capture session by entering the
    :meth:`~django_ormlens.analyzer.QueryAnalyzer.capture` context manager.

    The context manager cannot be used as a true ``with`` block here because
    the start and end of a request span two separate signal handlers.  Instead
    the context manager protocol is driven manually:

    1. ``ctx.__enter__()`` — runs the setup logic and returns the
       :class:`~django_ormlens.analyzer.AnalysisResult`.
    2. ``ctx.__exit__(None, None, None)`` — runs the teardown/finally block
       in ``on_request_finished``.

    Args:
        sender: The sender of the signal (Django's WSGIHandler or similar).
        **kwargs: Additional keyword arguments forwarded by the signal
            framework (e.g. ``environ`` for ``request_started``).
    """
    _reset_local()

    # Extract request path and method from WSGI environ if available.
    environ = kwargs.get("environ")
    if environ is not None:
        _local.request_path = environ.get("PATH_INFO", "")
        _local.request_method = environ.get("REQUEST_METHOD", "")

    if not _is_enabled():
        logger.debug("django-ormlens: signals disabled; skipping request_started.")
        return

    if not _should_sample():
        logger.debug("django-ormlens: sampling skipped for this request.")
        return

    analyzer = QueryAnalyzer()
    ctx = analyzer.capture()

    # Enter the context manager manually — __enter__ runs the setup logic
    # (reset_queries, force DEBUG=True) and returns the AnalysisResult.
    try:
        result: AnalysisResult = ctx.__enter__()
    except Exception:
        logger.exception("django-ormlens: capture().__enter__() raised unexpectedly.")
        return

    _local.active = True
    _local.analyzer = analyzer
    _local.result = result
    # Store the context manager so on_request_finished can __exit__ it.
    _local._ctx_manager = ctx

    logger.debug("django-ormlens: request capture started.")


def on_request_finished(sender: Any, **kwargs: Any) -> None:  # noqa: ANN401
    """Handle the ``request_finished`` signal.

    Finalises the query-capture session started in :func:`on_request_started`
    by closing the generator, which causes the ``finally`` block inside
    :meth:`~django_ormlens.analyzer.QueryAnalyzer.capture` to execute and
    populate the :class:`~django_ormlens.analyzer.AnalysisResult`.  The
    populated result is then logged.

    If no capture session is active for the current thread (either because the
    request was not sampled or because ``ENABLED`` is ``False``) this handler
    returns immediately without performing any work.

    Args:
        sender: The sender of the signal.
        **kwargs: Additional keyword arguments forwarded by the signal
            framework.
    """
    if not getattr(_local, "active", False):
        return

    ctx = getattr(_local, "_ctx_manager", None)
    result: AnalysisResult | None = getattr(_local, "result", None)

    if ctx is not None:
        try:
            # __exit__ triggers the finally block in capture(),
            # which populates result in-place.
            ctx.__exit__(None, None, None)
        except Exception:
            logger.exception(
                "django-ormlens: exception while finalising capture "
                "on request_finished."
            )

    # Capture path/method before _reset_local clears them.
    request_path: str = getattr(_local, "request_path", None) or ""
    request_method: str = getattr(_local, "request_method", None) or ""

    _reset_local()

    if result is not None:
        _log_result(result)
        _store_result(result, path=request_path, method=request_method)


# ---------------------------------------------------------------------------
# Result logging
# ---------------------------------------------------------------------------


def _log_result(result: AnalysisResult) -> None:
    """Emit formatted output for a completed request's analysis.

    Uses :class:`~django_ormlens.formatters.TerminalFormatter` to produce
    coloured, box-drawn output when ``colorama`` is installed.  Falls back
    to plain structured log messages on import failure.

    Args:
        result: The :class:`~django_ormlens.analyzer.AnalysisResult` produced
            by the capture session.
    """
    output_setting: str = str(get_ormlens_setting("OUTPUT", "terminal"))

    if output_setting == "terminal":
        try:
            from django_ormlens.formatters import get_formatter

            formatter = get_formatter("terminal")
            import sys

            print(formatter.format(result), file=sys.stderr)  # noqa: T201
            return
        except Exception:
            pass  # Fall back to plain logging below.

    logger.info(
        "django-ormlens [request]: %d quer%s in %.3fms%s%s",
        result.total_count,
        "y" if result.total_count == 1 else "ies",
        result.total_time,
        " | N+1 DETECTED" if result.has_n_plus_one else "",
        f" | {len(result.slow_queries)} slow" if result.slow_queries else "",
    )

    for detection in result.n_plus_one_detected:
        logger.warning(
            "django-ormlens [request]: N+1 on table '%s' (%dx)",
            detection.table,
            detection.count,
        )

    for slow in result.slow_queries:
        logger.warning(
            "django-ormlens [request]: slow query %.1fms — %.120s",
            slow.time_ms,
            slow.sql,
        )


# ---------------------------------------------------------------------------
# Report storage
# ---------------------------------------------------------------------------


def _store_result(result: AnalysisResult, *, path: str, method: str) -> None:
    """Persist a completed analysis result in the in-memory store.

    Storage is already gated by ``_is_enabled()`` and ``_should_sample()``
    in :func:`on_request_started`, so no additional guards are needed here.
    The bounded deque in :class:`~django_ormlens.store.ReportStore`
    prevents unbounded memory growth.

    Args:
        result: The analysis result to store.
        path: The HTTP request path.
        method: The HTTP request method.
    """
    try:
        from django_ormlens.store import StoredReport, get_store

        report = StoredReport(path=path, method=method, result=result)
        get_store().add(report)
    except Exception:
        logger.debug("django-ormlens: failed to store report.", exc_info=True)


# ---------------------------------------------------------------------------
# Signal registration
# ---------------------------------------------------------------------------

#: ``dispatch_uid`` prevents duplicate handler connections if this module is
#: accidentally imported more than once (e.g. in unusual reload scenarios).
_UID_STARTED = "django_ormlens.signals.on_request_started"
_UID_FINISHED = "django_ormlens.signals.on_request_finished"

request_started.connect(on_request_started, dispatch_uid=_UID_STARTED)
request_finished.connect(on_request_finished, dispatch_uid=_UID_FINISHED)

logger.debug(
    "django-ormlens: signal handlers registered (uid=%s, uid=%s).",
    _UID_STARTED,
    _UID_FINISHED,
)
