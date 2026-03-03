"""Tests for django_ormlens.middleware module."""

from __future__ import annotations

import pytest
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.test import RequestFactory

from django_ormlens.middleware import QueryLensMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_html_response(
    body: str = "<html><body><h1>Hello</h1></body></html>",
) -> HttpResponse:
    """Create an HTML response with the given body."""
    return HttpResponse(body, content_type="text/html; charset=utf-8")


def _make_middleware(
    response: HttpResponse | StreamingHttpResponse | None = None,
) -> QueryLensMiddleware:
    """Create a middleware instance with a simple get_response callable."""
    if response is None:
        response = _make_html_response()

    def get_response(request: object) -> HttpResponse | StreamingHttpResponse:
        return response  # type: ignore[return-value]

    return QueryLensMiddleware(get_response)


# ---------------------------------------------------------------------------
# Panel injection — positive cases
# ---------------------------------------------------------------------------


class TestPanelInjection:
    """Tests that the panel IS injected under correct conditions."""

    @pytest.mark.django_db
    def test_panel_injected_when_enabled(self, settings: object) -> None:
        """Panel should be injected when PANEL=True and DEBUG=True."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        middleware = _make_middleware()
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "django-ormlens" in content
        assert "ormlens-bar" in content
        assert "ormlens-panel" in content

    @pytest.mark.django_db
    def test_panel_shows_query_count(self, settings: object) -> None:
        """Panel bar should show query count."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        middleware = _make_middleware()
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        # The panel should contain a query count (at least "0 queries" or similar)
        assert "queries" in content or "query" in content

    @pytest.mark.django_db
    def test_panel_shows_time(self, settings: object) -> None:
        """Panel bar should show total time."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        middleware = _make_middleware()
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "ms" in content

    @pytest.mark.django_db
    def test_original_content_preserved(self, settings: object) -> None:
        """Original HTML content should still be present after injection."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        original_body = "<html><body><h1>My Page</h1></body></html>"
        middleware = _make_middleware(_make_html_response(original_body))
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "<h1>My Page</h1>" in content
        assert "</body>" in content

    @pytest.mark.django_db
    def test_content_length_updated(self, settings: object) -> None:
        """Content-Length header should be updated after injection."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        resp = _make_html_response()
        resp["Content-Length"] = str(len(resp.content))
        middleware = _make_middleware(resp)
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        assert int(response["Content-Length"]) == len(response.content)


# ---------------------------------------------------------------------------
# Panel injection — negative cases (gate checks)
# ---------------------------------------------------------------------------


class TestPanelGateChecks:
    """Tests that the panel is NOT injected when gate checks fail."""

    @pytest.mark.django_db
    def test_not_injected_when_panel_false(self, settings: object) -> None:
        """Panel should NOT be injected when PANEL=False."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": False}  # type: ignore[attr-defined]

        middleware = _make_middleware()
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "ormlens-bar" not in content

    @pytest.mark.django_db
    def test_not_injected_when_panel_missing(self, settings: object) -> None:
        """Panel should NOT be injected when PANEL setting is absent."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {}  # type: ignore[attr-defined]

        middleware = _make_middleware()
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "ormlens-bar" not in content

    @pytest.mark.django_db
    def test_not_injected_when_debug_false(self, settings: object) -> None:
        """Panel should NOT be injected when DEBUG=False."""
        settings.DEBUG = False  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        middleware = _make_middleware()
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "ormlens-bar" not in content

    @pytest.mark.django_db
    def test_not_injected_for_json_response(self, settings: object) -> None:
        """Panel should NOT be injected for JSON responses."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        json_resp = JsonResponse({"key": "value"})
        middleware = _make_middleware(json_resp)
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "ormlens-bar" not in content

    @pytest.mark.django_db
    def test_not_injected_for_plain_text(self, settings: object) -> None:
        """Panel should NOT be injected for plain text responses."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        text_resp = HttpResponse("plain text", content_type="text/plain")
        middleware = _make_middleware(text_resp)
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "ormlens-bar" not in content

    @pytest.mark.django_db
    def test_not_injected_when_no_body_tag(self, settings: object) -> None:
        """Panel should NOT be injected when no </body> tag is present."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        # HTML fragment without </body>
        resp = HttpResponse("<div>No body tag</div>", content_type="text/html")
        middleware = _make_middleware(resp)
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "ormlens-bar" not in content

    @pytest.mark.django_db
    def test_not_injected_for_streaming_response(self, settings: object) -> None:
        """Panel should NOT be injected for streaming responses."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        def stream_content():  # type: ignore[no-untyped-def]
            yield b"<html><body>"
            yield b"<h1>Streaming</h1>"
            yield b"</body></html>"

        streaming_resp = StreamingHttpResponse(
            stream_content(), content_type="text/html"
        )
        middleware = _make_middleware(streaming_resp)
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        assert isinstance(response, StreamingHttpResponse)

    @pytest.mark.django_db
    def test_not_injected_for_empty_response(self, settings: object) -> None:
        """Panel should NOT be injected for an empty HTML response."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        empty_resp = HttpResponse("", content_type="text/html")
        middleware = _make_middleware(empty_resp)
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "ormlens-bar" not in content


