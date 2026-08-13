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
variable, so a single shared client is safe across concurrent requests.

With a **write** key and `AUTO_FLUSH` (default on), phrases discovered while rendering are
registered after the response; with a **read** key nothing is written.

## Settings reference

| Key | Default | Purpose |
|---|---|---|
| `API_KEY` / `PROJECT_ID` | env `LANGSYS_*` | credentials |
| `API_URL` | `https://api.langsys.dev/api` | backend host |
| `BASE_LOCALE` | project base | source-string locale |
| `SUPPORTED` | `[]` | locale allow-list for `Accept-Language` matching |
| `QUERY_PARAM` | `locale` | query param that switches locale |
| `COOKIE_NAME` | `langsys_locale` | persisted-choice cookie |
| `COOKIE_MAX_AGE` | `31536000` | cookie lifetime (seconds) |
| `AUTO_FLUSH` | `True` | register discovered phrases after the response (write key) |

## Releasing

Built and published to [PyPI](https://pypi.org) manually. Bump the version in `pyproject.toml`,
update `CHANGELOG.md`, then:

```bash
python -m build
twine upload dist/*   # requires a PyPI token with upload access
```

## License

MIT
