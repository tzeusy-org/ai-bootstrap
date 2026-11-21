"""Offline tests for scripts/lgtm.py: no network, no credentials.

Run: python3 -m pytest skills/personal/lgtm-access/tests/ --import-mode=importlib -q
"""

import base64
import contextlib
import importlib.util
import io
import os
import pathlib
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lgtm.py"
spec = importlib.util.spec_from_file_location("lgtm", SCRIPT)
lgtm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lgtm)

CRED_VARS = (
    "GRAFANA_TOKEN", "GRAFANA_VIEWER_TOKEN", "GRAFANA_USER", "GRAFANA_PASSWORD",
    "HOMEPAGE_VAR_GRAFANA_USER", "HOMEPAGE_VAR_GRAFANA_PASSWORD",
)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in CRED_VARS and k != "LGTM_TAILNET"}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


class TailnetTests(unittest.TestCase):
    def test_env_override_wins_and_is_normalised(self):
        with clean_env(LGTM_TAILNET="tailnet.test."):
            self.assertEqual(lgtm.tailnet_suffix(), "tailnet.test")
            self.assertEqual(lgtm.endpoints()["otlp_http"], "http://otel.tailnet.test:4318")

    def test_unresolvable_tailnet_fails_closed(self):
        with clean_env(), \
                mock.patch.object(lgtm.subprocess, "run", side_effect=OSError), \
                mock.patch("builtins.open", side_effect=OSError):
            with self.assertRaises(lgtm.LgtmError):
                lgtm.tailnet_suffix()


class CredentialTests(unittest.TestCase):
    def test_token_beats_basic_auth(self):
        with clean_env(GRAFANA_VIEWER_TOKEN="tok", GRAFANA_USER="u", GRAFANA_PASSWORD="p"):
            self.assertEqual(lgtm.grafana_auth_header(), "Bearer tok")
            self.assertEqual(lgtm.credential_source(), "GRAFANA_VIEWER_TOKEN")

    def test_prod_names_build_basic_auth(self):
        with clean_env(HOMEPAGE_VAR_GRAFANA_USER="u", HOMEPAGE_VAR_GRAFANA_PASSWORD="p"):
            expected = "Basic " + base64.b64encode(b"u:p").decode()
            self.assertEqual(lgtm.grafana_auth_header(), expected)

    def test_half_a_basic_pair_is_no_credential(self):
        with clean_env(GRAFANA_USER="u"):
            self.assertIsNone(lgtm.grafana_auth_header())
            self.assertEqual(lgtm.credential_source(), "none")

    def test_credentialed_command_without_credential_exits_1(self):
        with clean_env(LGTM_TAILNET="tailnet.test"), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(lgtm.main(["logs", "{x=\"y\"}"]), 1)
        self.assertIn("no Grafana credential", err.getvalue())

    def test_doctor_never_prints_secret_values(self):
        with clean_env(LGTM_TAILNET="tailnet.test", GRAFANA_TOKEN="s3cr3t-value"), \
                mock.patch.object(lgtm, "status_code", return_value="200"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            lgtm.main(["doctor"])
        self.assertNotIn("s3cr3t-value", out.getvalue())
        self.assertIn("grafana credential: GRAFANA_TOKEN", out.getvalue())


class OutputTests(unittest.TestCase):
    def test_duration_parsing(self):
        self.assertEqual(lgtm.parse_duration("90s"), 90)
        self.assertEqual(lgtm.parse_duration("2h"), 7200)
        with self.assertRaises(lgtm.LgtmError):
            lgtm.parse_duration("2 hours")

    def test_vector_output_is_capped_and_reports_overflow(self):
        data = {
            "resultType": "vector",
            "result": [{"metric": {"__name__": "up", "job": f"j{i}"}, "value": [0, "1"]} for i in range(5)],
        }
        with contextlib.redirect_stdout(io.StringIO()) as out:
            lgtm.emit_vector(data, limit=2)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], 'up{job="j0"}  1')
        self.assertEqual(len(lines), 3)
        self.assertIn("3 more series", lines[-1])

    def test_matrix_output_summarises_points(self):
        data = {"resultType": "matrix", "result": [{"metric": {}, "values": [[0, "1"], [60, "4"]]}]}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            lgtm.emit_vector(data, limit=10)
        self.assertEqual(out.getvalue().strip(), "{}  points=2 first=1 last=4")


if __name__ == "__main__":
    unittest.main()
