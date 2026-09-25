"""``python manage.py langsys_messages --provider app.module:callable [--register]`` (MSG-7).

Lists every server-message template the providers declare, one per line, then one ``PROBLEM`` line
for each message that cannot be listed ahead of time. The exit status is non-zero when there is any
problem, so the command can gate CI. With ``--register`` the templates the catalog lacks are
registered through the app's own Langsys client. The listing itself is the core's.
"""

from __future__ import annotations

import sys
from importlib import import_module
from typing import Any, Callable

from django.core.management.base import BaseCommand, CommandParser
from langsys.messages import run_listing

from ...client import get_client


class Command(BaseCommand):
    help = "List the server-message templates the providers declare; register them with --register."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--provider",
            action="append",
            required=True,
            help="package.module:callable returning the declared templates; repeatable",
        )
        parser.add_argument(
            "--register",
            action="store_true",
            help="register the templates Langsys does not have yet, through the app's client",
        )
        parser.add_argument("--category", help="the category to register under")

    def handle(self, *args: Any, **options: Any) -> None:
        providers = [_provider(spec) for spec in options["provider"]]
        client = get_client() if options["register"] else None
        category = options["category"] or (client.message_category if client is not None else None)
        extra = {"category": category} if category else {}
        status = run_listing(providers, client=client, register=options["register"], **extra)
        if status:
            sys.exit(status)


def _provider(spec: str) -> Callable[[], Any]:
    module, _, name = spec.partition(":")
    provider: Callable[[], Any] = getattr(import_module(module), name)
    return provider
