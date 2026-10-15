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
  uv run spec-trace-check.py <repo-root> --authoring --affected-change NAME \
      --baseline-ref SHA --findings-json EXTERNAL_FRESH_PATH

Affected admission preserves the complete full-tree verdict and separately reports
its scoped verdict; it requires all three options and excludes --strict.
Exit 0 = clean or affected admission passes (full-tree debt stays explicit).
Exit 1 = findings or affected nonregression failure.
Exit 2 = missing/unsupported source, invalid admission inputs or capture refusal.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import subprocess
import stat
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
    records: list = field(default_factory=list)

    def error(self, path, line, msg, related_paths=()):
        self.errors.append(f"ERROR  {path}:{line}: {msg}")
        self.records.append({"level": "ERROR", "path": str(path), "line": line,
                             "message": msg, "related_paths": list(map(str, related_paths))})

    def warn(self, path, line, msg):
        self.warnings.append(f"WARN   {path}:{line}: {msg}")
        self.records.append({"level": "WARN", "path": str(path), "line": line,
                             "message": msg, "related_paths": []})



def check_grounding_sections(path, lines, is_delta, baseline_exists, f,
                             require_source_references=False):
    """Validate non-operative sections without widening operation semantics."""
    headings = [(i, m.group(1).strip()) for i, raw in enumerate(lines, 1)
                if (m := re.match(r"^## (.+)$", raw.rstrip()))]
    purpose = [i for i, title in headings if title == "Purpose"]
    footer = [i for i, title in headings if title == "Source References"]
    if is_delta and not baseline_exists:
        if len(purpose) != 1:
            f.error(path, 1, "new-capability delta requires exactly one initial Purpose section")
        else:
            start = purpose[0]
            end = next((i for i, _ in headings if i > start), len(lines) + 1)
            before = [raw for raw in lines[:start - 1]
                      if raw.strip() and not raw.startswith("# ")]
            body = "\n".join(lines[start:end - 1]).strip()
            if before or headings[0] != (start, "Purpose"):
                f.error(path, start, "new-capability Purpose must precede all delta operations")
            if len(body) < 50:
                f.error(path, start, "new-capability Purpose needs at least 50 characters")
            if any(re.match(r"^###(?:#)? (?:Requirement|Scenario):", raw)
                   for raw in lines[start:end - 1]):
                f.error(path, start, "Purpose cannot carry operative requirements or scenarios")
    if len(footer) > 1:
        f.error(path, footer[1], "Source References must occur exactly once")
    if require_source_references and not footer:
        f.error(path, 1, "repository requires a final non-empty Source References section")
    if footer:
        start = footer[0]
        if headings[-1] != (start, "Source References"):
            f.error(path, start, "Source References must be the final H2 section")
        content = lines[start:]
        if not any(raw.strip() for raw in content):
            f.error(path, start, "Source References must be non-empty")
        if any(re.match(r"^###(?:#)? (?:Requirement|Scenario):", raw) for raw in content):
            f.error(path, start, "Source References cannot carry operative requirements or scenarios")
    allowed = set(DELTA_H2 if is_delta else MAIN_H2)
    allowed.add("Source References")
    if is_delta and not baseline_exists:
        allowed.add("Purpose")
    return allowed


def parse_spec(path: Path, spec_name: str, is_delta: bool, f: Findings,
               baseline_exists=False, text=None, require_source_references=False) -> list:
    """Parse one spec.md; emit structural findings; return requirements."""
    rel = path
    lines = (decode_body(path.read_bytes(), path)
             if text is None else text).splitlines()
    allowed_h2 = check_grounding_sections(path, lines, is_delta, baseline_exists, f,
                                          require_source_references)
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
            allowed = allowed_h2
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
            cur = Requirement(title=m3.group(1).strip(), spec_name=spec_name,
                              file=path, line=i, op=cur_op or "?")
            if cur_op not in (DELTA_H2 if is_delta else {"Requirements"}):
                if is_delta and cur_op == "Purpose":
                    f.error(rel, i, "delta requirement must follow a delta-operation heading, not '## Purpose'")
                else:
                    f.error(rel, i, "requirement must belong to an explicit operation/Requirements section")
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
            else:
                f.error(rel, i, "scenario must belong to a requirement")
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
                    f.error(rel, r.id_line, f"duplicate ID '{r.req_id}' (also at {prev.file}:{prev.line})",
                            related_paths=(prev.file,))
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


