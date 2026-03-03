# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-03-03

### Added

- `QueryAnalyzer` class with `capture()` context manager for query analysis
- N+1 query detection with configurable threshold (`N1_THRESHOLD`)
- Slow query detection with configurable threshold (`SLOW_QUERY_MS`)
- `@explain_query` view decorator with sampling support
- Automatic per-request analysis via Django signals (`request_started`/`request_finished`)
- `TerminalFormatter` with Unicode box-drawn tables and optional colorama support
- `HtmlFormatter` with styled HTML output and XSS-safe rendering
- `get_formatter()` factory function with settings-based auto-detection
- `python manage.py querylens_report` management command with `--top` and `--format` options
- Live admin dashboard at `/admin/querylens/` with detail view and JSON API
- In-memory ring buffer report store (`MAX_STORED_REPORTS` setting)
- `QueryLensMiddleware` debug panel for HTML responses (`PANEL` setting)
- Thread-safe implementation using `threading.local()`
- Production sampling via `SAMPLE_RATE` setting
- Zero overhead when `ENABLED = False`
- Full type hints and Google-style docstrings
- 94% test coverage with pytest
