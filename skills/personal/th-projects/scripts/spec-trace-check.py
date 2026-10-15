#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""spec-trace-check.py — mechanical traceability validator for OpenSpec trees.

Validates the spec side of the doctrine -> RFC -> spec -> test chain defined
in th-projects references/spec-format.md:

  ERRORS (always fail):
    - unsupported H2 headings for the detected main/delta spec kind
    - requirement with no scenario (main specs; delta ADDED/MODIFIED)
    - scenario without exactly 1 WHEN and 1 THEN bullet
    - duplicate or malformed requirement IDs (ID spec-name must match dir)
    - stale test citations: REQ- IDs cited in tests but absent from specs
    - requirement block ordering is not heading, normative SHALL/MUST
      paragraph, contiguous ID / Source / Scope, then scenarios

  WARNINGS (field warnings are errors under --authoring or --strict):
    - requirement missing ID / Source / Scope lines (legacy specs)
    - v1-mandatory requirement with no test citation (when a test tree exists)

Usage:
  uv run spec-trace-check.py <repo-root> [--tests-dir DIR ...] [--authoring] [--strict]

Exit 0 = clean (warnings allowed unless --strict). Exit 1 = findings.
Exit 2 = no openspec/ tree found.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAIN_H2 = {"Purpose", "Requirements"}
DELTA_H2 = {
    "ADDED Requirements",
    "MODIFIED Requirements",
    "REMOVED Requirements",
    "RENAMED Requirements",
}
SCENARIO_FREE_OPS = {"REMOVED Requirements", "RENAMED Requirements"}
ID_LINE_RE = re.compile(r"^ID:\s*(\S+)\s*$")
ID_FORMAT_RE = re.compile(r"^REQ-([a-z0-9-]+)-(\d{3})$")
SOURCE_LINE_RE = re.compile(r"^Source:\s*\S")
SCOPE_LINE_RE = re.compile(r"^Scope:\s*(v1-mandatory|v1-reserved|post-v1)\s*$")
NORMATIVE_RE = re.compile(r"\b(?:SHALL|MUST)\b")
TEST_ID_RE = re.compile(r"REQ[-_][a-z0-9]+(?:[-_][a-z0-9]+)*[-_]\d{3}", re.IGNORECASE)
TEST_FILE_HINT = re.compile(r"(^|[._-])(test|spec)s?([._-]|$)", re.IGNORECASE)
TEST_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".sh", ".ex", ".exs", ".cs", ".swift"}


@dataclass
class Requirement:
    title: str
    spec_name: str
    file: Path
    line: int
    op: str  # "Requirements", "ADDED Requirements", ...
    req_id: str | None = None
    id_line: int | None = None
    has_source: bool = False
    scope: str | None = None
    normative_first: bool = False
    metadata_ordered: bool = False
    metadata_before_scenarios: bool = True
    scenarios_after_metadata: bool = True
    scenarios: int = 0


@dataclass
class Findings:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def error(self, path, line, msg):
        self.errors.append(f"ERROR  {path}:{line}: {msg}")

    def warn(self, path, line, msg):
        self.warnings.append(f"WARN   {path}:{line}: {msg}")