def normalize_test_id(raw: str) -> str:
    return raw.upper().replace("_", "-").replace("REQ-", "REQ-", 1).lower().replace("req-", "REQ-", 1)


def check_requirement_names(reqs, f):
    seen = {}
    restated = set()
    id_counts = collections.Counter(r.req_id for r in reqs if r.req_id)
    canonical_counts = collections.Counter((r.spec_name, r.title) for r in reqs if r.op == "Requirements")
    active_counts = collections.Counter((r.spec_name, r.title) for r in reqs if r.op != "Requirements")
    for r in reqs:
        key = (r.spec_name, r.title)
        previous = seen.get(key)
        if previous is None:
            seen[key] = r
            continue
        same_id = (r.op in {"MODIFIED Requirements", "REMOVED Requirements"} and
                   previous.req_id and previous.req_id == r.req_id)
        # An ID-less canonical needs an unambiguous predecessor and sole writer.
        successor_id = ID_FORMAT_RE.match(r.req_id or "")
        legacy_backfill = (previous.req_id is None and r.op == "MODIFIED Requirements" and
                           successor_id and successor_id.group(1) == r.spec_name and
                           id_counts[r.req_id] == 1 and canonical_counts[key] == 1 and
                           active_counts[key] == 1)
        allowed = (previous.op == "Requirements" and key not in restated and
                   (same_id or legacy_backfill))
        if allowed:
            restated.add(key)
        else:
            f.error(r.file, r.line,
                    f"duplicate requirement title '{r.title}' in capability '{r.spec_name}' "
                    f"(also at {previous.file}:{previous.line})", related_paths=(previous.file,))


@dataclass
class SourceView:
    """Exact captured source bytes; parsing never rereads a source file."""
    specs: dict
    tests: dict
    test_roots: tuple
    spec_modes: dict
    test_modes: dict

    def identity(self):
        return {
            "spec_raw_sha256": raw_hashes(self.specs),
            "test_raw_sha256": raw_hashes(self.tests),
            "spec_modes": {str(p): m for p, m in sorted(self.spec_modes.items())},
            "test_modes": {str(p): m for p, m in sorted(self.test_modes.items())},
            "test_roots": list(map(str, self.test_roots)),
            "test_membership_by_root": {
                str(root): sorted(str(p) for p in self.tests if p.is_relative_to(root))
                for root in self.test_roots
            },
            "discovery_policy": "all test/tests directories at depth <=3, excluding .git/node_modules ancestors; recursively include named test/spec files with supported suffixes; canonical and non-archived active spec.md bodies",
            "identity_kind": "exact raw bytes and membership; no newline or replacement-decoding normalization",
        }


def raw_hashes(bodies):
    return {str(p): hashlib.sha256(raw).hexdigest() for p, raw in sorted(bodies.items())}


def decode_body(raw, path):
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"source is not strict UTF-8: {path} at byte {error.start}") from error


def safe_relative(name):
    path = Path(name)
    if path.is_absolute() or not path.parts or any(p in (".", "..") for p in path.parts):
        raise ValueError("unsupported source path: " + name)
    return path


def test_root(path):
    return (path.name in ("test", "tests") and len(path.parts) <= 3
            and not {".git", "node_modules"}.intersection(path.parts))


def test_member(path, roots):
    return (path.suffix in TEST_SUFFIXES and TEST_FILE_HINT.search(path.name)
            and any(path.is_relative_to(root) for root in roots))


