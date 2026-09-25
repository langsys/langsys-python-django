"""CONFORMANCE.md is checked, not trusted.

Every spec rule is graded exactly once, in the vocabulary the fleet checker reads, and the evidence
resolves to something that exists: a delegated row names its core row and a probe that covers it,
every test it cites is a real test, and the summary counts are the table's own counts.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from collections import Counter
from typing import Optional

import pytest

from tests.test_probes import PROBES

REPO = pathlib.Path(__file__).resolve().parents[1]
FILE = REPO / "CONFORMANCE.md"
LANGSYS2 = REPO.parent / "langsys2"
SPEC_COMMIT = "a1b7568c7ebcc53d074d30665e2195c122ea6978"
SPEC_BLOB = "b0474afba2c9c1639baa8da219fa6a441b3e1c2f"

SPEC_REVISION_ROW = (
    "| **Spec revision read** | langsys2 a1b7568c…, docs/sdk-spec.mdx blob "
    "b0474afba2c9c1639baa8da219fa6a441b3e1c2f |"
)
PROFILES_ROW = (
    "| **Profiles** | server, binding, all — binding over langsys-python, per the spec's per-SDK "
    "profile table |"
)
TABLE_HEADER = "| Rule | Status | Tier | Evidence |"
BINDING_PROFILES = {"server", "binding", "all"}

#: The rule ids of docs/sdk-spec.mdx at SPEC_BLOB, in document order.
SPEC_RULES = [
    "GATE-1",
    "GATE-2",
    "GATE-3",
    "GATE-4",
    "GATE-5",
    "GATE-6",
    "GATE-7",
    "GATE-8",
    "GATE-9",
    "GATE-10",
    "CAT-1",
    "CAT-2",
    "CAT-3",
    "REG-1",
    "REG-2",
    "REG-3",
    "REG-4",
    "REG-5",
    "REG-6",
    "REG-7",
    "REG-8",
    "REG-9",
    "REG-10",
    "REG-11",
    "REG-12",
    "REG-13",
    "HINT-1",
    "HINT-2",
    "HINT-3",
    "HINT-4",
    "HINT-5",
    "HINT-6",
    "HINT-7",
    "HINT-8",
    "HINT-9",
    "HINT-10",
    "HINT-11",
    "HINT-12",
    "HINT-13",
    "ICU-1",
    "ICU-2",
    "ICU-3",
    "ICU-4",
    "ICU-5",
    "ICU-6",
    "CID-1",
    "CID-2",
    "CID-3",
    "CID-4",
    "TOK-1",
    "TOK-2",
    "TOK-3",
    "TOK-4",
    "TOK-5",
    "TOK-6",
    "MARK-1",
    "MARK-2",
    "MARK-3",
    "MARK-4",
    "SSR-1",
    "SSR-2",
    "SSR-3",
    "SRV-1",
    "SRV-2",
    "SRV-3",
    "SRV-4",
    "SRV-5",
    "SRV-6",
    "MSG-1",
    "MSG-2",
    "MSG-3",
    "MSG-4",
    "MSG-5",
    "MSG-6",
    "MSG-7",
    "MSG-8",
    "MSG-9",
    "MSG-10",
    "MSG-11",
    "MSG-12",
    "MIG-1",
    "MIG-2",
    "MIG-3",
    "MIG-4",
    "MIG-5",
    "MIG-6",
    "MIG-7",
    "MIG-8",
    "MIG-9",
    "SNAP-1",
    "SNAP-2",
    "SNAP-3",
    "BIND-1",
    "BIND-2",
    "BIND-3",
    "BIND-4",
    "BIND-5",
    "BIND-6",
    "GRANT-1",
    "GRANT-2",
    "GRANT-3",
    "GRANT-4",
    "CACHE-1",
    "CACHE-2",
    "OBS-1",
    "WIRE-1",
    "WIRE-2",
    "WIRE-3",
    "WIRE-4",
    "WIRE-5",
    "CONF-1",
    "CONF-2",
    "CONF-3",
]

STATUS = re.compile(
    r"implemented|provisional|delegated|partial|not implemented|held \(strip ruling\)|waived"
    r"|n/a \(profile: [a-z]+(, [a-z]+)*\)|n/a \(architecture: .+\)"
)
TIERS = {"live", "contract", "mock", "n/a (pure)", "-"}


def _cells(line: str) -> list[str]:
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def status_rows() -> list[tuple[str, str, str, str]]:
    lines = FILE.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(TABLE_HEADER))
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = _cells(line)
        assert len(cells) == 4, f"a status row must have four cells: {line}"
        rows.append((cells[0], cells[1], cells[2], cells[3]))
    return rows


def spec_text() -> Optional[str]:
    try:
        shown = subprocess.run(
            ["git", "-C", str(LANGSYS2), "cat-file", "-p", SPEC_BLOB],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return shown.stdout


def test_the_header_names_the_revision_and_profiles_read():
    text = FILE.read_text()
    assert SPEC_REVISION_ROW in text
    assert PROFILES_ROW in text


def test_the_pinned_rule_list_is_the_spec_blob_itself():
    """Re-derived from the blob when langsys2 is on this machine, never carried."""
    spec = spec_text()
    if spec is None:
        pytest.skip("langsys2 is not available to re-derive the rule list")
    commit_blob = subprocess.run(
        ["git", "-C", str(LANGSYS2), "rev-parse", f"{SPEC_COMMIT}:docs/sdk-spec.mdx"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert commit_blob == SPEC_BLOB
    assert re.findall(r"^### ([A-Z]+-\d+) ", spec, re.M) == SPEC_RULES


def test_every_rule_is_graded_exactly_once_in_spec_order():
    assert [row[0] for row in status_rows()] == SPEC_RULES


def test_every_status_and_tier_is_in_the_checker_vocabulary():
    for rule, status, tier, _ in status_rows():
        assert STATUS.fullmatch(status), f"{rule}: status {status!r}"
        assert tier in TIERS, f"{rule}: tier {tier!r}"


def test_a_delegated_row_names_its_core_row_and_a_probe_that_covers_it():
    probes = {probe.name: probe for probe in PROBES}
    for rule, status, tier, evidence in status_rows():
        if status != "delegated":
            continue
        assert tier == "-", f"{rule}: a delegated row takes tier '-'"
        assert f"core `{rule}`" in evidence, f"{rule}: names no core row"
        # "Probe" opens a sentence as often as "probe" continues one.
        named = re.findall(r"[Pp]robe `([a-z-]+)`", evidence)
        assert named, f"{rule}: names no absence probe"
        for name in named:
            assert name in probes, f"{rule}: unknown probe {name!r}"
        assert any(rule in probes[name].rules for name in named), (
            f"{rule}: none of {named} lists this rule"
        )


def test_every_probe_is_cited_by_the_rows_it_lists():
    rows = {rule: evidence for rule, _, _, evidence in status_rows()}
    for probe in PROBES:
        for rule in probe.rules:
            assert re.search(rf"[Pp]robe `{re.escape(probe.name)}`", rows[rule]), (
                f"{rule} does not cite probe {probe.name!r}"
            )


def test_every_cited_test_exists():
    defined = set()
    for path in (REPO / "tests").glob("test_*.py"):
        defined |= set(re.findall(r"^def (test_\w+)", path.read_text(), re.M))
    cited = set(re.findall(r"\b(test_[A-Za-z0-9_]+)\b", FILE.read_text())) - {
        path.stem for path in (REPO / "tests").glob("test_*.py")
    }
    assert cited, "the file cites no tests at all"
    assert cited <= defined, f"cited but not defined: {sorted(cited - defined)}"


def test_a_row_with_runtime_evidence_cites_a_test():
    for rule, status, _, evidence in status_rows():
        if status in {"implemented", "partial", "provisional"}:
            assert re.search(r"\btest_\w+", evidence), f"{rule}: {status} without a named test"


def test_profile_rows_agree_with_each_rules_profiles_line():
    spec = spec_text()
    if spec is None:
        pytest.skip("langsys2 is not available to read the Profiles lines")
    sections = re.split(r"^### (?=[A-Z]+-\d+ )", spec, flags=re.M)[1:]
    applies = {}
    for section in sections:
        rule = section.split(" ", 1)[0]
        line = re.search(r"^\*\*Profiles:\*\*(.*)$", section, re.M)
        assert line, f"{rule} has no Profiles line"
        applies[rule] = bool(set(re.findall(r"[a-z]+", line.group(1))) & BINDING_PROFILES)
    for rule, status, _, _ in status_rows():
        by_profile = status.startswith("n/a (profile:")
        assert by_profile != applies[rule], f"{rule}: {status!r} disagrees with its Profiles line"


def test_the_summary_counts_are_the_tables_counts():
    counted = Counter(
        "n/a (profile)"
        if status.startswith("n/a (profile")
        else "n/a (architecture)"
        if status.startswith("n/a (architecture")
        else status
        for _, status, _, _ in status_rows()
    )
    text = FILE.read_text()
    summary = text[text.index("## Summary") :]
    stated = {
        name: int(count) for name, count in re.findall(r"^\| `([^`]+)` \| (\d+) \|", summary, re.M)
    }
    assert stated == dict(counted)
    assert sum(stated.values()) == len(SPEC_RULES)
