"""``python manage.py langsys_messages --provider app.module:callable`` (MSG-7).

Options: ``--register``, ``--strict``, ``--category``.

Lists every server-message template the providers declare, one per line. A message that cannot be
listed ahead of time gets a ``PROBLEM`` line naming where it comes from and what would make it
listable; it registers the first time it is emitted, so the command still exits zero unless
``--strict`` asks for a failing exit. A field with no declared label gets an ``ADVICE`` line and
never fails the command. With ``--register`` the templates the catalog lacks are registered through
the app's own Langsys client. A template that still holds one of Django's or DRF's label
placeholders is refused (MSG-11). The listing itself is the core's.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator
from importlib import import_module
from typing import Any, Callable

from django.core.management.base import BaseCommand, CommandParser
from langsys.messages import run_listing

from ...client import get_client
from ...messages import LABEL_PLACEHOLDERS, LabelAdvice


def label_placeholders() -> tuple[str, ...]:
    """Django's label placeholders, and DRF's where DRF is installed."""
    try:
        from ...drf import LABEL_PLACEHOLDERS as DRF_PLACEHOLDERS
    except ImportError:
        return LABEL_PLACEHOLDERS
    return (*LABEL_PLACEHOLDERS, *DRF_PLACEHOLDERS)


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
        parser.add_argument(
            "--strict",
            action="store_true",
            help="exit non-zero when any message cannot be listed ahead of time",
        )
        parser.add_argument("--category", help="the category to register under")

    def handle(self, *args: Any, **options: Any) -> None:
        advice: list[LabelAdvice] = []
        providers = [_without_advice(_provider(spec), advice) for spec in options["provider"]]
        client = get_client() if options["register"] else None
        category = options["category"] or (client.message_category if client is not None else None)
        extra = {"category": category} if category else {}
        status = run_listing(
            providers,
            client=client,
            register=options["register"],
            strict=options["strict"],
            label_placeholders=label_placeholders(),
            **extra,
        )
        for item in advice:
            print(f"ADVICE {item}")
        if status:
            sys.exit(status)


def _provider(spec: str) -> Callable[[], Any]:
    module, _, name = spec.partition(":")
    provider: Callable[[], Any] = getattr(import_module(module), name)
    return provider


def _without_advice(
    provider: Callable[[], Iterable[Any]], advice: list[LabelAdvice]
) -> Callable[[], Iterator[Any]]:
    """The provider's templates and problems, with its label advice set aside to print as advice."""

    def listed() -> Iterator[Any]:
        for item in provider():
            if isinstance(item, LabelAdvice):
                advice.append(item)
            else:
                yield item

    return listed
