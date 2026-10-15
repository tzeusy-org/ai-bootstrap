#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Behavior regressions for contextual grounding and complete source admission."""
import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / 'scripts/spec-trace-check.py'
spec = importlib.util.spec_from_file_location('spec_trace_candidate', TOOL)
trace = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = trace
spec.loader.exec_module(trace)
PURPOSE = 'Defines complete bounded future behavior without claiming current capability or runtime delivery.'
REQ = '''### Requirement: Visible Evidence
In post-v1 scope, the system SHALL expose trustworthy evidence.

ID: REQ-core-demo-001
Source: RFC 0038 D19
Scope: post-v1

#### Scenario: Evidence is visible
- **WHEN** a reader inspects evidence
- **THEN** the actual evidence is visible
'''
FOOTER = '## Source References\n- RFC 0038 D19\n- Non-Negotiable Rule 1\n'
DELTA = '## Purpose\n' + PURPOSE + '\n\n## ADDED Requirements\n\n' + REQ + '\n' + FOOTER
MAIN = '# Core Demo\n\n## Purpose\nBaseline behavior.\n\n## Requirements\n\n' + REQ


def errors(body, exists=False):
    findings = trace.Findings()
    reqs = trace.parse_spec(Path('/fixture/changes/change/specs/core-demo/spec.md'),
                           'core-demo', True, findings, baseline_exists=exists,
                           text=body, require_source_references=True)
    trace.check_requirements(reqs, findings, strict_missing_fields=True)
    return findings.errors