def choose_roots(automatic, requested, complete=False):
    roots = tuple(sorted(set(map(safe_relative, requested)))) if requested else automatic
    if complete and roots != automatic:
        raise ValueError("affected mode requires the complete automatic test-root population; missing/subset/alternative overrides refuse")
    return roots


def current_test_roots(repo):
    roots = []
    def fail(error):
        raise error
    for parent, dirs, names in os.walk(repo, topdown=True, onerror=fail, followlinks=False):
        relative = Path(parent).relative_to(repo)
        # A root-policy name may be a dangling link/file, not an os.walk directory.
        # Inspect every entry form before choosing the supported directories.
        for name in sorted(set(dirs) | set(names)):
            path = relative / name
            if test_root(path):
                mode = os.stat(repo / path, follow_symlinks=False).st_mode
                if not stat.S_ISDIR(mode):
                    raise ValueError("unsupported current citation root: " + str(path))
        selected = []
        for name in sorted(dirs):
            path = relative / name
            if name in (".git", "node_modules") or len(path.parts) > 3:
                continue
            if (repo / path).is_symlink():
                if test_root(path):
                    raise ValueError("unsupported symlink test root: " + str(path))
                continue
            selected.append(name)
            if test_root(path):
                roots.append(path)
        dirs[:] = selected
    return tuple(sorted(set(roots)))


def current_test_paths(repo, roots):
    paths = set()
    def fail(error):
        raise error
    for root in roots:
        target = repo / root
        try:
            mode = os.stat(target, follow_symlinks=False).st_mode
        except FileNotFoundError:
            print(f"WARN   tests dir not found: {target}", file=sys.stderr)
            continue
        if not stat.S_ISDIR(mode):
            raise ValueError("unsupported configured citation root: " + str(root))
        for parent, dirs, names in os.walk(target, onerror=fail, followlinks=False):
            for name in set(dirs) | set(names):
                if (Path(parent) / name).is_symlink():
                    raise ValueError("unsupported symlink entry inside citation root: " + str(Path(parent) / name))
            for name in names:
                path = (Path(parent) / name).relative_to(repo)
                if test_member(path, roots):
                    paths.add(path)
    return sorted(paths)


def read_current_raw(repo, path):
    full = repo / path
    for ancestor in [full, *full.parents]:
        if ancestor == repo:
            break
        if ancestor.is_symlink():
            raise ValueError("unsupported symlink source: " + str(path))
    mode = os.stat(full, follow_symlinks=False).st_mode
    if not stat.S_ISREG(mode):
        raise ValueError("source is not a regular file: " + str(path))
    raw = full.read_bytes()
    decode_body(raw, path)  # strict validation of these exact bytes
    return raw, "100755" if mode & 0o111 else "100644"


def current_spec_paths(repo):
    """Explicit shallow enumeration: unreadable spec directories cannot vanish."""
    def directories(parent):
        try:
            mode = os.stat(parent, follow_symlinks=False).st_mode
        except FileNotFoundError:
            return []
        if stat.S_ISLNK(mode):
            raise ValueError("unsupported symlink spec directory: " + str(parent))
        if not stat.S_ISDIR(mode):
            raise ValueError("spec directory is not a directory: " + str(parent))
        selected = []
        for child in sorted(parent.iterdir()):
            if child.name == "archive":
                continue
            child_mode = os.stat(child, follow_symlinks=False).st_mode
            if stat.S_ISLNK(child_mode):
                raise ValueError("unsupported symlink spec entry: " + str(child))
            if stat.S_ISDIR(child_mode):
                selected.append(child)
        return selected
    openspec = repo / "openspec"
    capabilities = directories(openspec / "specs")
    for change in directories(openspec / "changes"):
        capabilities.extend(directories(change / "specs"))
    return sorted(path for directory in capabilities for path in directory.iterdir()
                  if path.name == "spec.md")


