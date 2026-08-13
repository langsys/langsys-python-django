"""Django integration for Langsys — a thin wrapper over the ``langsys`` Python SDK.

Setup (``settings.py``)::

    INSTALLED_APPS = [..., "langsys_django"]
    MIDDLEWARE = [..., "langsys_django.middleware.LangsysMiddleware"]
    LANGSYS = {"API_KEY": "…", "PROJECT_ID": "…", "BASE_LOCALE": "en-US"}

Then translate in templates (``{% load langsys %}{% t "Save" "UI" %}``), in views
(``from langsys_django import t``), or reach the full SDK via ``get_client()``.
"""

from __future__ import annotations

from .client import get_client, reset_client, set_client, t
from .locale import get_current_locale, set_current_locale

__all__ = [
    "t",
    "get_client",
    "set_client",
    "reset_client",
    "get_current_locale",
    "set_current_locale",
]
