"""Shared pytest fixtures for django-querylens test suite."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User


@pytest.fixture
def sample_user(db: None) -> User:
    """Create a single test user."""
    return User.objects.create_user(
        username="testuser",
        email="test@example.com",
    )


@pytest.fixture
def multiple_users(db: None) -> list[User]:
    """Create 5 test users for N+1 detection tests."""
    return [
        User.objects.create_user(username=f"user{i}") for i in range(5)
    ]
