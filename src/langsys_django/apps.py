from __future__ import annotations

from django.apps import AppConfig
from django.core.signals import request_finished


class LangsysDjangoConfig(AppConfig):
    name = "langsys_django"
    verbose_name = "Langsys"

    def ready(self) -> None:
        from .client import _finish_request

        request_finished.connect(_finish_request, dispatch_uid="langsys_django.finish_request")
