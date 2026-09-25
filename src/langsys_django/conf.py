"""Read Langsys settings from Django's ``settings.LANGSYS``.

Example ``settings.py``::

    LANGSYS = {
        "API_KEY": "…",          # or the LANGSYS_API_KEY env var
        "PROJECT_ID": "…",       # or LANGSYS_PROJECT_ID
        "API_URL": "https://api.langsys.dev/api",  # optional
        "BASE_LOCALE": "en-US",  # optional
        "QUERY_PARAM": "locale", # optional: the query parameter that carries a locale
        "COOKIE_NAME": "langsys_locale",           # optional: the cookie the app stores one in
    }

Every value is optional here; the underlying SDK also falls back to ``LANGSYS_*`` env vars.
``QUERY_PARAM`` and ``COOKIE_NAME`` only say where a request carries its locale; which locale is
served, and in what order the candidates are tried, is the SDK's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from django.conf import settings


@dataclass(frozen=True)
class LangsysSettings:
    api_key: Optional[str]
    project_id: Optional[str]
    api_url: Optional[str]
    base_locale: Optional[str]
    query_param: str
    cookie_name: str


def get_settings() -> LangsysSettings:
    raw: dict[str, Any] = getattr(settings, "LANGSYS", {}) or {}
    return LangsysSettings(
        api_key=raw.get("API_KEY"),
        project_id=raw.get("PROJECT_ID"),
        api_url=raw.get("API_URL"),
        base_locale=raw.get("BASE_LOCALE"),
        query_param=raw.get("QUERY_PARAM", "locale"),
        cookie_name=raw.get("COOKIE_NAME", "langsys_locale"),
    )
