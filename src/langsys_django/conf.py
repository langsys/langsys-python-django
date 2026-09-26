"""Read Langsys settings from Django's ``settings.LANGSYS``.

Example ``settings.py``::

    LANGSYS = {
        "API_KEY": "…",          # or the LANGSYS_API_KEY env var
        "PROJECT_ID": "…",       # or LANGSYS_PROJECT_ID
        "API_URL": "https://api.langsys.dev/api",  # optional
        "BASE_LOCALE": "en-US",  # optional
        "QUERY_PARAM": "locale", # optional: the query parameter that carries a locale
        "COOKIE_NAME": "langsys_locale",           # optional: the cookie the app stores one in
        "RESPONSE_KEY": "…",   # optional: the key entries sit under in error bodies
        "LEGACY_FILES": ["locale/en/LC_MESSAGES/django.po"],  # optional: gettext sources (MIG)
        "SNAPSHOT": "langsys/catalog.snapshot.json",         # optional: seeded at startup
    }

Every value is optional here; the underlying SDK also falls back to ``LANGSYS_*`` env vars.
``QUERY_PARAM`` and ``COOKIE_NAME`` only say where a request carries its locale; which locale is
served, and in what order the candidates are tried, is the SDK's. ``RESPONSE_KEY`` names the key a
failed form's or serializer's server-message entries sit under, beside Django's or DRF's own errors.
``LEGACY_FILES`` names an app's source translation files, each a path or a
``{"path": …, "format": …, "namespace": …}`` mapping, for the core to resolve legacy keys against.
``SNAPSHOT`` names a catalog snapshot the core loads when Django starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from django.conf import settings
from langsys.migrate import LegacyFile


@dataclass(frozen=True)
class LangsysSettings:
    api_key: Optional[str]
    project_id: Optional[str]
    api_url: Optional[str]
    base_locale: Optional[str]
    query_param: str
    cookie_name: str
    response_key: Optional[str]
    legacy_files: tuple[LegacyFile, ...]
    snapshot: Optional[str]


def get_settings() -> LangsysSettings:
    raw: dict[str, Any] = getattr(settings, "LANGSYS", {}) or {}
    return LangsysSettings(
        api_key=raw.get("API_KEY"),
        project_id=raw.get("PROJECT_ID"),
        api_url=raw.get("API_URL"),
        base_locale=raw.get("BASE_LOCALE"),
        query_param=raw.get("QUERY_PARAM", "locale"),
        cookie_name=raw.get("COOKIE_NAME", "langsys_locale"),
        response_key=raw.get("RESPONSE_KEY"),
        legacy_files=tuple(_legacy_file(item) for item in raw.get("LEGACY_FILES") or ()),
        snapshot=raw.get("SNAPSHOT"),
    )


def _legacy_file(item: Any) -> LegacyFile:
    return LegacyFile(**item) if isinstance(item, dict) else LegacyFile(item)
