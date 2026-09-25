"""CONF-3 mutation harness: break one fix in a copy of ``src/``, run the suite, report what reddens.

Each mutation copies ``src/`` to a scratch directory, applies edits that must each match exactly
once, and runs the suite with that copy imported ahead of the installed package
(``PYTHONPATH``) — so the absence probes read the mutated code too. A fix whose mutation reddens
no named test has a test that cannot fail.

Run from the repo root: ``.venv/bin/python _dev_/mutations.py``. The conformance-file tests are
excluded; they read ``CONFORMANCE.md``, which no mutation touches.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "langsys_django"

Edit = tuple[str, str, str]

MUTATIONS: list[tuple[str, list[Edit]]] = [
    ("M0 baseline, no edit", []),
    (
        "M1 restore the can_write branch in the request hook",
        [
            (
                "client.py",
                "    client.flush_pending()\n    client.reset_write_decision()\n",
                "    if client.can_write:\n"
                "        client.flush_pending()\n"
                "    else:\n"
                "        client.clear_pending()\n"
                "    client.reset_write_decision()\n",
            )
        ],
    ),
    ("M2 no flush at the request boundary", [("client.py", "    client.flush_pending()\n", "")]),
    (
        "M3 no reset at the request boundary",
        [("client.py", "    client.reset_write_decision()\n", "")],
    ),
    (
        "M4 streamed body not rendered in the request locale",
        [
            (
                "middleware.py",
                "        if locale and response.streaming:\n"
                "            _stream_in_locale(response, locale)\n",
                "",
            )
        ],
    ),
    (
        "M5 process-global locale instead of a ContextVar",
        [
            (
                "locale.py",
                "_current_locale: contextvars.ContextVar[str] = contextvars.ContextVar(\n"
                '    "langsys_current_locale", default=""\n'
                ")\n",
                "class _ProcessGlobal:\n"
                "    value = ''\n\n"
                "    def get(self):\n"
                "        return self.value\n\n"
                "    def set(self, value):\n"
                "        previous, self.value = self.value, value\n"
                "        return previous\n\n"
                "    def reset(self, previous):\n"
                "        self.value = previous\n\n\n"
                "_current_locale = _ProcessGlobal()\n",
            )
        ],
    ),
    (
        "M6 restore an AUTO_FLUSH switch",
        [
            (
                "client.py",
                "    client.flush_pending()\n",
                "    from django.conf import settings\n\n"
                "    if (getattr(settings, 'LANGSYS', {}) or {}).get('AUTO_FLUSH', True):\n"
                "        client.flush_pending()\n"
                "    else:\n"
                "        client.clear_pending()\n",
            )
        ],
    ),
    (
        "M7 the t filter bypasses the core",
        [
            (
                "templatetags/langsys.py",
                "    return _translate(phrase, category)\n",
                "    return phrase\n",
            )
        ],
    ),
    (
        "M8 the tag takes Django's empty string for a missing argument",
        [("templatetags/langsys.py", "ignore_failures=True", "ignore_failures=False")],
    ),
    (
        "M9 the settings API_URL never reaches the core",
        [("client.py", "                    api_url=cfg.api_url,\n", "")],
    ),
    (
        "M10 end the request scope when the middleware returns",
        [
            (
                "middleware.py",
                "        _end_scope_on_close(response, scope)\n",
                "        end_request_scope(scope)\n",
            )
        ],
    ),
    (
        "M11 no request scope: end it as soon as it opens",
        [
            (
                "middleware.py",
                "        scope = begin_request_scope()\n",
                "        scope = begin_request_scope()\n        end_request_scope(scope)\n",
            )
        ],
    ),
    (
        "M12 never end the request scope",
        [
            (
                "middleware.py",
                "        end_request_scope(scope)\n        close()\n",
                "        close()\n",
            )
        ],
    ),
    (
        "M13 a view that raises leaves its scope open",
        [
            (
                "middleware.py",
                "            if not served:\n                end_request_scope(scope)\n",
                "",
            )
        ],
    ),
    (
        "M14 the response carries no Vary",
        [
            (
                "middleware.py",
                "        if choice.vary:\n            patch_vary_headers(response, choice.vary)\n",
                "",
            )
        ],
    ),
    (
        "M15 the cookie outranks the URL",
        [
            (
                "middleware.py",
                "            url=self._url_locale(request),\n"
                "            cookie=request.COOKIES.get(self._cfg.cookie_name),\n",
                "            url=request.COOKIES.get(self._cfg.cookie_name),\n"
                "            cookie=self._url_locale(request),\n",
            )
        ],
    ),
    (
        "M16 the chosen locale is written back to a cookie",
        [
            (
                "middleware.py",
                "        return response\n\n    def _url_locale",
                "        response.set_cookie(self._cfg.cookie_name, locale)\n"
                "        return response\n\n    def _url_locale",
            )
        ],
    ),
    (
        "M17 any path prefix counts as a locale",
        [
            (
                "middleware.py",
                "        return get_language_from_path(request.path_info) if prefixed else None\n",
                "        return get_language_from_path(request.path_info)\n",
            )
        ],
    ),
]


def run(name: str, edits: list[Edit], scratch: pathlib.Path) -> dict[str, object]:
    root = scratch / re.sub(r"\W+", "-", name)
    shutil.copytree(REPO / "src", root / "src")
    for relative, old, new in edits:
        target = root / "src" / PACKAGE / relative
        text = target.read_text()
        if text.count(old) != 1:
            raise SystemExit(f"{name}: expected one match in {relative}, found {text.count(old)}")
        target.write_text(text.replace(old, new))
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    imported = subprocess.run(
        [sys.executable, "-c", f"import {PACKAGE}; print({PACKAGE}.__file__)"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not imported.startswith(str(root)):
        raise SystemExit(f"{name}: the mutated copy was not imported ({imported})")
    suite = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-rfE",
            "--ignore=tests/test_conformance_file.py",
            "tests",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    lines = suite.stdout.strip().splitlines()
    return {
        "mutation": name,
        "summary": lines[-1] if lines else "?",
        "red": sorted(set(re.findall(r"^FAILED (tests/\S+)", suite.stdout, re.M))),
        "errors": sorted(set(re.findall(r"^ERROR (tests/\S+)", suite.stdout, re.M))),
    }


def main() -> int:
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="langsys-django-mutations-"))
    try:
        for name, edits in MUTATIONS:
            print(json.dumps(run(name, edits, scratch)), flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