def parse_spec(path: Path, spec_name: str, is_delta: bool, f: Findings,
               *, allow_delta_purpose: bool = False) -> list:
    """Parse one spec.md; emit structural findings; return requirements."""
    rel = path
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    reqs: list[Requirement] = []
    cur: Requirement | None = None
    cur_op = None
    scen_line = None
    when_count = then_count = 0

    def close_scenario():
        nonlocal scen_line, when_count, then_count
        if scen_line is not None and (when_count != 1 or then_count != 1):
            f.error(rel, scen_line,
                    f"scenario needs exactly 1 WHEN and 1 THEN bullet (found {when_count} WHEN, {then_count} THEN)")
        scen_line, when_count, then_count = None, 0, 0

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        m2 = re.match(r"^## (.+)$", line)
        if m2:
            close_scenario()
            cur = None
            h2 = m2.group(1).strip()
            cur_op = h2
            allowed = DELTA_H2 if is_delta else MAIN_H2
            if is_delta and allow_delta_purpose:
                allowed = allowed | {"Purpose"}
            if h2 not in allowed:
                if is_delta and h2 in MAIN_H2:
                    msg = f"delta spec uses main-spec heading '## {h2}'"
                elif not is_delta and h2 in DELTA_H2:
                    msg = f"main spec uses delta heading '## {h2}'"
                else:
                    kind = "delta" if is_delta else "main"
                    expected = ", ".join(f"## {name}" for name in sorted(allowed))
                    msg = f"unsupported {kind}-spec H2 '## {h2}' (allowed: {expected})"
                f.error(rel, i, msg)
            continue
        m3 = re.match(r"^### Requirement:\s*(.+)$", line)
        if m3:
            close_scenario()
            if is_delta and cur_op == "Purpose":
                f.error(rel, i, "delta requirement must follow a delta-operation heading, not '## Purpose'")
            cur = Requirement(title=m3.group(1).strip(), spec_name=spec_name,
                              file=path, line=i, op=cur_op or "?")
            block_end = len(lines)
            for candidate in range(i, len(lines)):
                if re.match(r"^##(?:# Requirement:)?\s", lines[candidate]):
                    block_end = candidate
                    break
            block = lines[i:block_end]
            nonblank = [offset for offset, value in enumerate(block) if value.strip()]
            scenario_offsets = [
                offset for offset, value in enumerate(block)
                if re.match(r"^#### Scenario:", value)
            ]
            first_scenario = scenario_offsets[0] if scenario_offsets else len(block)

            id_offsets = []
            source_offsets = []
            scope_offsets = []
            for offset, value in enumerate(block):
                mid = ID_LINE_RE.match(value)
                if mid:
                    id_offsets.append(offset)
                    if cur.req_id is None:
                        cur.req_id, cur.id_line = mid.group(1), i + offset + 1
                if SOURCE_LINE_RE.match(value):
                    source_offsets.append(offset)
                    cur.has_source = True
                msc = SCOPE_LINE_RE.match(value)
                if msc:
                    scope_offsets.append(offset)
                    if cur.scope is None:
                        cur.scope = msc.group(1)
                elif value.startswith("Scope:"):
                    f.error(path, i + offset + 1,
                            f"invalid Scope value: {value!r} (use v1-mandatory | v1-reserved | post-v1)")

            if nonblank:
                paragraph_start = nonblank[0]
                paragraph_end = paragraph_start
                while paragraph_end + 1 < len(block) and block[paragraph_end + 1].strip():
                    paragraph_end += 1
                paragraph = "\n".join(block[paragraph_start:paragraph_end + 1])
                cur.normative_first = NORMATIVE_RE.search(paragraph) is not None

                metadata_start = paragraph_end + 1
                while metadata_start < len(block) and not block[metadata_start].strip():
                    metadata_start += 1
                expected = block[metadata_start:metadata_start + 3]
                cur.metadata_ordered = (
                    cur.normative_first
                    and len(expected) == 3
                    and ID_LINE_RE.match(expected[0]) is not None
                    and SOURCE_LINE_RE.match(expected[1]) is not None
                    and SCOPE_LINE_RE.match(expected[2]) is not None
                )
                if cur.metadata_ordered and scenario_offsets:
                    after_metadata = metadata_start + 3
                    while after_metadata < len(block) and not block[after_metadata].strip():
                        after_metadata += 1
                    cur.scenarios_after_metadata = after_metadata == first_scenario

            metadata_offsets = id_offsets + source_offsets + scope_offsets
            if metadata_offsets:
                cur.metadata_before_scenarios = max(metadata_offsets) < first_scenario
            reqs.append(cur)
            continue
        m4 = re.match(r"^#### Scenario:", line)
        if m4:
            close_scenario()
            scen_line = i
            if cur:
                cur.scenarios += 1
            continue
        if scen_line is not None:
            if re.match(r"^-\s+\*\*WHEN\*\*", line):
                when_count += 1
            elif re.match(r"^-\s+\*\*THEN\*\*", line):
                then_count += 1
            continue
    close_scenario()
    return reqs


def check_requirements(reqs, f: Findings, strict_missing_fields: bool):
    seen_ids: dict[str, Requirement] = {}
    restated_ids: set[str] = set()
    for r in reqs:
        rel, needs_scenarios = r.file, r.op not in SCENARIO_FREE_OPS
        if needs_scenarios and r.scenarios == 0:
            f.error(rel, r.line, f"requirement '{r.title}' has no scenarios")
        if r.req_id:
            m = ID_FORMAT_RE.match(r.req_id)
            if not m:
                f.error(rel, r.id_line, f"malformed ID '{r.req_id}' (expected REQ-{r.spec_name}-NNN)")
            elif m.group(1) != r.spec_name:
                f.error(rel, r.id_line, f"ID '{r.req_id}' names spec '{m.group(1)}' but lives in spec '{r.spec_name}'")
            elif r.req_id in seen_ids:
                prev = seen_ids[r.req_id]
                restates_existing = r.op in {
                    "MODIFIED Requirements",
                    "REMOVED Requirements",
                }
                if (
                    prev.op == "Requirements"
                    and restates_existing
                    and prev.title == r.title
                    and r.req_id not in restated_ids
                ):
                    restated_ids.add(r.req_id)
                else:
                    f.error(rel, r.id_line, f"duplicate ID '{r.req_id}' (also at {prev.file}:{prev.line})")
            else:
                seen_ids[r.req_id] = r
        field_report = f.error if strict_missing_fields else f.warn
        missing = [name for name, ok in
                   (("ID", r.req_id), ("Source", r.has_source), ("Scope", r.scope)) if not ok]
        if missing:
            field_report(rel, r.line, f"requirement '{r.title}' missing {'/'.join(missing)} line(s)")
        if not r.normative_first:
            f.error(rel, r.line,
                    f"requirement '{r.title}' must place a normative SHALL/MUST paragraph before ID/Source/Scope")
        elif not r.metadata_before_scenarios:
            f.error(rel, r.line,
                    f"requirement '{r.title}' must place ID, Source, Scope before its first scenario")
        elif not missing and not r.metadata_ordered:
            f.error(rel, r.line,
                    f"requirement '{r.title}' must place contiguous ID, Source, Scope after its normative paragraph")
        elif not missing and needs_scenarios and not r.scenarios_after_metadata:
            f.error(rel, r.line,
                    f"requirement '{r.title}' must place scenarios immediately after ID, Source, Scope")
    return seen_ids


