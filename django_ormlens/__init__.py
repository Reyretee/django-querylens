"""django-ormlens — Django ORM query visualizer with N+1 detection.

Public API
----------

.. code-block:: python

    from django_ormlens import QueryAnalyzer, explain_query

    # Context-manager style
    analyzer = QueryAnalyzer()
    with analyzer.capture() as result:
        list(MyModel.objects.all())

    # Decorator style
    @explain_query
    def my_view(request):
        ...

Package version: 0.1.0
"""

from __future__ import annotations

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Default app config — required for Django < 3.2 auto-discovery compatibility
# ---------------------------------------------------------------------------

default_app_config = "django_ormlens.apps.DjangoOrmLensConfig"

# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

from django_ormlens.analyzer import QueryAnalyzer  # noqa: E402
from django_ormlens.decorators import explain_query  # noqa: E402, F401
from django_ormlens.middleware import QueryLensMiddleware  # noqa: E402, F401

__all__ = [
    "QueryAnalyzer",
    "QueryLensMiddleware",
    "explain_query",
    "__version__",
]