# ---------------------------------------------------------------------------
# N+1 and slow query indicators
# ---------------------------------------------------------------------------


class TestPanelWarnings:
    """Tests for N+1 and slow query warning indicators in the panel."""

    @pytest.mark.django_db
    def test_panel_shows_n1_warning(self, settings: object) -> None:
        """Panel should show N+1 warning badge when N+1 is detected."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "PANEL": True,
            "N1_THRESHOLD": 2,
        }

        from django.contrib.auth.models import User

        def get_response(request: object) -> HttpResponse:
            # Trigger N+1 by hitting the same table multiple times
            User.objects.count()
            User.objects.count()
            User.objects.count()
            return _make_html_response()

        middleware = QueryLensMiddleware(get_response)
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "N+1" in content
        assert "ormlens-bar-error" in content

    @pytest.mark.django_db
    def test_panel_shows_slow_query_warning(self, settings: object) -> None:
        """Panel should show slow query badge when slow queries exist."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "PANEL": True,
            "SLOW_QUERY_MS": 0,  # Flag everything as slow
            "N1_THRESHOLD": 999,  # Don't trigger N+1
        }

        from django.contrib.auth.models import User

        def get_response(request: object) -> HttpResponse:
            User.objects.count()
            return _make_html_response()

        middleware = QueryLensMiddleware(get_response)
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "Slow" in content
        assert "ormlens-bar-warn" in content

    @pytest.mark.django_db
    def test_panel_green_when_no_issues(self, settings: object) -> None:
        """Panel should use green status when no N+1 or slow queries."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {  # type: ignore[attr-defined]
            "PANEL": True,
            "N1_THRESHOLD": 999,
            "SLOW_QUERY_MS": 99999,
        }

        middleware = _make_middleware()
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "#27ae60" in content  # green color


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestPanelEdgeCases:
    """Edge case tests for the middleware."""

    @pytest.mark.django_db
    def test_case_insensitive_body_tag(self, settings: object) -> None:
        """Panel should handle case-insensitive </BODY> tags."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        resp = HttpResponse(
            "<html><BODY><h1>Test</h1></BODY></html>",
            content_type="text/html",
        )
        middleware = _make_middleware(resp)
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "ormlens-bar" in content

    @pytest.mark.django_db
    def test_panel_html_is_self_contained(self, settings: object) -> None:
        """Panel HTML should include inline CSS and JS, no external deps."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        middleware = _make_middleware()
        rf = RequestFactory()
        response = middleware(rf.get("/"))

        content = response.content.decode()
        assert "<style>" in content
        assert "onclick" in content
        # No external stylesheet or script references
        assert '<link rel="stylesheet"' not in content
        assert "<script src=" not in content

    @pytest.mark.django_db
    def test_panel_css_prefixed(self, settings: object) -> None:
        """All CSS classes should be prefixed with 'ormlens-'."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        from django_ormlens.analyzer import AnalysisResult
        from django_ormlens.middleware import QueryLensMiddleware

        panel_html = QueryLensMiddleware._build_panel(AnalysisResult())
        # Extract class names — every class attribute value should start with ormlens-
        import re

        classes = re.findall(r'class="([^"]*)"', panel_html)
        for class_attr in classes:
            for cls in class_attr.split():
                assert cls.startswith("ormlens-"), (
                    f"CSS class '{cls}' is not prefixed with 'ormlens-'"
                )

    @pytest.mark.django_db
    def test_xss_safety(self, settings: object) -> None:
        """SQL content should be HTML-escaped in the panel."""
        settings.DEBUG = True  # type: ignore[attr-defined]
        settings.ORMLENS = {"PANEL": True}  # type: ignore[attr-defined]

        from django_ormlens.analyzer import AnalysisResult

        result = AnalysisResult(
            queries=[
                {"sql": '<script>alert("xss")</script>', "time": "0.001"},
            ],
            total_count=1,
            total_time=1.0,
        )

        panel_html = QueryLensMiddleware._build_panel(result)
        assert "<script>" not in panel_html
        assert "&lt;script&gt;" in panel_html
