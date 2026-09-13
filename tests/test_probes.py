"""Absence probes for every rule this binding delegates to its core.

A binding that reimplements behaviour ships the same bug twice, so each ``delegated`` row in
``CONFORMANCE.md`` names one of these probes. A probe is a pattern that must match nothing in
this binding's code **and** must match something real — the core, or this binding before its
838 fixes — because a probe that cannot fire proves nothing.

Code only: comments and docstrings are stripped with ``ast`` before matching, so prose that
merely mentions a behaviour does not count, while a string literal that implements one (an
endpoint path, an attribute name) still does.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

import langsys
import pytest

import langsys_django

REPO = pathlib.Path(__file__).resolve().parents[1]
#: The package as imported, not the repo path: a mutated copy placed ahead of the installed
#: package (see ``_dev_/mutations.py``) is then what the probes read.
BINDING = pathlib.Path(langsys_django.__file__).resolve().parent
CORE = pathlib.Path(langsys.__file__).resolve().parent
#: This binding before the 838 fixes: the firing control for the defects it actually had.
PRE_FIX = "34a6a87"
TS_CORE = REPO.parent / "langsys-js-typescript" / "src"


@dataclass(frozen=True)
class Probe:
    name: str
    rules: tuple[str, ...]
    pattern: str
    #: ``(kind, where)``: ``core`` is a path inside the installed ``langsys`` package,
    #: ``pre-fix`` a path in this repo at ``PRE_FIX``, ``ts-core`` a file in the TypeScript core
    #: checkout, ``synthetic`` a literal line that implements the behaviour.
    controls: tuple[tuple[str, str], ...]


PROBES = (
    Probe(
        "capability",
        ("GATE-1", "GATE-8", "BIND-2"),
        r"\b(can_write|write_enabled|key_type|KeyType|_resolve_write_enabled|_observed_decision)\b",
        (("pre-fix", "src/langsys_django/middleware.py"), ("core", "client.py")),
    ),
    Probe(
        "queue-mutation",
        ("GATE-2", "GATE-5", "REG-6"),
        r"\b(clear_pending|_pending|_pending_blocks|pending_phrases|pending_content_blocks)\b",
        (("pre-fix", "src/langsys_django/middleware.py"), ("core", "client.py")),
    ),
    Probe(
        "cache",
        ("GATE-4", "CACHE-1", "BIND-5"),
        r"(?i)cache|memoi[sz]",
        (("core", "catalog.py"),),
    ),
    Probe(
        "registration-request",
        ("REG-1", "GATE-6"),
        r"translatable-items|\bregister_phrases\b|\bregister_content_blocks\b|\.post\(",
        (("core", "registration.py"),),
    ),
    Probe(
        "report-lane",
        ("HINT-2", "GATE-6"),
        r"(?i)discovery|\bhint",
        (
            ("synthetic", "self._http.post('discovery/hint', json={'page_url': url})"),
            ("ts-core", "api.ts"),
        ),
    ),
    Probe(
        "catalog-read",
        ("CAT-1", "CAT-2", "GATE-7"),
        # `resolve(` as a function only: the core reads its catalog with `resolve(catalog, ...)`,
        # while `var.resolve(context)` is Django resolving a template variable.
        r"\b(get_translations|lookup_block|_catalog|catalog)\b|(?<![.\w])resolve\(",
        (("core", "client.py"),),
    ),
    Probe(
        "scheduling",
        ("REG-2", "REG-8", "BIND-3"),
        r"\b(Timer|sleep|debounce|monotonic|backoff\w*|retry|retries|interval)\b",
        (("core", "client.py"),),
    ),
    Probe(
        "network-client",
        ("BIND-3",),
        r"\b(httpx|requests|urllib|socket|aiohttp)\b",
        (("core", "http.py"),),
    ),
    Probe(
        "send-concurrency",
        ("REG-7",),
        r"\b(_sending|acquire|in_flight)\b",
        (("core", "client.py"),),
    ),
    Probe(
        "batching",
        ("REG-9",),
        r"\b(batch_limit|batch|translatable_items)\b",
        (("core", "registration.py"),),
    ),
    Probe(
        "failure-shape",
        ("REG-10",),
        r"except Exception\b|'success'",
        (("pre-fix", "src/langsys_django/middleware.py"), ("core", "client.py")),
    ),
    Probe(
        "ellipsis",
        ("REG-11",),
        r"(?i)…|ellipsis",
        (("core", "client.py"),),
    ),
    Probe(
        "block-identity",
        ("REG-12", "CAT-3", "CID-1", "CID-2", "CID-3", "CID-4"),
        r"\b(custom_id\w*|content_block\w*|md5\w*|hashlib)\b",
        (("core", "client.py"),),
    ),
    Probe(
        "interpolation",
        ("ICU-1", "ICU-2", "ICU-3", "ICU-4", "ICU-5", "TOK-5"),
        r"(?i)\binterpolat\w*|\bplural\b|messageformat|\bbabel\b|\.format\(",
        (("core", "interpolate.py"),),
    ),
    Probe(
        "tokenizer",
        ("TOK-1", "TOK-2", "TOK-3", "TOK-4"),
        r"\b(lxml|etree|tokeniz\w*|extract_phrases|translatable_attributes)\b",
        (("core", "client.py"),),
    ),
    Probe(
        "identity-stamping",
        ("MARK-1", "MARK-2"),
        r"data-ls-|data-langsys-|\bstamp_\w+",
        (("core", "client.py"),),
    ),
    Probe(
        "auth-header",
        ("WIRE-1",),
        r"(?i)authorization|\bheaders\b",
        (("core", "http.py"),),
    ),
    Probe(
        "response-parsing",
        ("WIRE-2",),
        r"json\.loads|\.json\(\)|\bstatus_code\b",
        (("core", "http.py"),),
    ),
    Probe(
        "identifier-normalisation",
        ("WIRE-3",),
        r"\.lower\(\)|__uncategorized__|\bUNCATEGORIZED\b|\bnormalize_locale\b",
        (("core", "catalog.py"),),
    ),
    Probe(
        "raising",
        ("WIRE-4",),
        r"\braise\b",
        (("core", "http.py"),),
    ),
    Probe(
        "diagnostics",
        ("OBS-1",),
        r"\b(logger|logging|warn\w*)\b",
        (("pre-fix", "src/langsys_django/middleware.py"), ("core", "client.py")),
    ),
    Probe(
        "binding-discovery-switch",
        ("BIND-4",),
        r"\bAUTO_FLUSH\b|\bauto_flush\b",
        (("pre-fix", "src/langsys_django/conf.py"),),
    ),
)


def code_of(source: str) -> str:
    """Source with comments and docstrings removed; every other string literal kept."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def binding_code() -> dict[str, str]:
    return {
        str(path.relative_to(BINDING.parent)): code_of(path.read_text())
        for path in sorted(BINDING.rglob("*.py"))
    }


