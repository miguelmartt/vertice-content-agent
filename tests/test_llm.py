import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from vca.db import DB
from vca.llm import LLM, LLMError


class FakeProc:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class LLMTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = DB(self.tmp / "t.sqlite3")
        self.db.init()
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def llm(self, result, max_calls=5):
        def runner(cmd, **kwargs):
            self.calls.append((cmd, kwargs))
            if isinstance(result, Exception):
                raise result
            return result
        return LLM("claude", self.tmp / "sandbox", self.db, "Europe/Madrid", max_calls, 60, runner=runner)

    def test_structured_output_and_flags(self):
        out = {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"n": 4},
               "total_cost_usd": 0.01}
        result = self.llm(FakeProc(json.dumps(out))).run("t", "sistema", "hola", {"type": "object"}, "sonnet")
        self.assertEqual(result, {"n": 4})
        cmd, kwargs = self.calls[0]
        self.assertIn("--tools", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertNotIn("--bare", cmd)  # --bare ignora la suscripción (OAuth)
        self.assertEqual(kwargs["input"], "hola")
        self.assertEqual(self.db.query("SELECT ok, cost_usd FROM llm_calls"), [{"ok": 1, "cost_usd": 0.01}])

    def test_falls_back_to_result_json(self):
        out = {"subtype": "success", "result": '{"n": 5}'}
        self.assertEqual(self.llm(FakeProc(json.dumps(out))).run("t", "s", "p", {}, "m"), {"n": 5})

    def test_error_is_logged(self):
        out = {"subtype": "error_max_turns", "is_error": True, "result": "límite"}
        with self.assertRaises(LLMError):
            self.llm(FakeProc(json.dumps(out), returncode=1)).run("t", "s", "p", {}, "m")
        self.assertEqual(self.db.query("SELECT ok FROM llm_calls")[0]["ok"], 0)

    def test_timeout(self):
        with self.assertRaises(LLMError):
            self.llm(subprocess.TimeoutExpired("claude", 60)).run("t", "s", "p", {}, "m")

    def test_daily_cap(self):
        out = json.dumps({"subtype": "success", "structured_output": {}})
        llm = self.llm(FakeProc(out), max_calls=1)
        llm.run("t", "s", "p", {}, "m")
        with self.assertRaises(LLMError):
            llm.run("t", "s", "p", {}, "m")


if __name__ == "__main__":
    unittest.main()