class AdmissionTests(unittest.TestCase):
    def test_grounding_refusals_and_valid_contexts(self):
        self.assertEqual(errors(DELTA), [])
        self.assertEqual(errors('## ADDED Requirements\n\n' + REQ + '\n' + FOOTER, True), [])
        cases = [
            (DELTA, True, 'main-spec heading'),
            (DELTA.replace('## Purpose\n' + PURPOSE + '\n\n', ''), False, 'exactly one'),
            (DELTA.replace(PURPOSE, 'Short.'), False, '50 characters'),
            ('## ADDED Requirements\n\n' + REQ + '\n## Purpose\n' + PURPOSE + '\n' + FOOTER, False, 'precede all'),
            ('## Purpose\n' + PURPOSE + '\n' + DELTA, False, 'exactly one'),
            (DELTA.removesuffix(FOOTER), False, 'requires a final'),
            (DELTA.replace(FOOTER, '## Source References\n'), False, 'non-empty'),
            (DELTA + '\n' + FOOTER, False, 'exactly once'),
            (DELTA + '\n## REMOVED Requirements\n', False, 'final H2'),
            (DELTA + '\n' + REQ, False, 'cannot carry operative'),
            (DELTA.replace(PURPOSE, PURPOSE + '\n' + REQ), False, 'Purpose cannot carry'),
            (DELTA.replace('## ADDED Requirements', '## Arbitrary\n\n## ADDED Requirements'), False, 'unsupported delta-spec'),
        ]
        for body, exists, refusal in cases:
            with self.subTest(refusal=refusal):
                self.assertTrue(any(refusal in x for x in errors(body, exists)))

    def test_existing_metadata_identity_and_scenario_refusals(self):
        cases = [
            (DELTA.replace('Source: RFC 0038 D19\n', ''), 'missing Source'),
            (DELTA.replace('Scope: post-v1\n', ''), 'missing Scope'),
            (DELTA.replace('Source: RFC', '\nSource: RFC'), 'contiguous'),
            (DELTA.replace('REQ-core-demo-001', 'REQ-other-demo-001'), 'names spec'),
            (DELTA.replace('REQ-core-demo-001', 'REQ-core-demo-one'), 'malformed ID'),
            (DELTA.replace('- **THEN**', '- **WHEN** another condition holds\n- **THEN**'), '1 WHEN and 1 THEN'),
            (DELTA.replace('- **THEN** the actual evidence is visible\n', ''), '1 WHEN and 1 THEN'),
            (DELTA[:DELTA.index('#### Scenario:')] + FOOTER, 'has no scenarios'),
        ]
        cases.append((DELTA.replace('## ADDED Requirements\n\n', ''), 'delta-operation heading'))
        cases.append((DELTA.replace('### Requirement: Visible Evidence', '#### Scenario: Orphan\n- **WHEN** input exists\n- **THEN** evidence exists\n\n### Requirement: Visible Evidence'), 'scenario must belong'))
        for body, refusal in cases:
            with self.subTest(refusal=refusal):
                self.assertTrue(any(refusal in x for x in errors(body)))

    def git(self, root, *args):
        return subprocess.check_output(
            ['git', *args], cwd=root, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, timeout=60,
            env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')},
        ).decode().strip()

    def baseline(self, root, foreign=False, crlf=False, stale=False, extra_root=False):
        self.git(root, 'init', '-q')
        self.git(root, 'config', 'user.name', 'Fixture')
        self.git(root, 'config', 'user.email', 'fixture@example.invalid')
        self.git(root, 'config', 'core.hooksPath', '/dev/null')
        self.git(root, 'config', 'commit.gpgsign', 'false')
        self.git(root, 'config', 'core.autocrlf', 'false')
        p = root / 'openspec/specs/core-sentinel/spec.md'
        p.parent.mkdir(parents=True)
        body = MAIN.replace('core-demo', 'core-sentinel') + '\n## Notes\nKnown baseline fault.\n'
        p.write_bytes(body.replace('\n', '\r\n' if crlf else '\n').encode())
        if foreign:
            p = root / 'openspec/changes/z-foreign/specs/core-demo/spec.md'
            p.parent.mkdir(parents=True)
            p.write_bytes(DELTA.encode())
        p = root / 'tests/test_citations.py'
        p.parent.mkdir()
        body = '# Spec: REQ-ghost-spec-999\n' if stale else '# citation fixture\n'
        p.write_bytes(body.replace('\n', '\r\n' if crlf else '\n').encode())
        if extra_root:
            p = root / 'roster/alpha/tests/test_other.py'
            p.parent.mkdir(parents=True)
            p.write_bytes(b'# second complete citation root\n')
        self.git(root, 'add', '.')
        self.git(root, 'commit', '-qm', 'fixture baseline')
        return self.git(root, 'rev-parse', 'HEAD')

    def add_delta(self, root):
        p = root / 'openspec/changes/dashboard-one-mind-goal-surface/specs/core-demo/spec.md'
        p.parent.mkdir(parents=True)
        p.write_bytes(DELTA.encode())
        return p

    def prepare(self, root, sha, tests_dirs=(), change='dashboard-one-mind-goal-surface', tool_path=TOOL):
        view = trace.capture_current(root, tests_dirs, complete_roots=True)
        digest = hashlib.sha256(tool_path.read_bytes()).hexdigest()
        record, baseline, _ = trace.build_record(root, view, digest, authoring=True,
                                                change=change, reference=sha, tests_dirs=tests_dirs)
        return view, record, baseline

    def admit(self, root, sha, tests_dirs=(), target=None):
        view, record, baseline = self.prepare(root, sha, tests_dirs)
        trace.finalize_receipt(root, view, record, baseline, sha, tests_dirs, target=target)
        return record['affected_receipt']

    def test_full_nonpass_is_preserved_and_outside_changes_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sha = self.baseline(root)
            p = self.add_delta(root)
            receipt = self.admit(root, sha)
            self.assertEqual(receipt['verdict'], 'PASS')
            self.assertEqual(receipt['full_union_verdict'], 'NONPASS')
            self.assertEqual(receipt['full_baseline_verdict'], 'NONPASS')
            self.assertEqual(receipt['baseline_findings'], receipt['complete_union_findings'])
            self.assertEqual(receipt['all_additions'], [])
            self.assertEqual(receipt['all_removals'], [])
            self.assertEqual(receipt['baseline_source_population']['test_roots'], ['tests'])
            self.assertEqual(receipt['baseline_source_population']['test_membership_by_root'],
                             {'tests': ['tests/test_citations.py']})
            p.write_bytes(DELTA.replace('Scope: post-v1', 'Scope: v1-mandatory').encode())
            future = self.admit(root, sha)
            self.assertEqual(future['verdict'], 'PASS')
            self.assertEqual(len(future['owned_future_coverage_warnings']), 1)
            self.assertTrue(future['all_additions'])
            self.assertEqual(future['nonregression_faults'], [])
            p.write_bytes(DELTA.removesuffix(FOOTER).encode())
            self.assertEqual(self.admit(root, sha)['verdict'], 'NONPASS')
            p.write_bytes(DELTA.encode())
            outside = root / 'openspec/specs/core-sentinel/spec.md'
            old = outside.read_bytes()
            outside.write_bytes(old + b'\nOutside mutation.\n')
            with self.assertRaisesRegex(ValueError, 'outside the entire affected'):
                self.admit(root, sha)
            outside.write_bytes(old)
            t = root / 'tests/test_citations.py'
            t.write_bytes(b'# altered citation source\n')
            with self.assertRaisesRegex(ValueError, 'test-citation source differs'):
                self.admit(root, sha)
            t.write_bytes(b'# citation fixture\n')
            untracked = root / 'openspec/specs/core-untracked/spec.md'
            untracked.parent.mkdir(parents=True)
            untracked.write_bytes(MAIN.replace('core-demo', 'core-untracked').encode())
            with self.assertRaisesRegex(ValueError, 'outside the entire affected'):
                self.admit(root, sha)
            untracked.unlink()
            p.unlink()
            with self.assertRaisesRegex(ValueError, 'no spec files'):
                self.admit(root, sha)

        # The real CLI requires an explicit, fresh external admission request.
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output_tmp:
            root = Path(tmp)
            sha = self.baseline(root)
            self.add_delta(root)
            output = Path(output_tmp)
            common = [sys.executable, str(TOOL), str(root)]
            request = ['--authoring', '--affected-change',
                       'dashboard-one-mind-goal-surface', '--baseline-ref', sha]
            environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            target = output / 'valid.json'
            result = subprocess.run(common + request + ['--findings-json', str(target)],
                                    stdin=subprocess.DEVNULL, capture_output=True,
                                    timeout=60, env=environment)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(target.read_bytes())['affected_receipt']['verdict'], 'PASS')
            self.assertIn(b'full-tree remains NONPASS', result.stdout)
            original_receipt = target.read_bytes()
            invalid = [
                (request + ['--strict'], output / 'strict.json', b'excludes --strict'),
                (request[1:], output / 'no-authoring.json', b'requires --authoring'),
                (request[:-2], output / 'no-baseline.json', b'--baseline-ref'),
                (request, target, b'fresh absent path'),
                (request, root / 'inside.json', b'outside repository'),
            ]
            for options, refused, message in invalid:
                with self.subTest(invalid_admission=refused.name):
                    result = subprocess.run(common + options + ['--findings-json', str(refused)],
                                            stdin=subprocess.DEVNULL, capture_output=True,
                                            timeout=60, env=environment)
                    self.assertEqual(result.returncode, 2, result.stderr.decode())
                    self.assertIn(message, result.stderr)
                    if refused == target:
                        self.assertEqual(target.read_bytes(), original_receipt)
                    else:
                        self.assertFalse(refused.exists())
            result = subprocess.run(common + request, stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=60, env=environment)
            self.assertEqual(result.returncode, 2, result.stderr.decode())
            self.assertIn(b'--findings-json', result.stderr)

        # M1: the actual native stale citation must remain in complete diagnostics.
        for action in ('delete', 'rename-outside-discovery', 'untracked-addition', 'delete-root'):
            with self.subTest(citation_population=action), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sha = self.baseline(root, stale=True)
                self.add_delta(root)
                receipt = self.admit(root, sha)
                self.assertEqual(receipt['verdict'], 'PASS')
                self.assertTrue(any('stale test citation' in row['message'] for row in receipt['baseline_findings']))
                self.assertEqual(receipt['baseline_findings'], receipt['complete_union_findings'])
                t = root / 'tests/test_citations.py'
                if action in ('delete', 'delete-root'):
                    t.unlink()
                    if action == 'delete-root':
                        t.parent.rmdir()
                elif action == 'rename-outside-discovery':
                    dest = root / 'docs/citation_body.py'
                    dest.parent.mkdir()
                    t.rename(dest)
                else:
                    (t.parent / 'test_untracked.py').write_bytes(b'# Spec: REQ-ghost-spec-999\n')
                with self.assertRaisesRegex(ValueError, 'test-citation source differs'):
                    self.admit(root, sha)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sha = self.baseline(root, extra_root=True)
            self.add_delta(root)
            for roots in (['missing'], ['tests'], ['roster/alpha/tests']):
                with self.subTest(incomplete_roots=roots), self.assertRaisesRegex(ValueError, 'complete automatic test-root'):
                    self.admit(root, sha, roots)
            complete = ['roster/alpha/tests', 'tests']
            receipt = self.admit(root, sha, complete)
            self.assertEqual(receipt['verdict'], 'PASS')
            self.assertEqual(receipt['baseline_source_population']['test_roots'], complete)
            self.assertEqual(len(receipt['baseline_tests_hashes']), 2)
            # Ordinary explicitly selected discovery remains configurable.
            ordinary = trace.capture_current(root, ['tests'])
            self.assertEqual(set(ordinary.tests), {Path('tests/test_citations.py')})
            (root / 'new/tests').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'test-citation source differs'):
                self.admit(root, sha)

        # M4: reject unsupported root entries before tree/dir filtering.
        forms = ('gitlink-absent', 'gitlink-working-directory',
                 'dangling-native-current', 'dangling-native-current-absent',
                 'dangling-current-only', 'native-current-regular-file')
        for form in forms:
            with self.subTest(unsupported_root=form), tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output_tmp:
                root = Path(tmp)
                child_oid = self.baseline(root)
                t = root / 'tests/test_citations.py'
                t.unlink()
                t.parent.rmdir()
                if form != 'dangling-current-only':
                    self.git(root, 'rm', '--cached', 'tests/test_citations.py')
                    if form.startswith('gitlink'):
                        self.git(root, 'update-index', '--add', '--cacheinfo', '160000', child_oid, 'tests')
                    elif form == 'native-current-regular-file':
                        (root / 'tests').write_bytes(b'unsupported non-directory citation root\n')
                        self.git(root, 'add', 'tests')
                    else:
                        (root / 'tests').symlink_to('missing-target', target_is_directory=True)
                        self.git(root, 'add', 'tests')
                    self.git(root, 'commit', '-qm', 'unsupported native citation root fixture')
                    sha = self.git(root, 'rev-parse', 'HEAD')
                    if form == 'dangling-native-current-absent':
                        (root / 'tests').unlink()
                    if form == 'gitlink-working-directory':
                        (root / 'tests').mkdir()
                    # Independently exercise native admission, even when current capture also refuses.
                    with self.assertRaisesRegex(ValueError, 'unsupported native citation root'):
                        trace.capture_baseline(root, sha)
                else:
                    sha = child_oid
                    (root / 'tests').symlink_to('missing-target', target_is_directory=True)
                    native_sha, native_view = trace.capture_baseline(root, sha)
                    self.assertEqual(native_sha, sha)
                    self.assertEqual(native_view.test_roots, (Path('tests'),))
                self.add_delta(root)
                target = Path(output_tmp) / 'refused-root.json'
                with self.assertRaisesRegex(ValueError, 'unsupported.*citation root'):
                    self.admit(root, sha, target=target)
                self.assertFalse(target.exists())

        # Keep unsupported descendants protected even when they have no test suffix.
        for form in ('gitlink', 'dangling-symlink-current', 'dangling-symlink-current-absent'):
            with self.subTest(unsupported_descendant=form), tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output_tmp:
                root = Path(tmp)
                child_oid = self.baseline(root)
                if form == 'gitlink':
                    self.git(root, 'update-index', '--add', '--cacheinfo', '160000', child_oid, 'tests/opaque')
                else:
                    (root / 'tests/opaque').symlink_to('missing-target', target_is_directory=True)
                    self.git(root, 'add', 'tests/opaque')
                self.git(root, 'commit', '-qm', 'unsupported descendant citation source fixture')
                sha = self.git(root, 'rev-parse', 'HEAD')
                if form == 'dangling-symlink-current-absent':
                    (root / 'tests/opaque').unlink()
                with self.assertRaisesRegex(ValueError, 'unsupported.*citation root'):
                    trace.capture_baseline(root, sha)
                self.add_delta(root)
                target = Path(output_tmp) / 'refused-descendant.json'
                with self.assertRaisesRegex(ValueError, 'unsupported.*citation root'):
                    self.admit(root, sha, target=target)
                self.assertFalse(target.exists())

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output_tmp:
            root = Path(tmp)
            sha = self.baseline(root)
            self.add_delta(root)
            target = Path(output_tmp) / 'regular-root.json'
            receipt = self.admit(root, sha, target=target)
            self.assertTrue(target.exists())
            emitted = json.loads(target.read_bytes())
            self.assertEqual(receipt['verdict'], 'PASS')
            self.assertEqual(emitted['source_population']['test_roots'], ['tests'])
            self.assertEqual(emitted['test_hashes']['tests/test_citations.py'],
                             hashlib.sha256((root / 'tests/test_citations.py').read_bytes()).hexdigest())
            custom = root / 'docs/checks/test_custom.py'
            custom.parent.mkdir(parents=True)
            custom.write_bytes(b'# ordinary configurable citation fixture\n')
            ordinary = trace.capture_current(root, ['docs/checks'])
            self.assertEqual(ordinary.tests, {Path('docs/checks/test_custom.py'): custom.read_bytes()})

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output_tmp:
            root = Path(tmp)
            self.baseline(root)
            t = root / 'tests/test_citations.py'
            t.unlink()
            t.parent.rmdir()
            self.git(root, 'rm', '--cached', 'tests/test_citations.py')
            self.git(root, 'commit', '-qm', 'normal no-test native authoring fixture')
            sha = self.git(root, 'rev-parse', 'HEAD')
            self.add_delta(root)
            target = Path(output_tmp) / 'normal-no-tests.json'
            receipt = self.admit(root, sha, target=target)
            self.assertEqual(receipt['verdict'], 'PASS')
            self.assertEqual(receipt['baseline_source_population']['test_roots'], [])
            self.assertEqual(receipt['baseline_tests_hashes'], {})
            self.assertTrue(target.exists())

        # Ordinary arbitrary configured roots use a different stat guard from
        # automatic test/tests discovery; neither a file nor a dangling link is absence.
        for form in ('regular-file', 'dangling-link'):
            with self.subTest(ordinary_configured_root=form), tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output_tmp:
                root = Path(tmp)
                self.baseline(root)
                self.add_delta(root)
                configured = root / 'docs/checks'
                configured.parent.mkdir()
                if form == 'regular-file':
                    configured.write_bytes(b'not a citation directory\n')
                else:
                    configured.symlink_to('missing-target', target_is_directory=True)
                with self.assertRaisesRegex(ValueError, 'unsupported configured citation root'):
                    trace.capture_current(root, ['docs/checks'])
                target = Path(output_tmp) / 'refused-ordinary.json'
                result = subprocess.run(
                    [sys.executable, str(TOOL), str(root), '--authoring',
                     '--tests-dir', 'docs/checks', '--findings-json', str(target)],
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                    env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')},
                )
                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertIn(b'unsupported configured citation root', result.stderr)
                self.assertFalse(target.exists())

        # A genuinely missing ordinary override remains a warning, not an opaque root.
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output_tmp:
            root = Path(tmp)
            self.baseline(root)
            self.add_delta(root)
            sentinel = root / 'openspec/specs/core-sentinel/spec.md'
            sentinel.write_bytes(MAIN.replace('core-demo', 'core-sentinel').encode())
            target = Path(output_tmp) / 'ordinary-missing.json'
            result = subprocess.run(
                [sys.executable, str(TOOL), str(root), '--authoring',
                 '--tests-dir', 'docs/missing', '--findings-json', str(target)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')},
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertIn(b'WARN   tests dir not found:', result.stderr)
            self.assertTrue(target.exists())
            self.assertEqual(json.loads(target.read_bytes())['test_hashes'], {})

        # Excluded root-policy ancestors stay excluded, rather than gaining implicit custody.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_oid = self.baseline(root)
            self.git(root, 'update-index', '--add', '--cacheinfo', '160000', child_oid, 'node_modules/tests')
            self.git(root, 'commit', '-qm', 'explicitly excluded root-policy fixture')
            sha = self.git(root, 'rev-parse', 'HEAD')
            self.add_delta(root)
            self.assertEqual(self.admit(root, sha)['verdict'], 'PASS')

        # M2: equal visible text never replaces raw source custody.
        for source in ('foreign-spec', 'citation-test'):
            with self.subTest(newline_mutation=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sha = self.baseline(root)
                self.add_delta(root)
                target = root / ('openspec/specs/core-sentinel/spec.md' if source == 'foreign-spec' else 'tests/test_citations.py')
                target.write_bytes(target.read_bytes().replace(b'\n', b'\r\n'))
                expected = 'outside the entire affected' if source == 'foreign-spec' else 'test-citation source differs'
                with self.assertRaisesRegex(ValueError, expected):
                    self.admit(root, sha)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sha = self.baseline(root, crlf=True)
            self.add_delta(root)
            receipt = self.admit(root, sha)
            self.assertEqual(receipt['verdict'], 'PASS')
            spec_bytes = (root / 'openspec/specs/core-sentinel/spec.md').read_bytes()
            test_bytes = (root / 'tests/test_citations.py').read_bytes()
            self.assertIn(b'\r\n', spec_bytes)
            self.assertEqual(receipt['baseline_spec_hashes']['specs/core-sentinel/spec.md'], hashlib.sha256(spec_bytes).hexdigest())
            self.assertEqual(receipt['baseline_tests_hashes']['tests/test_citations.py'], hashlib.sha256(test_bytes).hexdigest())
            self.assertEqual(receipt['baseline_spec_hashes']['specs/core-sentinel/spec.md'], receipt['union_spec_hashes']['specs/core-sentinel/spec.md'])
            self.assertEqual(receipt['all_additions'], [])
            self.assertEqual(receipt['all_removals'], [])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.baseline(root)
            self.git(root, 'rm', 'openspec/specs/core-sentinel/spec.md')
            self.git(root, 'commit', '-qm', 'empty native spec corpus')
            sha = self.git(root, 'rev-parse', 'HEAD')
            self.add_delta(root)
            with self.assertRaisesRegex(ValueError, 'baseline has no canonical or active spec'):
                self.admit(root, sha)
        for source in ('foreign-spec', 'owned-spec', 'citation-test'):
            with self.subTest(malformed_current=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sha = self.baseline(root)
                p = self.add_delta(root)
                target = p if source == 'owned-spec' else root / ('openspec/specs/core-sentinel/spec.md' if source == 'foreign-spec' else 'tests/test_citations.py')
                target.write_bytes(target.read_bytes() + b'\xff')
                with self.assertRaisesRegex(ValueError, 'strict UTF-8'):
                    self.admit(root, sha)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.baseline(root)
            t = root / 'tests/test_citations.py'
            original = t.read_bytes()
            t.write_bytes(original + b'\xff')
            self.git(root, 'add', 'tests/test_citations.py')
            self.git(root, 'commit', '-qm', 'malformed native citation fixture')
            sha = self.git(root, 'rev-parse', 'HEAD')
            t.write_bytes(original)
            self.add_delta(root)
            with self.assertRaisesRegex(ValueError, 'strict UTF-8'):
                self.admit(root, sha)

        # M3: mutate real fixture sources during the second native batch read.
        for source in ('foreign-spec', 'owned-spec', 'citation-test', 'citation-membership'):
            with self.subTest(late_native_read=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sha = self.baseline(root)
                p = self.add_delta(root)
                original_read = trace.git_read
                native_batches = 0
                def mutate_at_final_native_read(repo, *args, **kwargs):
                    nonlocal native_batches
                    raw = original_read(repo, *args, **kwargs)
                    if args[:2] == ('cat-file', '--batch'):
                        native_batches += 1
                        if native_batches == 2:
                            if source == 'citation-membership':
                                (root / 'tests/test_late.py').write_bytes(b'# Spec: REQ-ghost-spec-999\n')
                            else:
                                target = p if source == 'owned-spec' else root / ('openspec/specs/core-sentinel/spec.md' if source == 'foreign-spec' else 'tests/test_citations.py')
                                target.write_bytes(target.read_bytes() + b'\n# Spec: REQ-ghost-spec-999\n')
                    return raw
                with mock.patch.object(trace, 'git_read', side_effect=mutate_at_final_native_read):
                    with self.assertRaisesRegex(ValueError, 'changed before receipt emission'):
                        self.admit(root, sha)
                self.assertEqual(native_batches, 2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sha = self.baseline(root)
            p = self.add_delta(root)
            view, record, baseline = self.prepare(root, sha)
            t = root / 'tests/test_citations.py'
            captured = t.read_bytes()
            # The parsed findings and digest both describe the same initial bytes.
            t.write_bytes(b'# Spec: REQ-ghost-spec-999\n')
            findings, _, _, hashes = trace.analyze(root, view, authoring=True)
            self.assertFalse(any('stale test citation' in row['message'] for row in findings.records))
            self.assertEqual(hashes['tests/test_citations.py'], hashlib.sha256(captured).hexdigest())
            with self.assertRaisesRegex(ValueError, 'changed before receipt emission'):
                trace.finalize_receipt(root, view, record, baseline, sha)
            t.write_bytes(captured)
            original_dumps = trace.json.dumps
            def mutate_during_serialization(*args, **kwargs):
                payload = original_dumps(*args, **kwargs)
                t.write_bytes(b'# Spec: REQ-ghost-spec-999\n')
                return payload
            with mock.patch.object(trace.json, 'dumps', side_effect=mutate_during_serialization):
                with self.assertRaisesRegex(ValueError, 'changed before receipt emission'):
                    trace.finalize_receipt(root, view, record, baseline, sha)
            t.write_bytes(captured)
            with tempfile.TemporaryDirectory() as output_tmp:
                target = Path(output_tmp) / 'receipt.json'
                trace.finalize_receipt(root, view, record, baseline, sha, target=target)
                emitted = json.loads(target.read_bytes())
                self.assertEqual(emitted['test_hashes']['tests/test_citations.py'], hashlib.sha256(captured).hexdigest())
                self.assertEqual(emitted['affected_receipt']['verdict'], 'PASS')
                # A refused attempt emits no new receipt.
                refused = Path(output_tmp) / 'refused.json'
                t.write_bytes(b'# changed after analysis\n')
                with self.assertRaises(ValueError):
                    trace.finalize_receipt(root, view, record, baseline, sha, target=refused)
                self.assertFalse(refused.exists())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.baseline(root)
            self.add_delta(root)
            original_read = trace.git_read
            resolutions = 0
            def move_native_reference(repo, *args, **kwargs):
                nonlocal resolutions
                if args[:3] == ('rev-parse', '--verify', 'HEAD^{commit}'):
                    resolutions += 1
                    if resolutions == 2:
                        (root / 'README.md').write_bytes(b'new native fixture identity\n')
                        self.git(root, 'add', 'README.md')
                        self.git(root, 'commit', '-qm', 'move only disposable native reference')
                return original_read(repo, *args, **kwargs)
            with mock.patch.object(trace, 'git_read', side_effect=move_native_reference):
                with self.assertRaisesRegex(ValueError, 'native baseline identity/source population changed'):
                    self.admit(root, 'HEAD')
            self.assertEqual(resolutions, 2)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as tool_tmp:
            root = Path(tmp)
            sha = self.baseline(root)
            self.add_delta(root)
            tool = Path(tool_tmp) / 'checker.py'
            tool.write_bytes(TOOL.read_bytes())
            view, record, baseline = self.prepare(root, sha, tool_path=tool)
            tool.write_bytes(tool.read_bytes() + b'\n# deterministic disposable tool drift\n')
            with self.assertRaisesRegex(ValueError, 'checker source changed'):
                trace.finalize_receipt(root, view, record, baseline, sha, tool_path=tool)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sha = self.baseline(root)
            self.add_delta(root)
            with self.assertRaises(subprocess.SubprocessError):
                self.admit(root, 'missing-native-ref')
            with self.assertRaisesRegex(ValueError, 'canonical change name'):
                self.prepare(root, sha, change='../not-a-change')
            original_iterdir = Path.iterdir
            def fail_source_directory(directory):
                if directory == root / 'openspec/changes/dashboard-one-mind-goal-surface/specs':
                    raise PermissionError('deterministic unreadable spec-directory fixture')
                return original_iterdir(directory)
            with mock.patch.object(Path, 'iterdir', autospec=True, side_effect=fail_source_directory):
                with self.assertRaisesRegex(PermissionError, 'unreadable spec-directory'):
                    self.admit(root, sha)
            # Full diagnostics still retain every occurrence, including repeated old syntax faults.
            p = root / 'openspec/specs/core-sentinel/spec.md'
            p.write_bytes(p.read_bytes() + b'\n## Notes\nAnother baseline fault.\n')
            self.git(root, 'add', 'openspec/specs/core-sentinel/spec.md')
            self.git(root, 'commit', '-qm', 'two complete baseline occurrences')
            sha = self.git(root, 'rev-parse', 'HEAD')
            receipt = self.admit(root, sha)
            self.assertEqual(len(receipt['baseline_findings']), 2)
            self.assertEqual(receipt['baseline_findings'], receipt['complete_union_findings'])
            self.assertEqual(receipt['all_additions'], [])
            self.assertEqual(receipt['all_removals'], [])

    def test_id_and_name_collisions_are_owned_even_when_reported_on_foreign_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sha = self.baseline(root, foreign=True)
            p = self.add_delta(root)
            receipt = self.admit(root, sha)
            self.assertEqual(receipt['verdict'], 'NONPASS')
            self.assertTrue(any('duplicate ID' in x['message'] and str(p) in x['related_paths']
                                for x in receipt['owned_errors']))
            self.assertTrue(any('z-foreign' in x['path'] for x in receipt['owned_errors']))
            self.assertTrue(receipt['nonregression_faults'])
            p.write_bytes(DELTA.replace('REQ-core-demo-001', 'REQ-core-demo-002').encode())
            receipt = self.admit(root, sha)
            self.assertTrue(any('duplicate requirement title' in x['message'] for x in receipt['owned_errors']))
            self.assertEqual(receipt['verdict'], 'NONPASS')
            p.write_bytes(DELTA.replace('REQ-core-demo-001', 'REQ-core-demo-002').replace('Visible Evidence', 'Different Evidence').encode())
            self.assertEqual(self.admit(root, sha)['verdict'], 'PASS')

        # Backfill uses the same real CLI and native admission as established IDs.
        cases = (
            ('legacy-fresh', None),
            ('established-same', None),
            ('duplicate-canonical-current', 'duplicate requirement title'),
            ('duplicate-canonical-baseline', 'duplicate requirement title'),
            ('second-writer-fresh', 'duplicate requirement title'),
            ('second-writer-same', 'duplicate ID'),
            ('foreign-modified-missing-id', 'duplicate requirement title'),
            ('foreign-modified-malformed-id', 'duplicate requirement title'),
            ('foreign-added-missing-id', 'duplicate requirement title'),
            ('foreign-added-malformed-id', 'duplicate requirement title'),
            ('established-changed', 'duplicate requirement title'),
            ('missing-id', 'missing ID'),
            ('malformed-id', 'malformed ID'),
            ('wrong-capability-id', 'names spec'),
            ('reused-canonical-id', 'duplicate ID'),
            ('reused-active-id', 'duplicate ID'),
            ('added-same-title', 'duplicate requirement title'),
            ('legacy-removed', 'duplicate requirement title'),
            ('legacy-renamed', 'duplicate requirement title'),
            ('missing-scenario', 'has no scenarios'),
            ('missing-source', 'missing Source'),
        )
        environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        for case, refusal in cases:
            with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output_tmp:
                root, output = Path(tmp), Path(output_tmp)
                self.baseline(root)
                # Complete this fixture's source before freezing its native baseline.
                sentinel = root / 'openspec/specs/core-sentinel/spec.md'
                sentinel.write_bytes(MAIN.replace('core-demo', 'core-sentinel').encode())
                requirement = REQ.replace('In post-v1 scope, the system SHALL', 'The system SHALL')
                requirement = requirement.replace('Scope: post-v1', 'Scope: v1-mandatory')
                legacy = requirement.replace('ID: REQ-core-demo-001\n', '')
                canonical = root / 'openspec/specs/core-demo/spec.md'
                canonical.parent.mkdir(parents=True)
                prefix = '# Core Demo\n\n## Purpose\nBaseline behavior.\n\n## Requirements\n\n'
                canonical.write_bytes((prefix + (requirement if case.startswith('established-') else legacy)).encode())
                if case == 'duplicate-canonical-baseline':
                    canonical.write_bytes(canonical.read_bytes() + b'\n' + legacy.encode())
                if case == 'reused-canonical-id':
                    canonical.write_bytes(canonical.read_bytes() + b'\n' + requirement.replace('Visible Evidence', 'Different Evidence').encode())
                if case in ('second-writer-fresh', 'second-writer-same', 'reused-active-id') or case.startswith('foreign-'):
                    foreign = root / 'openspec/changes/z-foreign/specs/core-demo/spec.md'
                    foreign.parent.mkdir(parents=True)
                    body = requirement
                    operation = 'MODIFIED'
                    if case == 'second-writer-fresh':
                        body = body.replace('REQ-core-demo-001', 'REQ-core-demo-002')
                    if case == 'reused-active-id':
                        body = body.replace('Visible Evidence', 'Different Evidence')
                        operation = 'ADDED'
                    if case.startswith('foreign-'):
                        if '-added-' in case:
                            operation = 'ADDED'
                        if case.endswith('-missing-id'):
                            body = legacy
                        else:
                            body = body.replace('REQ-core-demo-001', 'REQ-core-demo-invalid')
                    foreign.write_bytes((f'## {operation} Requirements\n\n' + body + '\n' + FOOTER).encode())
                self.git(root, 'add', '.')
                self.git(root, 'commit', '-qm', 'complete identity fixture baseline')
                sha = self.git(root, 'rev-parse', 'HEAD')
                body, operation = requirement, 'MODIFIED'
                if case == 'established-changed':
                    body = body.replace('REQ-core-demo-001', 'REQ-core-demo-002')
                elif case == 'missing-id':
                    body = legacy
                elif case == 'malformed-id':
                    body = body.replace('REQ-core-demo-001', 'REQ-core-demo-one')
                elif case == 'wrong-capability-id':
                    body = body.replace('REQ-core-demo-001', 'REQ-other-demo-001')
                elif case == 'added-same-title':
                    operation = 'ADDED'
                elif case == 'legacy-removed':
                    operation = 'REMOVED'
                elif case == 'legacy-renamed':
                    operation = 'RENAMED'
                elif case == 'missing-scenario':
                    body = body[:body.index('#### Scenario:')]
                elif case == 'missing-source':
                    body = body.replace('Source: RFC 0038 D19\n', '')
                owned = self.add_delta(root)
                owned.write_bytes((f'## {operation} Requirements\n\n' + body + '\n' + FOOTER).encode())
                if case == 'duplicate-canonical-current':
                    canonical.write_bytes(canonical.read_bytes() + b'\n' + legacy.encode())
                for mode in ('default', 'authoring', 'affected'):
                    with self.subTest(identity_case=case, interface=mode):
                        target = output / f'{mode}.json'
                        args = [sys.executable, str(TOOL), str(root)]
                        if mode != 'default':
                            args.append('--authoring')
                        if mode == 'affected':
                            args.extend(['--affected-change', 'dashboard-one-mind-goal-surface', '--baseline-ref', sha])
                        result = subprocess.run(args + ['--findings-json', str(target)],
                                                stdin=subprocess.DEVNULL, capture_output=True,
                                                timeout=60, env=environment)
                        if case == 'duplicate-canonical-current' and mode == 'affected':
                            self.assertEqual(result.returncode, 2, result.stderr.decode())
                            self.assertIn(b'outside the entire affected', result.stderr)
                            self.assertFalse(target.exists())
                            continue
                        expected = 1 if refusal or (case == 'legacy-fresh' and mode == 'authoring') else 0
                        if case == 'missing-source' and mode == 'default':
                            expected = 0  # Default mode retains missing metadata as warnings.
                        self.assertEqual(result.returncode, expected, result.stdout.decode() + result.stderr.decode())
                        record = json.loads(target.read_bytes())
                        findings = record['complete_findings']
                        if refusal:
                            self.assertTrue(any(refusal in row['message'] for row in findings), findings)
                        else:
                            self.assertFalse(any('duplicate requirement title' in row['message'] for row in findings), findings)
                        if case == 'legacy-fresh':
                            self.assertTrue(any("'Visible Evidence' missing ID" in row['message'] and row['path'] == str(canonical) for row in findings))
                            self.assertTrue(any('has no test citation' in row['message'] and row['path'] == str(owned) for row in findings))
                        if mode == 'affected':
                            receipt = record['affected_receipt']
                            self.assertEqual(receipt['verdict'], 'NONPASS' if refusal else 'PASS')
                            if refusal:
                                self.assertTrue(receipt['owned_errors'] or receipt['nonregression_faults'])
                            if case == 'legacy-fresh':
                                self.assertEqual(receipt['full_baseline_verdict'], 'NONPASS')
                                self.assertEqual(receipt['full_union_verdict'], 'NONPASS')
                                self.assertEqual(len(receipt['owned_future_coverage_warnings']), 1)
                                self.assertEqual(receipt['nonregression_faults'], [])
                            if case == 'duplicate-canonical-baseline':
                                self.assertTrue(any('duplicate requirement title' in row['message'] and row['path'] == str(canonical) for row in receipt['baseline_findings']))
                                self.assertTrue(any('duplicate requirement title' in row['message'] and row['path'] == str(owned) for row in receipt['owned_errors']))
                            if case.startswith('foreign-'):
                                self.assertTrue(any('duplicate requirement title' in row['message'] and row['path'] == str(foreign) for row in receipt['baseline_findings']))
                                self.assertTrue(any('duplicate requirement title' in row['message'] and row['path'] == str(owned) for row in receipt['owned_errors']))


if __name__ == '__main__':
    unittest.main()
