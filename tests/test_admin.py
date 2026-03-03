"""Tests for django_ormlens.admin module."""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from django_ormlens.analyzer import AnalysisResult, N1Detection, SlowQuery
from django_ormlens.store import StoredReport, get_store


@pytest.fixture
def staff_client(db: None, settings: object) -> Client:
    """Return a Django test client logged in as a staff user.

    Also sets ``DEBUG=True`` since pytest-django defaults to ``False``.
    """
    settings.DEBUG = True  # type: ignore[attr-defined]

    from django.contrib.auth.models import User

    user = User.objects.create_user(
        username="staffuser",
        password="testpass123",  # noqa: S106
        is_staff=True,
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def anon_client(settings: object) -> Client:
    """Return an anonymous Django test client."""
    settings.DEBUG = True  # type: ignore[attr-defined]
    return Client()


@pytest.fixture(autouse=True)
def _clear_store() -> None:
    """Clear the report store before each test."""
    get_store().clear()


def _make_stored_report(
    *,
    path: str = "/api/test/",
    method: str = "GET",
    total_count: int = 3,
    total_time: float = 15.0,
    n1: bool = False,
    slow: bool = False,
) -> StoredReport:
    """Helper to create a StoredReport with customizable result."""
    result = AnalysisResult(
        total_count=total_count,
        total_time=total_time,
        n_plus_one_detected=[N1Detection(table="myapp_post", count=5)] if n1 else [],
        slow_queries=(
            [SlowQuery(sql="SELECT * FROM big_table", time_ms=200.0)] if slow else []
        ),
        has_n_plus_one=n1,
    )
    return StoredReport(path=path, method=method, result=result)


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------


class TestDashboardView:
    """Tests for the ormlens dashboard view."""

    def test_staff_can_access(self, staff_client: Client) -> None:
        response = staff_client.get("/admin/ormlens/")
        assert response.status_code == 200

    def test_anonymous_redirected(self, anon_client: Client) -> None:
        response = anon_client.get("/admin/ormlens/")
        assert response.status_code == 302

    def test_reports_shown_in_table(self, staff_client: Client) -> None:
        store = get_store()
        store.add(_make_stored_report(path="/api/users/", method="POST"))
        store.add(_make_stored_report(path="/api/items/", method="GET"))

        response = staff_client.get("/admin/ormlens/")
        content = response.content.decode()

        assert "/api/users/" in content
        assert "/api/items/" in content
        assert "POST" in content
        assert "GET" in content

    def test_empty_state_message(self, staff_client: Client) -> None:
        response = staff_client.get("/admin/ormlens/")
        content = response.content.decode()
        assert "No reports yet" in content

    def test_debug_false_returns_403(self, db: None, settings: object) -> None:
        settings.DEBUG = False  # type: ignore[attr-defined]
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "ENABLED": False,
            "SAMPLE_RATE": 0.0,
        }

        from django.contrib.auth.models import User

        user = User.objects.create_user(
            username="staffuser_debug",
            password="testpass123",  # noqa: S106
            is_staff=True,
        )
        client = Client()
        client.force_login(user)

        response = client.get("/admin/ormlens/")
        assert response.status_code == 403

    def test_clear_action(self, staff_client: Client) -> None:
        store = get_store()
        original = _make_stored_report(path="/should-be-cleared/")
        store.add(original)
        assert store.count >= 1

        response = staff_client.post("/admin/ormlens/", {"action": "clear"})
        assert response.status_code == 200
        # The original report should be gone. The signal handler may have
        # added a new report for the POST request itself, so we check that
        # the original was cleared rather than asserting count == 0.
        assert store.get_by_id(original.id) is None


# ---------------------------------------------------------------------------
# Detail view
# ---------------------------------------------------------------------------


class TestDetailView:
    """Tests for the ormlens detail view."""

    def test_renders_report_detail(self, staff_client: Client) -> None:
        store = get_store()
        report = _make_stored_report(
            path="/api/detail-test/", n1=True, slow=True
        )
        store.add(report)

        response = staff_client.get(f"/admin/ormlens/{report.id}/")
        assert response.status_code == 200
        content = response.content.decode()

        # HtmlFormatter output should be present
        assert "django-ormlens" in content
        assert "/api/detail-test/" in content

    def test_unknown_id_returns_404(self, staff_client: Client) -> None:
        response = staff_client.get("/admin/ormlens/nonexistent123/")
        assert response.status_code == 404

    def test_debug_false_returns_403(self, db: None, settings: object) -> None:
        settings.DEBUG = False  # type: ignore[attr-defined]
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "ENABLED": False,
            "SAMPLE_RATE": 0.0,
        }

        from django.contrib.auth.models import User

        user = User.objects.create_user(
            username="staffuser_detail",
            password="testpass123",  # noqa: S106
            is_staff=True,
        )
        client = Client()
        client.force_login(user)

        store = get_store()
        report = _make_stored_report()
        store.add(report)

        response = client.get(f"/admin/ormlens/{report.id}/")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# API view
# ---------------------------------------------------------------------------


class TestApiView:
    """Tests for the ormlens API reports view."""

    def test_returns_json(self, staff_client: Client) -> None:
        store = get_store()
        store.add(_make_stored_report(path="/api/1/"))
        store.add(_make_stored_report(path="/api/2/", n1=True))

        response = staff_client.get("/admin/ormlens/api/reports/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"

        data = json.loads(response.content)
        assert "reports" in data
        assert len(data["reports"]) == 2

    def test_json_fields(self, staff_client: Client) -> None:
        store = get_store()
        report = _make_stored_report(
            path="/api/check/", method="POST", total_count=5, total_time=42.5,
            n1=True, slow=True,
        )
        store.add(report)

        response = staff_client.get("/admin/ormlens/api/reports/")
        data = json.loads(response.content)
        entry = data["reports"][0]

        assert entry["id"] == report.id
        assert entry["path"] == "/api/check/"
        assert entry["method"] == "POST"
        assert entry["total_count"] == 5
        assert entry["total_time"] == 42.5
        assert entry["n_plus_one_count"] == 1
        assert entry["slow_query_count"] == 1
        assert "timestamp" in entry

    def test_debug_false_returns_403(self, db: None, settings: object) -> None:
        settings.DEBUG = False  # type: ignore[attr-defined]
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "ENABLED": False,
            "SAMPLE_RATE": 0.0,
        }

        from django.contrib.auth.models import User

        user = User.objects.create_user(
            username="staffuser_api",
            password="testpass123",  # noqa: S106
            is_staff=True,
        )
        client = Client()
        client.force_login(user)

        response = client.get("/admin/ormlens/api/reports/")
        assert response.status_code == 403

    def test_anonymous_redirected(self, anon_client: Client) -> None:
        response = anon_client.get("/admin/ormlens/api/reports/")
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


class TestUrlResolution:
    """Tests that ormlens admin URLs are resolvable."""

    def test_dashboard_url_resolves(self) -> None:
        url = reverse("admin:ormlens_dashboard")
        assert url == "/admin/ormlens/"

    def test_detail_url_resolves(self) -> None:
        url = reverse("admin:ormlens_detail", kwargs={"report_id": "abc123"})
        assert url == "/admin/ormlens/abc123/"

    def test_api_url_resolves(self) -> None:
        url = reverse("admin:ormlens_api_reports")
        assert url == "/admin/ormlens/api/reports/"