def capture_current(repo, tests_dirs=(), complete_roots=False):
    automatic = current_test_roots(repo)
    roots = choose_roots(automatic, tests_dirs, complete_roots)
    paths = current_spec_paths(repo)
    specs, spec_modes, tests, test_modes = {}, {}, {}, {}
    for full in paths:
        relative = full.relative_to(repo / "openspec")
        if "archive" not in relative.parts:
            specs[relative], spec_modes[relative] = read_current_raw(repo, Path("openspec") / relative)
    for path in current_test_paths(repo, roots):
        tests[path], test_modes[path] = read_current_raw(repo, path)
    return SourceView(specs, tests, roots, spec_modes, test_modes)


def find_test_files(repo: Path, tests_dirs):
    """Ordinary configurable discovery; affected scans use captured complete views."""
    roots = choose_roots(current_test_roots(repo), tests_dirs)
    return [repo / path for path in current_test_paths(repo, roots)]


def current_bodies(repo):
    return capture_current(repo).specs


def analyze(repo, view, authoring=False, strict=False):
    openspec = repo / "openspec"
    bodies = view.specs
    f = Findings()
    reqs: list[Requirement] = []
    main_paths = sorted(p for p in bodies if re.fullmatch(r"specs/[^/]+/spec.md", p.as_posix()))
    delta_paths = sorted(p for p in bodies if re.fullmatch(r"changes/[^/]+/specs/[^/]+/spec.md", p.as_posix())
                         and "archive" not in p.parts)
    for relative in main_paths:
        spec = openspec / relative
        parsed = parse_spec(spec, spec.parent.name, is_delta=False, f=f,
                            text=decode_body(bodies[relative], spec))
        if (authoring or strict) and not parsed:
            f.error(spec, 1, "spec file has no requirements")
        reqs += parsed
    for relative in delta_paths:
        spec = openspec / relative
        parsed = parse_spec(spec, spec.parent.name, is_delta=True, f=f,
                            baseline_exists=Path("specs") / spec.parent.name / "spec.md" in bodies,
                            text=decode_body(bodies[relative], spec))
        if (authoring or strict) and not parsed:
            f.error(spec, 1, "spec file has no requirements")
        reqs += parsed
    if not reqs and not f.errors and (authoring or strict):
        f.error(openspec, 1, "no requirements found under openspec/ — authoring/strict validation fails closed")
    check_requirement_names(reqs, f)
    seen_ids = check_requirements(reqs, f, strict_missing_fields=strict or authoring)
    cited: set[str] = set()
    citations: dict[str, tuple] = {}
    for path, raw in sorted(view.tests.items()):
        text = decode_body(raw, path)
        for m in TEST_ID_RE.finditer(text):
            rid = normalize_test_id(m.group(0))
            cited.add(rid)
            citations.setdefault(rid, (repo / path, text[:m.start()].count("\n") + 1))
    for rid, (tf, line) in sorted(citations.items()):
        if rid not in seen_ids:
            f.error(tf, line, f"stale test citation: '{rid}' not found in any spec")
    if view.tests:
        report = f.error if strict else f.warn
        for rid, r in sorted(seen_ids.items()):
            if r.scope == "v1-mandatory" and rid not in cited:
                report(r.file, r.line, f"v1-mandatory requirement '{rid}' has no test citation")
    else:
        mandatory = [r for r in reqs if r.scope == "v1-mandatory"]
        if strict and mandatory:
            for r in mandatory:
                f.error(r.file, r.line,
                        f"strict mode cannot verify test citation for '{r.req_id or r.title}' because no test files were found")
        else:
            print("note: no test tree found — skipping test-citation coverage", file=sys.stderr)
    return f, reqs, seen_ids, raw_hashes(view.tests)


