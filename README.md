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
MIDDLEWARE = [
    ...,
    "django.middleware.locale.LocaleMiddleware",  # optional: Langsys then serves its language
    "langsys_django.middleware.LangsysMiddleware",
]

LANGSYS = {
    "API_KEY": "…",  # or the LANGSYS_API_KEY env var
    "PROJECT_ID": "…",  # or LANGSYS_PROJECT_ID
    "BASE_LOCALE": "en-US",
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

Put `{% langsys_resolved %}` on your layout's root element:

```django
<html {% langsys_resolved %}>
```

When a page renders in a locale other than the project's base, the root gets a `data-ls-resolved`
attribute, so a Langsys browser SDK on the same page never mistakes the translated text for new
source text. A page in the base locale stays unmarked, and its text stays discoverable.

In Python (views, etc.):

```python
from langsys_django import t, get_client

label = t("Save", "UI")
label = t("Hello, {name}!", "Greetings", name="Sarah")

# The full SDK is available for content blocks / whole-page translation:
html = get_client().translate_page(rendered_html, category="UI")  # needs langsys[html]
```

`t()` and `{% t %}` keep the names `phrase` and `category` for themselves, so a placeholder with
either name can't be passed as a keyword: `t("Browse {category}", category=name)` uses `name` as
the category. In Python, pass the value through the SDK:
`get_client().translate("Browse {category}", category="UI", params={"category": name})`. In a
template, rename the placeholder.

## Validation errors as translatable messages

A failed form's errors become Langsys server messages without changing what Django reports. Each
entry carries Django's own `code`, and a `template` that is Django's own sentence in your source
language, before Django fills it: `This field is required.` as Django wrote it, and
`Ensure this value has at least {limit_value} characters (it has {show_value}).` with its values as
`params`. Where Django's sentence names the field or the model (`%(field_label)s`,
`%(model_name)s`), the name Django prints is written into the template, so it is translated with
the sentence.

```python
from langsys_django.messages import error_response

form = SignupForm(request.POST)
if not form.is_valid():
    return error_response(form)  # 400: form.errors.get_json_data(), plus the entries beside it
```

The body is Django's own `form.errors.get_json_data()`; the entries sit beside it under
`langsys_errors` (or the `RESPONSE_KEY` setting), for a client SDK to render translated. In a
template, render them in the request's locale:

```django
{% for entry in form|langsys_errors %}<li>{% t_message entry %}</li>{% endfor %}
```

A custom validator follows Django's convention and needs nothing more: raise
`ValidationError("That name is reserved.", code="reserved")`, or a message with `%(name)s`
placeholders and `params`. A failure raised without a code carries none.

List every template ahead of time, and register the ones Langsys doesn't have yet:

```bash
python manage.py langsys_messages --provider myapp.langsys:templates [--register] [--strict]
```

where `templates()` returns `declared_templates([SignupForm, ...])` from `langsys_django.messages`.
A class-based validator's `message` is listed as it is. A validator function or `clean` method is
listed when you name the messages it raises with `@declares(...)`; otherwise the command reports it
with a `PROBLEM` line, and its messages register the first time they are emitted. The command exits
zero either way, unless `--strict` asks it to fail on a problem. A field with no declared label gets
an `ADVICE` line, since Django then names it from its key. A template that still holds one of
Django's label placeholders is refused.

With Django REST framework (`pip install langsys-django[drf]`), set the exception handler:

```python
REST_FRAMEWORK = {"EXCEPTION_HANDLER": "langsys_django.drf.exception_handler"}
```

DRF's own error response is unchanged, and a failed validation's response also carries its entries
under the same key. Each entry has DRF's own code and sentence
(`Ensure this field has no more than {max_length} characters.`), its values read from the field and
the input, and a `field` path such as `items.1.name`. For the listing command, a provider returns
`langsys_django.drf.declared_templates([SignupSerializer, ...])`.

## How the locale is resolved

With Django's `LocaleMiddleware` before `LangsysMiddleware`, Langsys serves the language Django
resolved for the request (`request.LANGUAGE_CODE`), so your own messages, dates and translations
agree. The SDK maps it to your project's locales: `es-es` is `es-es`, and a bare `es` is your
project's default Spanish locale. A language your project doesn't serve is served in the base
locale. Your app varies its responses on what Django decided, so Langsys adds nothing to `Vary`.

Without `LocaleMiddleware`, `LangsysMiddleware` resolves the locale itself, trying in order:

1. the URL: the `?locale=` query parameter, or the language prefix when your URLconf uses
   `i18n_patterns`;
2. the `langsys_locale` cookie;
3. the `Accept-Language` header;

and otherwise the project's base locale. Every candidate is checked against the locales your
project serves, and one it doesn't serve is skipped. The response gets the `Vary` headers the
choice depended on (`Cookie`, `Accept-Language`), so a CDN or other shared cache never serves one
visitor's language to another. A locale taken from the URL needs none, because the URL is already
the cache key.

The middleware never writes the cookie: set it wherever your app lets a visitor pick a language.

The locale is exposed to translations for the whole request through a context variable, including
a streamed response body, which renders after the middleware has returned. So a single shared
client is safe across concurrent requests.

## When phrases are registered

Phrases the catalog doesn't have yet are queued while the page renders. The middleware runs each
request inside one of the SDK's request scopes, so nothing a request discovers is sent before its
response, streamed bodies included, has gone out: not by the SDK's debounce, and not by another
request finishing first. When Django then fires `request_finished`, the app asks the SDK to flush
the queue and to forget the request's write decision, so the decision never carries over to the
next request.

Whether anything is sent is the SDK's call, not this package's: the server decides per session
whether it may write. A read-only key sends nothing, and if the API can't be reached the queue is
kept and retried rather than dropped.

Outside a request — a management command, a Celery task — the SDK's debounce and exit hook
apply; call `get_client().flush_pending()` at the end of long-running work.

## Settings reference

| Key | Default | Purpose |
|---|---|---|
| `API_KEY` / `PROJECT_ID` | env `LANGSYS_*` | credentials |
| `API_URL` | `https://api.langsys.dev/api` | backend host — point it at a test double to run without the real API. Read when the client is first built, so call `langsys_django.reset_client()` after changing it |
| `BASE_LOCALE` | project base | source-string locale |
| `QUERY_PARAM` | `locale` | query parameter that carries a locale in the URL |
| `COOKIE_NAME` | `langsys_locale` | cookie your app keeps a visitor's locale in |
| `RESPONSE_KEY` | `langsys_errors` | key a failed form's or serializer's entries sit under, beside Django's or DRF's own errors |

## Releasing

Built and published to [PyPI](https://pypi.org) manually. Bump the version in `pyproject.toml`,
update `CHANGELOG.md`, then:

```bash
python -m build
twine upload dist/*   # requires a PyPI token with upload access
```

## License

MIT