def control_text(kind: str, where: str) -> Optional[str]:
    """The control's text, or ``None`` when it is not available on this machine."""
    if kind == "core":
        return code_of((CORE / where).read_text())
    if kind == "synthetic":
        return where
    if kind == "pre-fix":
        try:
            shown = subprocess.run(
                ["git", "-C", str(REPO), "show", f"{PRE_FIX}:{where}"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return code_of(shown.stdout)
    if kind == "ts-core":
        path = TS_CORE / where
        return path.read_text() if path.exists() else None
    raise AssertionError(f"unknown control kind {kind!r}")


def test_the_binding_code_is_what_the_probes_read():
    """Positive control on the reader itself: without it, an empty read passes every probe."""
    code = binding_code()
    assert "langsys_django/middleware.py" in code
    assert re.search(r"\bclass LangsysMiddleware\b", code["langsys_django/middleware.py"])


@pytest.mark.parametrize("probe", PROBES, ids=lambda probe: probe.name)
def test_probe_matches_nothing_in_this_binding(probe):
    hits = {
        path: sorted({match.group(0) for match in re.finditer(probe.pattern, text)})
        for path, text in binding_code().items()
        if re.search(probe.pattern, text)
    }
    assert not hits, f"probe {probe.name!r} ({', '.join(probe.rules)}) fired in the binding: {hits}"


@pytest.mark.parametrize("probe", PROBES, ids=lambda probe: probe.name)
def test_probe_fires_on_its_control(probe):
    ran = []
    for kind, where in probe.controls:
        text = control_text(kind, where)
        if text is None:
            continue
        assert re.search(probe.pattern, text), (
            f"probe {probe.name!r} did not fire on {kind}:{where}"
        )
        ran.append(f"{kind}:{where}")
    assert ran, f"no control for probe {probe.name!r} is available, so it proves nothing"