def find_test_files(repo: Path, tests_dirs):
    roots = [repo / d for d in tests_dirs] if tests_dirs else [
        p for p in repo.rglob("*") if p.is_dir() and p.name in ("tests", "test")
        and "node_modules" not in p.parts and ".git" not in p.parts
        and len(p.relative_to(repo).parts) <= 3
    ]
    files = []
    for root in roots:
        if not root.is_dir():
            print(f"WARN   tests dir not found: {root}", file=sys.stderr)
            continue
        files.extend(p for p in root.rglob("*")
                     if p.is_file() and p.suffix in TEST_SUFFIXES and TEST_FILE_HINT.search(p.name))
    return files


def normalize_test_id(raw: str) -> str:
    return raw.upper().replace("_", "-").replace("REQ-", "REQ-", 1).lower().replace("req-", "REQ-", 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo_root", type=Path)
    ap.add_argument("--tests-dir", action="append", default=[],
                    help="test directory relative to repo root (repeatable; default: auto-detect tests/ + test/)")
    ap.add_argument("--strict", action="store_true",
                    help="treat missing ID/Source/Scope and uncovered v1-mandatory requirements as errors")
    ap.add_argument("--authoring", action="store_true",
                    help="treat missing ID/Source/Scope as errors without requiring implementation test coverage")
    args = ap.parse_args()

    repo = args.repo_root.resolve()
    openspec = repo / "openspec"
    if not openspec.is_dir():
        print(f"no openspec/ tree at {repo}", file=sys.stderr)
        return 2

    f = Findings()
    reqs: list[Requirement] = []

    for spec in sorted(openspec.glob("specs/*/spec.md")):
        parsed = parse_spec(spec, spec.parent.name, is_delta=False, f=f)
        if (args.authoring or args.strict) and not parsed:
            f.error(spec, 1, "spec file has no requirements")
        reqs += parsed
    for spec in sorted(openspec.glob("changes/*/specs/*/spec.md")):
        if "archive" in spec.relative_to(openspec).parts:
            continue
        # OpenSpec 1.9 carries Purpose into a new main spec; it does not update
        # an existing canonical Purpose through a delta.
        canonical = openspec / "specs" / spec.parent.name / "spec.md"
        parsed = parse_spec(spec, spec.parent.name, is_delta=True, f=f,
                            allow_delta_purpose=not canonical.exists())
        if (args.authoring or args.strict) and not parsed:
            f.error(spec, 1, "spec file has no requirements")
        reqs += parsed

    if not reqs and not f.errors:
        if args.authoring or args.strict:
            print("ERROR  no requirements found under openspec/ — authoring/strict validation fails closed")
            return 1
        print("no requirements found under openspec/ — nothing to check")
        return 0

    seen_ids = check_requirements(
        reqs, f, strict_missing_fields=args.strict or args.authoring
    )

    test_files = find_test_files(repo, args.tests_dir)
    cited: set[str] = set()
    citations: dict[str, tuple] = {}
    for tf in test_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in TEST_ID_RE.finditer(text):
            rid = normalize_test_id(m.group(0))
            cited.add(rid)
            citations.setdefault(rid, (tf, text[:m.start()].count("\n") + 1))

    for rid, (tf, line) in sorted(citations.items()):
        if rid not in seen_ids:
            f.error(tf, line, f"stale test citation: '{rid}' not found in any spec")

    if test_files:
        report = f.error if args.strict else f.warn
        for rid, r in sorted(seen_ids.items()):
            if r.scope == "v1-mandatory" and rid not in cited:
                report(r.file, r.line, f"v1-mandatory requirement '{rid}' has no test citation")
    else:
        mandatory = [r for r in reqs if r.scope == "v1-mandatory"]
        if args.strict and mandatory:
            for r in mandatory:
                f.error(r.file, r.line,
                        f"strict mode cannot verify test citation for '{r.req_id or r.title}' because no test files were found")
        else:
            print("note: no test tree found — skipping test-citation coverage", file=sys.stderr)

    for msg in f.errors + f.warnings:
        print(msg)
    ok = not f.errors
    print(f"\nspec-trace-check: {len(reqs)} requirements, {len(seen_ids)} IDs, "
          f"{len(f.errors)} error(s), {len(f.warnings)} warning(s) — {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
