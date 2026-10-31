"""Secretos y entorno: lo que no debe salir de donde está."""

import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from vca import http
from vca.config import REQUIRED, load_env_file
from vca.llm import claude_env
from vca.util import child_env


class EnvFileTest(unittest.TestCase):
    def test_values_are_data_not_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "env"
            path.write_text(
                "# comentario\n"
                "VCA_A=$(touch /tmp/x)\n"
                'VCA_B="mon 08:00"\n'
                "VCA_C=`id`\n"
                "no es una linea\n"
                "1BAD=x\n",
                encoding="utf-8",
            )
            env = {}
            load_env_file(str(path), env)
        self.assertEqual(env, {"VCA_A": "$(touch /tmp/x)", "VCA_B": "mon 08:00", "VCA_C": "`id`"})

    def test_example_lists_every_required_variable(self):
        env = {}
        load_env_file(str(Path(__file__).resolve().parent.parent / ".env.example"), env)
        required = {name for names in REQUIRED.values() for name in names} | {"CLAUDE_CODE_OAUTH_TOKEN"}
        self.assertEqual(required - set(env), set())
        self.assertEqual(env["VCA_PLAN_SCHEDULE"], "mon 08:00")


class ChildEnvTest(unittest.TestCase):
    ENV = {
        "PATH": "/usr/bin", "HOME": "/var/lib/x", "VCA_GITHUB_TOKEN": "github_pat_x", "VCA_TELEGRAM_TOKEN": "1:x",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x", "ANTHROPIC_API_KEY": "sk-ant-api-x", "DISABLE_AUTOUPDATER": "1",
    }

    def test_subprocesses_do_not_get_agent_tokens(self):
        with mock.patch.dict(os.environ, self.ENV, clear=True):
            env = child_env()
        self.assertEqual(set(env), {"PATH", "HOME"})

    def test_claude_gets_only_its_own_token_and_never_an_api_key(self):
        with mock.patch.dict(os.environ, self.ENV, clear=True):
            env = claude_env()
        self.assertEqual(set(env), {"PATH", "HOME", "CLAUDE_CODE_OAUTH_TOKEN", "DISABLE_AUTOUPDATER"})


class RedactTest(unittest.TestCase):
    def test_known_token_formats_are_hidden(self):
        text = ("https://api.telegram.org/bot123456:AAH-abc_def/getMe github_pat_11ABCDEF_xyz "
                "ghp_abcdefghijklmnopqrstuvwxyz0123456789 sk-ant-oat01-abc-def Bearer abc.def "
                "1//0gAbcdefghijklmnopqrstuvwxyz GOCSPX-abcdef")
        out = http.redact(text)
        for secret in ("AAH-abc_def", "11ABCDEF", "ghp_", "oat01", "abc.def", "1//0g", "GOCSPX"):
            self.assertNotIn(secret, out)


class RedirectTest(unittest.TestCase):
    def redirect(self, src, dst):
        req = urllib.request.Request(src, headers={"Authorization": "Bearer secreto"})
        return http._NoAuthOnRedirect().redirect_request(req, None, 302, "Found", {}, dst)

    def test_authorization_is_dropped_on_another_host(self):
        self.assertFalse(self.redirect("https://api.github.com/a", "https://evil.example/b").has_header("Authorization"))

    def test_authorization_is_dropped_on_https_to_http(self):
        self.assertFalse(self.redirect("https://api.github.com/a", "http://api.github.com/b").has_header("Authorization"))

    def test_authorization_is_kept_on_the_same_host(self):
        self.assertTrue(self.redirect("https://api.github.com/a", "https://api.github.com/b").has_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
