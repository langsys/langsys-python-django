# langsys-django

Django integration for [Langsys](https://langsys.dev) — a **thin wrapper** over the
[`langsys`](https://github.com/langsys/langsys-python) Python SDK. It adds only the
Django-idiomatic pieces (a template tag, request-locale middleware, settings-based setup)
and delegates all translation to the base SDK.

The phrase in your code is the lookup key **and** the base-language default — no keys
file. Untranslated phrases render as the source phrase.

## Install

```bash
pip install langsys-django
```

Requires Python 3.9+ and Django 4.2+.

## Setup

```python
# settings.py
INSTALLED_APPS = [..., "langsys_django"]
MIDDLEWARE = [..., "langsys_django.middleware.LangsysMiddleware"]

LANGSYS = {
    "API_KEY": "…",        # or the LANGSYS_API_KEY env var
    "PROJECT_ID": "…",     # or LANGSYS_PROJECT_ID
    "BASE_LOCALE": "en-US",
    "SUPPORTED": ["en-US", "es-ES"],   # optional locale allow-list for detection
}
```

## Use it

In templates:

```django
{% load langsys %}
{% t "Save" %}
{% t "Save" "UI" %}
{% t "Hello, {name}!" "Greetings" name=request.user.get_full_name %}

{{ "Save"|t }}
{{ "Save"|t:"UI" }}
```

A keyword argument whose variable doesn't exist is passed to the SDK as *missing*, not as an
empty string, so the gap stays visible (`Hello, {name}!`) instead of rendering `Hello, !`.

In Python (views, etc.):

```python
from langsys_django import t, get_client

label = t("Save", "UI")
label = t("Hello, {name}!", "Greetings", name="Sarah")

# The full SDK is available for content blocks / whole-page translation:
html = get_client().translate_page(rendered_html, category="UI")   # needs langsys[html]
```

## How the locale is resolved

`LangsysMiddleware` picks the request locale in order: `?locale=` (persisted to a cookie),
then the `langsys_locale` cookie, then the `Accept-Language` header (matched against
`SUPPORTED`). It exposes that locale to translations for the request via a context
variable — including a streamed response body, which renders after the middleware has
returned — so a single shared client is safe across concurrent requests.

## When phrases are registered

Phrases the catalog doesn't have yet are queued while the page renders. When Django fires
`request_finished` — after the response, streamed bodies included, has been sent — the app asks
the SDK to flush that queue and to forget the request's write decision, so the decision never
carries over to the next request.

Whether anything is sent is the SDK's call, not this package's: the server decides per session
whether it may write. A read-only key sends nothing, and if the API can't be reached the queue is
kept and retried rather than dropped. The SDK also sends on a short debounce of its own, so on a
render that runs long a batch can still go out before the response finishes.

Outside a request — a management command, a Celery task — the SDK's debounce and exit hook
apply; call `get_client().flush_pending()` at the end of long-running work.

## Settings reference

| Key | Default | Purpose |
|---|---|---|
| `API_KEY` / `PROJECT_ID` | env `LANGSYS_*` | credentials |
| `API_URL` | `https://api.langsys.dev/api` | backend host — point it at a test double to run without the real API. Read when the client is first built, so call `langsys_django.reset_client()` after changing it |
| `BASE_LOCALE` | project base | source-string locale |
| `SUPPORTED` | `[]` | locale allow-list for `Accept-Language` matching |
| `QUERY_PARAM` | `locale` | query param that switches locale |
| `COOKIE_NAME` | `langsys_locale` | persisted-choice cookie |
| `COOKIE_MAX_AGE` | `31536000` | cookie lifetime (seconds) |

## Releasing

Built and published to [PyPI](https://pypi.org) manually. Bump the version in `pyproject.toml`,
update `CHANGELOG.md`, then:

```bash
python -m build
twine upload dist/*   # requires a PyPI token with upload access
```

## License

MIT
