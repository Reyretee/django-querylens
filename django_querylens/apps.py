"""AppConfig for django-querylens."""

from __future__ import annotations

import logging
from typing import Any

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class DjangoQueryLensConfig(AppConfig):
    """Django application configuration for django-querylens.

    Registers the app with Django and connects signal handlers
    when the application registry is fully populated.

    Example:
        In settings.py::

            INSTALLED_APPS = [
                ...
                "django_querylens",
            ]
    """

    name = "django_querylens"
    verbose_name = "Django Query Lens"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Connect signal handlers and patch admin URLs when Django is fully loaded.

        Imports the signals module to register ``request_started`` and
        ``request_finished`` handlers for automatic per-request query analysis.
        Also patches ``AdminSite.get_urls`` to include the querylens dashboard
        URL patterns, guarded by an ``ImportError`` check so
        ``django.contrib.admin`` remains optional.
        """
        import django_querylens.signals  # noqa: F401

        self._patch_admin_urls()

    @staticmethod
    def _patch_admin_urls() -> None:
        """Patch ``AdminSite.get_urls`` to include querylens admin views.

        Uses a ``_querylens_patched`` sentinel attribute on the original method
        to prevent double-patching if ``ready()`` is called more than once.
        """
        try:
            from django.contrib.admin import AdminSite
        except ImportError:
            logger.debug(
                "django-querylens: django.contrib.admin not installed; "
                "skipping admin URL patch."
            )
            return

        original_get_urls = AdminSite.get_urls

        if getattr(original_get_urls, "_querylens_patched", False):
            return

        def patched_get_urls(self: AdminSite) -> list[Any]:
            from django_querylens.admin import get_admin_urls

            custom_urls = get_admin_urls()
            return custom_urls + original_get_urls(self)

        patched_get_urls._querylens_patched = True  # type: ignore[attr-defined]
        AdminSite.get_urls = patched_get_urls  # type: ignore[method-assign]

        logger.debug("django-querylens: admin URLs patched successfully.")
