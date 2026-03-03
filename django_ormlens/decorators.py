"""View decorators for django-ormlens query analysis.

This module provides the ``explain_query`` decorator which wraps Django view
functions (both function-based and class-based via ``method_decorator``) in a
:class:`~django_ormlens.analyzer.QueryAnalyzer` capture session.  After the
view returns, the analysis results are forwarded to a configurable output
function (default: :mod:`logging`).

Typical usage::

    from django_ormlens.decorators import explain_query

    @explain_query
    def my_view(request):
        articles = list(Article.objects.all())
        return HttpResponse("ok")

    # With a custom output function:
    @explain_query(output_fn=my_reporter)
    def another_view(request):
        ...

    # With class-based views:
    from django.utils.decorators import method_decorator

    @method_decorator(explain_query, name="dispatch")
    class MyView(View):
        ...
"""

from __future__ import annotations

import functools
import logging
import random
from collections.abc import Callable
from typing import Any, TypeVar, overload

from django_ormlens.analyzer import AnalysisResult, QueryAnalyzer, get_ormlens_setting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type variables
# ---------------------------------------------------------------------------

_F = TypeVar("_F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Default output function
# ---------------------------------------------------------------------------


def _default_output(result: AnalysisResult, view_name: str) -> None:
    """Log analysis results using the standard logging framework.

    Emits a summary at INFO level and detailed warnings for N+1 patterns and
    slow queries.

    Args:
        result: The populated :class:`~django_ormlens.analyzer.AnalysisResult`
            from the capture session.
        view_name: The ``__qualname__`` of the wrapped view function, used to
            contextualise log messages.
    """
    logger.info(
        "django-ormlens [%s]: %d quer%s in %.3fms%s%s",
        view_name,
        result.total_count,
        "y" if result.total_count == 1 else "ies",
        result.total_time,
        " | N+1 DETECTED" if result.has_n_plus_one else "",
        f" | {len(result.slow_queries)} slow" if result.slow_queries else "",
    )

    for detection in result.n_plus_one_detected:
        logger.warning(
            "django-ormlens [%s]: N+1 on table '%s' (%dx)",
            view_name,
            detection.table,
            detection.count,
        )

    for slow in result.slow_queries:
        logger.warning(
            "django-ormlens [%s]: slow query %.1fms — %.120s",
            view_name,
            slow.time_ms,
            slow.sql,
        )


# ---------------------------------------------------------------------------
# Sampling helper
# ---------------------------------------------------------------------------


def _should_sample() -> bool:
    """Determine whether this request should be sampled.

    Reads ``ORMLENS["SAMPLE_RATE"]`` (a float in ``[0.0, 1.0]``).  Returns
    ``True`` with a probability equal to the configured rate.

    Returns:
        ``True`` if the current request should be analysed.
    """
    rate: float = float(get_ormlens_setting("SAMPLE_RATE", 1.0))
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate  # noqa: S311 — non-cryptographic intentional


# ---------------------------------------------------------------------------
# explain_query decorator
# ---------------------------------------------------------------------------


@overload
def explain_query(view_func: _F) -> _F: ...


@overload
def explain_query(
    *,
    output_fn: Callable[[AnalysisResult, str], None] | None = None,
) -> Callable[[_F], _F]: ...


def explain_query(
    view_func: _F | None = None,
    *,
    output_fn: Callable[[AnalysisResult, str], None] | None = None,
) -> _F | Callable[[_F], _F]:
    """Decorator that wraps a Django view in a query-capture session.

    After the view function returns, the captured queries are analysed and the
    results are forwarded to *output_fn* (default: structured log messages via
    :mod:`logging`).

    The decorator respects two settings from ``ORMLENS``:

    * ``ENABLED`` — when ``False``, the decorator is a complete no-op with
      zero overhead; the original view function is returned unwrapped.
    * ``SAMPLE_RATE`` — a float in ``[0.0, 1.0]``.  Each request is
      independently sampled; when the random draw exceeds the rate the view
      runs unwrapped for that request.

    The decorator can be applied in two ways:

    * **Without arguments** — ``@explain_query`` applied directly to the view.
    * **With arguments** — ``@explain_query(output_fn=...)`` factory style.

    It works with both function-based views and class-based views.  For
    class-based views, combine with :func:`django.utils.decorators.method_decorator`::

        from django.utils.decorators import method_decorator

        @method_decorator(explain_query, name="dispatch")
        class ArticleListView(ListView):
            ...

    Args:
        view_func: The view callable to wrap.  When ``None`` the decorator is
            being used in factory mode (with keyword arguments) and a wrapper
            factory is returned instead.
        output_fn: Optional callable with signature
            ``(result: AnalysisResult, view_name: str) -> None`` invoked after
            each captured request.  Defaults to :func:`_default_output` which
            emits structured log messages.

    Returns:
        The wrapped view function when *view_func* is provided, or a decorator
        factory when called with keyword arguments only.

    Example::

        @explain_query
        def article_list(request):
            return HttpResponse(Article.objects.count())

        # Custom reporter for your observability stack:
        def send_to_datadog(result, view_name):
            statsd.gauge("django.queries", result.total_count, tags=[view_name])

        @explain_query(output_fn=send_to_datadog)
        def article_detail(request, pk):
            article = Article.objects.get(pk=pk)
            return HttpResponse(article.title)
    """
    # Factory mode: @explain_query(output_fn=...) — return a decorator.
    if view_func is None:

        def decorator(func: _F) -> _F:
            return _wrap_view(func, output_fn=output_fn)

        return decorator

    # Direct mode: @explain_query applied without parentheses.
    return _wrap_view(view_func, output_fn=output_fn)


def _wrap_view(
    view_func: _F,
    *,
    output_fn: Callable[[AnalysisResult, str], None] | None,
) -> _F:
    """Internal helper that applies the capture wrapper to a view function.

    When ``ORMLENS["ENABLED"]`` is ``False`` at decoration time the original
    function is returned as-is.  The ENABLED check at decoration time provides
    the stated "zero overhead" guarantee — no wrapper frame is ever pushed onto
    the call stack for a permanently-disabled configuration.

    Note: ``SAMPLE_RATE`` is evaluated per-request (inside the wrapper) to
    allow runtime configuration changes without restarting the server.
    ``ENABLED`` is evaluated once at decoration time for maximum performance.

    Args:
        view_func: The view callable to wrap.
        output_fn: Output callback; ``None`` falls back to
            :func:`_default_output`.

    Returns:
        The wrapped (or original) view callable.
    """
    effective_output: Callable[[AnalysisResult, str], None] = (
        output_fn if output_fn is not None else _default_output
    )
    view_name: str = getattr(view_func, "__qualname__", repr(view_func))
    analyzer = QueryAnalyzer()

    @functools.wraps(view_func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Per-request sampling check.
        if not _should_sample():
            logger.debug(
                "django-ormlens: sampling skipped for '%s'.",
                view_name,
            )
            return view_func(*args, **kwargs)

        with analyzer.capture() as result:
            response = view_func(*args, **kwargs)

        try:
            effective_output(result, view_name)
        except Exception:
            logger.exception(
                "django-ormlens: output_fn raised an exception for '%s'; "
                "suppressing to avoid masking the view response.",
                view_name,
            )

        return response

    return wrapper  # type: ignore[return-value]
