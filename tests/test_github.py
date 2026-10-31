"""Lectura de CI en GitHub: check runs o, si el token no puede, GitHub Actions."""

import unittest
from unittest import mock

from vca import github
from vca.http import HttpError

FORBIDDEN = '{"message":"Resource not accessible by personal access token"}'


def fake_api(runs, check_runs_error=None, statuses=()):
    calls = []

    def request_json(method, url, **kwargs):
        calls.append(url)
        if url.endswith("/check-runs"):
            if check_runs_error:
                raise HttpError(check_runs_error, FORBIDDEN, url)
            return {"check_runs": runs}
        if url.endswith("/actions/runs"):
            return {"workflow_runs": runs}
        if url.endswith("/status"):
            return {"total_count": len(statuses), "statuses": list(statuses)}
        raise AssertionError(f"llamada inesperada a {url}")

    return request_json, calls


class ChecksTest(unittest.TestCase):
    def setUp(self):
        self.gh = github.GitHub("token", "miguelmartt/verticedev")

    def checks(self, runs, **kwargs):
        request_json, calls = fake_api(runs, **kwargs)
        with mock.patch.object(github.http, "request_json", side_effect=request_json):
            return self.gh.checks("abc123"), calls

    def test_reads_check_runs(self):
        result, calls = self.checks([{"name": "php-tests", "status": "completed", "conclusion": "success"}])
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "checks")
        self.assertFalse(any(c.endswith("/actions/runs") for c in calls))

    def test_falls_back_to_actions_when_token_cannot_read_checks(self):
        result, _ = self.checks([{"name": "Security", "status": "in_progress", "conclusion": None}], check_runs_error=403)
        self.assertEqual(result["source"], "actions")
        self.assertEqual(result["pending"], ["Security"])
        self.assertFalse(result["ok"])

    def test_failed_workflow_run_is_reported(self):
        result, _ = self.checks([{"name": "Security", "status": "completed", "conclusion": "failure"}], check_runs_error=403)
        self.assertEqual(result["failed"], ["Security"])
        self.assertFalse(result["ok"])

    def test_green_workflow_run_is_ok(self):
        result, _ = self.checks([{"name": "Security", "status": "completed", "conclusion": "success"}], check_runs_error=404)
        self.assertTrue(result["ok"])

    def test_other_errors_are_raised(self):
        with self.assertRaises(HttpError):
            self.checks([], check_runs_error=401)


if __name__ == "__main__":
    unittest.main()
