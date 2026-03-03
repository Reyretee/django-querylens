# django-querylens

Django ORM query visualizer with automatic N+1 detection, slow query identification, and terminal/HTML reports.

## Features

- **N+1 Query Detection** — Automatically detects repeated queries to the same table
- **Slow Query Detection** — Flags queries exceeding a configurable threshold
- **View Decorator** — `@explain_query` wraps views with zero-effort query profiling
- **Automatic Signal-Based Capture** — Per-request analysis via Django signals
- **Terminal & HTML Reports** — Box-drawn terminal tables or styled HTML output
- **Management Command** — `python manage.py querylens_report` for CLI reporting
- **Live Admin Dashboard** — Real-time query history at `/admin/querylens/`
- **Debug Panel** — Collapsible overlay injected into HTML responses
- **Production Safe** — Configurable sampling rate, zero overhead when disabled
- **Thread Safe** — All state stored in `threading.local()`
- **Zero Dependencies** — Only requires Django (optional: `colorama` for colored output)

## Installation

```bash
pip install django-querylens
```

For colored terminal output:

```bash
pip install django-querylens[color]
```

## Quick Start

### 1. Add to INSTALLED_APPS

```python
# settings.py
INSTALLED_APPS = [
    ...
    'django_querylens',
]
```

### 2. Configure settings

```python
# settings.py
QUERYLENS = {
    'ENABLED': True,           # Master switch
    'SAMPLE_RATE': 1.0,        # 1.0 = all requests, 0.1 = 10% sampling
    'N1_THRESHOLD': 3,         # Min repeated queries to flag N+1
    'SLOW_QUERY_MS': 100,      # Slow query threshold in milliseconds
    'OUTPUT': 'terminal',      # 'terminal' or 'html'
    'MAX_STORED_REPORTS': 1000,# Max reports kept in memory for admin dashboard
}
```

### 3. Use the decorator on views

```python
from django_querylens import explain_query

@explain_query
def article_list(request):
    articles = list(Article.objects.all())
    for article in articles:
        _ = article.author.name  # N+1 detected!
    return render(request, 'articles.html', {'articles': articles})
```

### 4. Or use the context manager directly

```python
from django_querylens import QueryAnalyzer

analyzer = QueryAnalyzer()
with analyzer.capture() as result:
    users = list(User.objects.all())
    for user in users:
        User.objects.get(pk=user.pk)  # N+1!

print(f"Total queries: {result.total_count}")
print(f"N+1 detected: {result.has_n_plus_one}")
for detection in result.n_plus_one_detected:
    print(f"  Table: {detection.table} ({detection.count}x)")
```

### 5. Generate a CLI report

```bash
python manage.py querylens_report --top 10
python manage.py querylens_report --format html > report.html
```

## Automatic Per-Request Analysis

When `django_querylens` is in `INSTALLED_APPS` and `QUERYLENS['ENABLED']` is `True`, signal handlers automatically capture and log query analysis for every HTTP request (respecting `SAMPLE_RATE`).

With `colorama` installed (`pip install django-querylens[color]`), you get colored box-drawn output in the terminal. Without it, structured plain-text log lines are emitted.

All captured reports are also stored in memory and accessible via the admin dashboard (see below).

## Admin Dashboard

django-querylens includes a live query history dashboard accessible at `/admin/querylens/`. It requires staff access and `DEBUG = True`.

### What it shows

- **Dashboard** (`/admin/querylens/`) — A table of recent requests with color-coded rows:
  - Red border = N+1 detected
  - Orange border = slow queries
  - Green border = clean
- **Detail** (`/admin/querylens/<report_id>/`) — Full HTML-formatted analysis for a single request
- **API** (`/admin/querylens/api/reports/`) — JSON endpoint for programmatic access or auto-refresh

### Features

- Refresh and Clear All buttons
- Query count, total time, N+1 count, and slow query count per request
- Click any row to see the full analysis report
- Reports are stored in a bounded in-memory ring buffer (`MAX_STORED_REPORTS`, default 1000)

No extra setup is needed — the dashboard URLs are automatically registered when `django.contrib.admin` is installed.

## Custom Output Function

```python
from django_querylens import explain_query
from django_querylens.analyzer import AnalysisResult

def send_to_monitoring(result: AnalysisResult, view_name: str) -> None:
    statsd.gauge('django.queries.count', result.total_count, tags=[view_name])
    if result.has_n_plus_one:
        statsd.increment('django.queries.n_plus_one', tags=[view_name])

@explain_query(output_fn=send_to_monitoring)
def my_view(request):
    ...
```

## Formatters

```python
from django_querylens.formatters import get_formatter, TerminalFormatter, HtmlFormatter

# Auto-detect from settings
formatter = get_formatter()
output = formatter.format(result)

# Explicit
terminal_output = TerminalFormatter().format(result)
html_output = HtmlFormatter().format(result)
```

## Debug Panel

django-querylens includes a lightweight debug panel that automatically injects a collapsible overlay at the bottom of every HTML response — similar to Django Debug Toolbar but focused on query analysis.

### Setup

```python
# settings.py
MIDDLEWARE = [
    ...
    'django_querylens.middleware.QueryLensMiddleware',
]

QUERYLENS = {
    'PANEL': True,  # Enable the debug panel (default: False)
    ...
}
```

The panel is **only injected** when all of the following are true:

- `QUERYLENS['PANEL']` is `True`
- `settings.DEBUG` is `True` (never in production)
- Response `Content-Type` is `text/html`
- Response body contains a `</body>` tag
- Response is not a streaming response

### What it shows

- **Bottom bar**: Query count and total time with color-coded status (green/orange/red)
- **N+1 Detections**: Tables with repeated query patterns (red)
- **Slow Queries**: Queries exceeding `SLOW_QUERY_MS` threshold (orange)
- **All Queries**: Collapsible list of every query with execution time

The panel is self-contained (inline CSS, no external dependencies) with a dark theme and all CSS classes prefixed with `querylens-` to avoid conflicts.

## Production Configuration

For production, use a low sample rate to minimize overhead:

```python
QUERYLENS = {
    'ENABLED': True,
    'SAMPLE_RATE': 0.01,       # Analyze 1% of requests
    'N1_THRESHOLD': 5,
    'SLOW_QUERY_MS': 200,
    'OUTPUT': 'terminal',
    'MAX_STORED_REPORTS': 500,
}
```

To completely disable (zero overhead):

```python
QUERYLENS = {
    'ENABLED': False,
}
```

## Compatibility

- Python 3.9+
- Django 3.2, 4.x, 5.x, 6.x

## Development

```bash
git clone https://github.com/Reyretee/django-querylens.git
cd django-querylens
pip install -e ".[dev]"
pytest
```

## License

MIT License. See [LICENSE](LICENSE) for details.
