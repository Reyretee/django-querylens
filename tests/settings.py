"""Minimal Django settings for the django-querylens test suite.

This module is referenced by ``pyproject.toml`` via::

    [tool.pytest.ini_options]
    DJANGO_SETTINGS_MODULE = "tests.settings"

It uses an in-memory SQLite database and includes only the apps required
to exercise the django-querylens package.
"""

from __future__ import annotations

SECRET_KEY = "django-querylens-insecure-test-secret-key"  # noqa: S105

DEBUG = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django_querylens",
    "tests",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "tests.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# django-querylens configuration used during tests.
# N1_THRESHOLD is deliberately low (2) to make N+1 tests easier to trigger.
QUERYLENS = {
    "ENABLED": True,
    "SAMPLE_RATE": 1.0,
    "N1_THRESHOLD": 2,
    "SLOW_QUERY_MS": 50,
    "OUTPUT": "terminal",
    "MAX_STORED_REPORTS": 1000,
}

USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