def git_read(repo, *argv, input_bytes=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(["git", *argv], cwd=repo, env=env,
                            stdin=subprocess.DEVNULL if input_bytes is None else None,
                            input=input_bytes, capture_output=True, timeout=60, check=True)
    return result.stdout


def native_entries(repo, sha):
    entries = {}
    for row in git_read(repo, "ls-tree", "-r", "-t", "-z", sha).split(b"\0"):
        if not row:
            continue
        header, name = row.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split()
        path = safe_relative(name.decode("utf-8", errors="strict"))
        if path in entries:
            raise ValueError("duplicate native source path: " + str(path))
        entries[path] = (mode, kind, oid)
    return entries


def native_blobs(repo, entries, paths):
    """One bounded batch read; parse and hash precisely these returned blob bytes."""
    objects = sorted(set(entries[path][2] for path in paths))
    if not objects:
        return {}
    output = git_read(repo, "cat-file", "--batch",
                      input_bytes="".join(oid + "\n" for oid in objects).encode("ascii"))
    blobs, offset = {}, 0
    for expected in objects:
        end = output.index(b"\n", offset)
        oid, kind, size = output[offset:end].decode("ascii").split()
        if oid != expected or kind != "blob":
            raise ValueError("native batch returned an unsupported object")
        count = int(size)
        if count < 0:
            raise ValueError("invalid native blob size")
        start = end + 1
        raw = output[start:start + count]
        if len(raw) != count or output[start + count:start + count + 1] != b"\n":
            raise ValueError("incomplete native blob capture")
        blobs[expected] = raw
        offset = start + count + 1
    if offset != len(output):
        raise ValueError("unexpected native batch suffix")
    return {path: blobs[entries[path][2]] for path in paths}


def capture_baseline(repo, reference, tests_dirs=(), complete_roots=True):
    sha = git_read(repo, "rev-parse", "--verify", reference + "^{commit}").decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        raise ValueError("unsupported native baseline identity")
    entries = native_entries(repo, sha)
    # Validate all policy-matching entries before filtering by Git object kind.
    # A gitlink or symlink named tests must not vanish from the root population.
    automatic = tuple(sorted(path for path in entries if test_root(path)))
    for path in automatic:
        mode, kind, _ = entries[path]
        if mode != "040000" or kind != "tree":
            raise ValueError("unsupported native citation root: " + str(path) + " (" + mode + " " + kind + ")")
    roots = choose_roots(automatic, tests_dirs, complete_roots)
    spec_paths = sorted(path for path in entries if re.fullmatch(
        r"openspec/(?:specs/[^/]+|changes/[^/]+/specs/[^/]+)/spec.md", path.as_posix())
        and "archive" not in path.parts)
    if not spec_paths:
        raise ValueError("baseline has no canonical or active spec files")
    test_paths = sorted(path for path, (_, kind, _) in entries.items()
                        if kind != "tree" and test_member(path, roots))
    for path, (mode, kind, _) in entries.items():
        if (kind == "commit" or mode == "120000") and any(path.is_relative_to(root) for root in roots):
            raise ValueError("unsupported submodule/symlink inside native citation root: " + str(path))
    for path in set(spec_paths) | set(test_paths):
        mode, kind, _ = entries[path]
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError("unsupported native source mode: " + str(path))
    bodies = native_blobs(repo, entries, sorted(set(spec_paths) | set(test_paths)))
    for path, raw in bodies.items():
        decode_body(raw, path)
    return sha, SourceView(
        {p.relative_to("openspec"): bodies[p] for p in spec_paths},
        {p: bodies[p] for p in test_paths}, roots,
        {p.relative_to("openspec"): entries[p][0] for p in spec_paths},
        {p: entries[p][0] for p in test_paths},
    )


def diagnostic_key(row):
    return (row["level"], row["path"], row["line"], row["message"], tuple(row["related_paths"]))


def affected_admission(repo, change, reference, after_view, after_findings, tests_dirs=()):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", change):
        raise ValueError("affected change must be one canonical change name")
    base_sha, before_view = capture_baseline(repo, reference, tests_dirs)
    prefix = Path("changes") / change / "specs"
    affected = {p for p in after_view.specs if p.is_relative_to(prefix)}
    if not affected:
        raise ValueError("affected change contains no spec files")
    for path in set(before_view.specs) | set(after_view.specs):
        if path not in affected and (before_view.specs.get(path) != after_view.specs.get(path)
                                    or before_view.spec_modes.get(path) != after_view.spec_modes.get(path)):
            raise ValueError("baseline/current raw spec source differs outside the entire affected change: " + str(path))
    if (before_view.test_roots != after_view.test_roots or before_view.tests != after_view.tests
            or before_view.test_modes != after_view.test_modes):
        raise ValueError("native/current complete test-citation source differs in roots, membership, raw bytes or mode")
    before, _, _, _ = analyze(repo, before_view, authoring=True)
    grounding = Findings()
    for p in sorted(affected):
        check_grounding_sections(repo / "openspec" / p, decode_body(after_view.specs[p], p).splitlines(),
                                 True, Path("specs") / p.parent.name / "spec.md" in after_view.specs,
                                 grounding, require_source_references=True)
    owned = {str(repo / "openspec" / p) for p in affected}
    owned_errors = [row for row in after_findings.records if row["level"] == "ERROR"
                    and owned.intersection([row["path"], *row["related_paths"]])]
    old = collections.Counter(map(diagnostic_key, before.records))
    new = collections.Counter(map(diagnostic_key, after_findings.records))
    additions, removals = new - old, old - new
    coverage_warnings = {k: n for k, n in additions.items()
                         if k[0] == "WARN" and k[1] in owned
                         and k[3].startswith("v1-mandatory requirement '")
                         and k[3].endswith("' has no test citation")}
    regressions = additions - collections.Counter(coverage_warnings)
    receipt = {
        "name": "affected-authoring-and-full-diagnostic-nonregression",
        "baseline_sha": base_sha, "affected_paths": sorted(owned),
        "baseline_findings": before.records, "complete_union_findings": after_findings.records,
        "owned_errors": owned_errors, "grounding_errors": grounding.records,
        "all_additions": [{"identity": list(k), "multiplicity": n} for k, n in sorted(additions.items())],
        "all_removals": [{"identity": list(k), "multiplicity": n} for k, n in sorted(removals.items())],
        "owned_future_coverage_warnings": [{"identity": list(k), "multiplicity": n} for k, n in sorted(coverage_warnings.items())],
        "nonregression_faults": [{"identity": list(k), "multiplicity": n} for k, n in sorted(regressions.items())],
        "baseline_source_population": before_view.identity(),
        "union_source_population": after_view.identity(),
        "baseline_spec_hashes": raw_hashes(before_view.specs),
        "baseline_tests_hashes": raw_hashes(before_view.tests),
        "union_spec_hashes": raw_hashes(after_view.specs),
        "union_tests_hashes": raw_hashes(after_view.tests),
        "full_baseline_verdict": "PASS" if not before.errors else "NONPASS",
        "full_union_verdict": "PASS" if not after_findings.errors else "NONPASS",
        "verdict": "PASS" if not (owned_errors or grounding.errors or regressions) else "NONPASS",
        "scope": "All affected spec bodies plus full-tree ID/citation interactions; never a full-tree PASS.",
    }
    return receipt, before_view


def build_record(repo, view, tool_sha256, authoring=False, strict=False,
                 change=None, reference=None, tests_dirs=()):
    f, reqs, seen_ids, test_hashes = analyze(repo, view, authoring, strict)
    scoped, baseline = None, None
    if change:
        scoped, baseline = affected_admission(repo, change, reference, view, f, tests_dirs)
    record = {
        "format_version": 2, "tool_sha256": tool_sha256, "repo": str(repo),
        "full_verdict": "PASS" if not f.errors else "NONPASS",
        "requirements": len(reqs), "scenarios": sum(r.scenarios for r in reqs),
        "IDs": len(seen_ids), "complete_findings": f.records,
        "source_population": view.identity(), "spec_hashes": raw_hashes(view.specs),
        "test_hashes": test_hashes, "affected_receipt": scoped,
        "finalization": "native baseline identity and complete source population, then complete current source population and checker raw digest reverified immediately before receipt write; finite observation, no filesystem lock or continuous guarantee",
    }
    return record, baseline, f


def finalize_receipt(repo, view, record, baseline=None, reference=None, tests_dirs=(),
                     target=None, tool_path=None):
    """Serialize first; final native reads precede the final dirty-source capture."""
    payload = (json.dumps(record, indent=2) + "\n").encode("utf-8")
    if baseline is not None:
        sha, final_baseline = capture_baseline(repo, reference, tests_dirs)
        admitted_baseline = record["affected_receipt"]["baseline_source_population"]
        if (sha != record["affected_receipt"]["baseline_sha"]
                or baseline.identity() != admitted_baseline
                or final_baseline.identity() != admitted_baseline):
            raise ValueError("native baseline identity/source population changed before receipt emission")
    final_current = capture_current(repo, tests_dirs, complete_roots=baseline is not None)
    if view.identity() != record["source_population"] or final_current.identity() != record["source_population"]:
        raise ValueError("current spec/test roots, membership or raw bytes changed before receipt emission")
    checker = Path(__file__) if tool_path is None else tool_path
    if hashlib.sha256(checker.read_bytes()).hexdigest() != record["tool_sha256"]:
        raise ValueError("checker source changed before receipt emission")
    if target is not None:
        if target.resolve().is_relative_to(repo):
            raise ValueError("findings receipt must be outside repository")
        with target.open("xb") as receipt:
            receipt.write(payload)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo_root", type=Path)
    ap.add_argument("--tests-dir", action="append", default=[],
                    help="ordinary mode configurable; affected mode permits only the complete automatic root set")
    ap.add_argument("--strict", action="store_true",
                    help="treat missing ID/Source/Scope and uncovered v1-mandatory requirements as errors")
    ap.add_argument("--authoring", action="store_true",
                    help="treat missing ID/Source/Scope as errors without requiring implementation test coverage")
    ap.add_argument("--findings-json", type=Path, help="write complete findings/source receipt outside repository")
    ap.add_argument("--affected-change", help="admit the entire named active change against a full native baseline")
    ap.add_argument("--baseline-ref", help="immutable native Git baseline for affected admission")
    args = ap.parse_args()
    if args.affected_change and (not args.authoring or args.strict or not args.baseline_ref or not args.findings_json):
        ap.error("affected mode requires --authoring, --baseline-ref and external --findings-json; excludes --strict")
    tool_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    repo = args.repo_root.resolve()
    if not (repo / "openspec").is_dir():
        print(f"no openspec/ tree at {repo}", file=sys.stderr)
        return 2
    target = args.findings_json.resolve() if args.findings_json else None
    if target is not None and target.is_relative_to(repo):
        ap.error("findings receipt must be outside repository")
    # A failed new attempt cannot leave an older receipt available as its output.
    if target is not None and target.exists():
        ap.error("findings receipt must be a fresh absent path")
    try:
        view = capture_current(repo, args.tests_dir, complete_roots=bool(args.affected_change))
        record, baseline, f = build_record(repo, view, tool_sha256, args.authoring, args.strict,
                                           args.affected_change, args.baseline_ref, args.tests_dir)
        for msg in f.errors + f.warnings:
            print(msg)
        print(f"\nspec-trace-check: {record['requirements']} requirements, {record['IDs']} IDs, "
              f"{len(f.errors)} error(s), {len(f.warnings)} warning(s) — {'PASS' if not f.errors else 'FAIL'}")
        finalize_receipt(repo, view, record, baseline, args.baseline_ref, args.tests_dir, target)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print("source/admission refusal: " + str(error), file=sys.stderr)
        return 2
    scoped = record["affected_receipt"]
    if scoped:
        print("affected-authoring: " + scoped["verdict"] + "; full-tree remains " + scoped["full_union_verdict"])
        return 0 if scoped["verdict"] == "PASS" else 1
    return 0 if not f.errors else 1


if __name__ == "__main__":
    sys.exit(main())
