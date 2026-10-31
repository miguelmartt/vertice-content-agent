"""Recorrido completo con dobles: plan → aprobar → borrador y PR → publicar → CI → producción → medición."""

import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from vca import app as app_module
from vca.app import App
from vca.bot import Bot
from vca.config import Config
from tests.helpers import good_draft, site_data

CHAT = "42"
SITE_URL = "https://verticedev.es"


class FakeTG:
    def __init__(self):
        self.messages = []
        self.docs = []

    def send(self, text, buttons=None):
        self.messages.append((text, buttons))
        return len(self.messages)

    def edit_buttons(self, *args, **kwargs):
        pass

    def answer(self, *args, **kwargs):
        pass

    def send_document(self, name, content, caption=""):
        self.docs.append((name, content))

    def text(self):
        return "\n".join(m for m, _ in self.messages)


class FakeGSC:
    def __init__(self):
        self.rows = [
            {"query": "software de gestion a medida", "page": f"{SITE_URL}/servicios/apps-medida",
             "clicks": 0, "impressions": 140, "ctr": 0.0, "position": 64.2},
            {"query": "npm o pnpm", "page": f"{SITE_URL}/blog/pnpm-vs-npm-ventajas",
             "clicks": 1, "impressions": 75, "ctr": 0.01, "position": 8.3},
        ]

    def query(self, start, end, dimensions, row_limit=5000, filters=None):
        return self.rows if list(dimensions) == ["query", "page"] else []

    def page_totals(self, url, start, end):
        return {"clicks": 3, "impressions": 250, "position": 12.0}

    def page_queries(self, url, start, end, limit=25):
        return []

    def inspect(self, url):
        return {"verdict": "PASS", "coverage": "Submitted and indexed", "last_crawl": ""}


class FakeGitHub:
    def __init__(self, ok=True):
        self.ok = ok
        self.error = None
        self.merged = []
        self.closed = []

    def open_pr(self, branch, base, title, body):
        return 7, "https://github.com/miguelmartt/verticedev/pull/7"

    def checks(self, sha):
        if self.error:
            raise self.error
        if self.ok:
            return {"total": 3, "pending": [], "failed": [], "ok": True}
        return {"total": 3, "pending": [], "failed": ["php-tests"], "ok": False}

    def merge(self, number, title, sha):
        self.merged.append(number)
        self.merged_sha = sha
        return "merged-sha"

    def close(self, number):
        self.closed.append(number)

    def delete_branch(self, branch):
        pass


class FakeSite:
    def __init__(self):
        self.written = []

    def sync(self):
        pass

    def load(self):
        return site_data()

    def read_article(self, slug):
        return {"intro": "Intro actual", "body": "## Sección\nTexto actual"}

    def write_article(self, slug, view, entry, kind):
        self.written.append({"slug": slug, "view": view, "entry": entry, "kind": kind})
        return ["app/Support/Blog.php", f"resources/views/blog/{slug}.blade.php"]

    def lint(self, paths):
        pass

    def commit_branch(self, branch, paths, message, push=True):
        return "sha123"


class FakeLLM:
    def __init__(self, plan):
        self.plan = plan
        self.calls = []

    def run(self, task, system, prompt, schema, model, timeout=None):
        self.calls.append((task, prompt))
        if task == "plan":
            return self.plan
        if task.startswith("draft"):
            return good_draft()
        if task == "report":
            return {"summary": "Poco tráfico.", "services": [
                {"slug": "apps-medida", "verdict": "reforzar", "evidence": "140 impr. en pos. 64", "suggestion": "Caso real"}],
                "gaps": []}
        return {"memo": ["Los temas comerciales aún no tienen datos."]}

    def calls_left_today(self):
        return 10


class FakeSuggest:
    def __init__(self, *args, **kwargs):
        self.requests = 0
        self.blocked = False

    def expand(self, seeds):
        return {}


PLAN = {
    "proposals": [
        {"kind": "new", "slug": "Software gestión a medida", "title": "Cómo elegir software de gestión a medida",
         "category": "Negocio", "target_keyword": "software de gestion a medida", "intent": "commercial",
         "angle": "Lo que aprendí con ArrayStock", "outline": ["Qué es", "Cuánto cuesta", "Cómo elegir"],
         "rationale": "140 impresiones en posición 64 sin artículo que lo cubra", "service": "apps-medida"},
        {"kind": "improve", "slug": "pnpm-vs-npm-ventajas", "existing_slug": "pnpm-vs-npm-ventajas",
         "title": "Mejorar npm o pnpm", "category": "DevOps", "target_keyword": "npm o pnpm", "intent": "comparison",
         "angle": "Configuración real del repo", "outline": ["Tabla", "Instalar"], "rationale": "pos. 8,3 y CTR bajo"},
        {"kind": "improve", "slug": "no-existe", "title": "Inventado", "category": "DevOps", "target_keyword": "x",
         "intent": "informational", "angle": "", "outline": [], "rationale": ""},
    ],
    "service_notes": "apps-medida tiene demanda pero rinde mal",
}


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = Config.from_env({
            "VCA_STATE_DIR": str(self.tmp),
            "VCA_TELEGRAM_TOKEN": "123:abc",
            "VCA_TELEGRAM_CHAT_ID": CHAT,
            "VCA_GITHUB_TOKEN": "x",
            "VCA_GITHUB_REPO": "owner/site",
            "VCA_GSC_SITE": "sc-domain:verticedev.es",
            "VCA_SITE_URL": SITE_URL,
            "VCA_DEPLOY_COMMAND": "cd /var/www/web && bash deploy.sh",
            "VCA_TIMEZONE": "UTC",
        })
        self.app = App(cfg)
        self.tg = self.app.tg = FakeTG()
        self.app.gsc = FakeGSC()
        self.github = self.app.github = FakeGitHub()
        self.site = self.app.site = FakeSite()
        self.llm = self.app.llm = FakeLLM(PLAN)
        self.bot = Bot(self.app)
        patcher = mock.patch.object(app_module, "Suggest", FakeSuggest)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def press(self, data, chat=CHAT, sender=None):
        self.bot.handle({"update_id": 1, "callback_query": {
            "id": "cb", "data": data, "from": {"id": int(sender or chat)},
            "message": {"message_id": 1, "chat": {"id": int(chat)}}}})

    def say(self, text, chat=CHAT, sender=None):
        self.bot.handle({"update_id": 2, "message": {
            "chat": {"id": int(chat)}, "from": {"id": int(sender or chat)}, "text": text}})

    def publish(self, pid):
        """Pulsa el «Publicar» del último borrador, como haría el botón real."""
        self.press(f"publish:{pid}:{app_module.draft_version(self.app.db.proposal(pid)['draft'])}")

    def run_queue(self):
        while not self.app.tasks.empty():
            name, fn, args = self.app.tasks.get_nowait()
            self.app.run_task(name, fn, *args)

    def test_full_cycle(self):
        self.app.plan_week()
        proposals = self.app.db.proposals()
        self.assertEqual([p["slug"] for p in proposals], ["software-gestion-a-medida", "pnpm-vs-npm-ventajas"])
        self.assertIn("Plan de la semana", self.tg.text())
        self.assertIn("apps-medida tiene demanda", self.tg.text())
        new = proposals[0]
        self.assertGreater(new["predicted"], 0)

        self.press(f"draft:{new['id']}")
        self.run_queue()
        p = self.app.db.proposal(new["id"])
        self.assertEqual(p["status"], "review")
        self.assertEqual(p["pr_number"], 7)
        self.assertEqual(self.tg.docs[0][0], "software-gestion-a-medida.html")
        written = self.site.written[-1]
        self.assertEqual(written["kind"], "new")
        self.assertEqual(written["entry"]["date"], self.app.today().isoformat())
        self.assertIn("@extends('layouts.article'", written["view"])

        self.publish(new["id"])
        self.run_queue()
        self.assertEqual(self.app.db.proposal(new["id"])["status"], "publishing")

        self.app.ci_watch()
        self.assertEqual(self.github.merged, [7])
        self.assertEqual(self.app.db.proposal(new["id"])["status"], "merged")
        self.assertIn("bash deploy.sh", self.tg.text())

        page = f"<h1>{good_draft()['headline']}</h1>".encode()
        with mock.patch.object(app_module.http, "request", return_value=(200, page)):
            self.app.livecheck()
        self.assertEqual(self.app.db.proposal(new["id"])["status"], "live")

        live_at = (self.app.now() - timedelta(days=60)).isoformat()
        self.app.db.update_proposal(new["id"], self.app.now(), live_at=live_at)
        weights_before = dict(self.app.db.weights())
        self.app.learn_daily()
        self.assertEqual(len(self.app.db.outcomes()), 3)
        self.assertEqual(self.app.db.proposal(new["id"])["status"], "measured")
        self.assertNotEqual(self.app.db.weights(), weights_before)
        self.assertIn("a los 56 días", self.tg.text())

    def test_reject_with_reason_teaches_preferences(self):
        self.app.plan_week()
        p = self.app.db.proposals()[0]
        self.press(f"reject:{p['id']}")
        self.say("No quiero artículos de precios todavía")
        p = self.app.db.proposal(p["id"])
        self.assertEqual(p["status"], "rejected")
        self.assertEqual(p["rejection"], "No quiero artículos de precios todavía")
        self.assertEqual(self.app.db.prefs()[("category", "Negocio")], (0, 1))

    def test_changes_redraft_with_feedback(self):
        self.app.plan_week()
        p = self.app.db.proposals()[0]
        self.press(f"draft:{p['id']}")
        self.run_queue()
        self.press(f"changes:{p['id']}")
        self.say("Añade una tabla de costes por fases")
        self.run_queue()
        p = self.app.db.proposal(p["id"])
        self.assertEqual(p["status"], "review")
        self.assertEqual(p["feedback"], ["Añade una tabla de costes por fases"])
        last_prompt = self.llm.calls[-1][1]
        self.assertIn("Añade una tabla de costes por fases", last_prompt)

    def test_improve_keeps_date_and_sets_modified(self):
        self.app.plan_week()
        improve = self.app.db.proposals()[1]
        self.press(f"draft:{improve['id']}")
        self.run_queue()
        entry = self.site.written[-1]["entry"]
        self.assertEqual(entry["date"], "2026-05-26")
        self.assertEqual(entry["modified"], self.app.today().isoformat())
        prompt = next(p for t, p in self.llm.calls if t == "draft")
        self.assertIn("articulo_actual", prompt)

    def test_ci_failure_returns_to_review(self):
        self.github.ok = False
        self.app.plan_week()
        p = self.app.db.proposals()[0]
        self.press(f"draft:{p['id']}")
        self.run_queue()
        self.publish(p["id"])
        self.run_queue()
        self.app.ci_watch()
        self.assertEqual(self.app.db.proposal(p["id"])["status"], "review")
        self.assertEqual(self.github.merged, [])
        self.assertIn("CI ha fallado", self.tg.text())

    def test_unreadable_ci_is_reported_after_timeout(self):
        self.github.error = app_module.http.HttpError(403, "Resource not accessible", "https://api.github.com/x")
        self.app.plan_week()
        p = self.app.db.proposals()[0]
        self.press(f"draft:{p['id']}")
        self.run_queue()
        self.publish(p["id"])
        self.run_queue()
        self.app.ci_watch()
        self.assertEqual(self.app.db.proposal(p["id"])["status"], "publishing")
        since = (self.app.now() - timedelta(minutes=self.app.cfg.ci_timeout_minutes + 1)).isoformat()
        self.app.db.update_proposal(p["id"], self.app.now(), publishing_since=since)
        self.app.ci_watch()
        self.assertEqual(self.app.db.proposal(p["id"])["status"], "review")
        self.assertEqual(self.github.merged, [])
        self.assertIn("No puedo leer la CI", self.tg.text())

    def test_other_chats_are_ignored(self):
        self.app.plan_week()
        p = self.app.db.proposals()[0]
        count = len(self.tg.messages)
        self.press(f"draft:{p['id']}", chat="999")
        self.say("/estado", chat="999")
        self.assertEqual(len(self.tg.messages), count)
        self.assertEqual(self.app.db.proposal(p["id"])["status"], "proposed")

    def test_other_members_of_the_chat_are_ignored(self):
        # Si el bot acabara en un grupo con el id configurado, solo el dueño puede actuar.
        self.app.plan_week()
        p = self.app.db.proposals()[0]
        count = len(self.tg.messages)
        self.press(f"draft:{p['id']}", sender="777")
        self.say("/investigar", sender="777")
        self.assertEqual(len(self.tg.messages), count)
        self.assertEqual(self.app.db.proposal(p["id"])["status"], "proposed")

    def test_publish_button_of_an_old_draft_does_nothing(self):
        self.app.plan_week()
        p = self.app.db.proposals()[0]
        self.press(f"draft:{p['id']}")
        self.run_queue()
        self.press(f"publish:{p['id']}:{app_module.draft_version({'otro': 'borrador'})}")
        self.press(f"publish:{p['id']}")
        self.assertTrue(self.app.tasks.empty())
        self.assertEqual(self.app.db.proposal(p["id"])["status"], "review")

    def test_worker_survives_telegram_failures(self):
        def broken_send(text, buttons=None):
            raise app_module.http.HttpError(0, "sin red", "https://api.telegram.org/x")

        self.tg.send = broken_send
        with self.assertLogs("vca", level="ERROR"):
            self.app.run_task("falla", lambda: 1 / 0)  # no debe propagar aunque Telegram tampoco responda

    def test_double_press_is_harmless(self):
        self.app.plan_week()
        p = self.app.db.proposals()[0]
        self.press(f"draft:{p['id']}")
        self.press(f"draft:{p['id']}")
        self.assertEqual(self.app.tasks.qsize(), 1)

    def test_monthly_report(self):
        self.app.plan_week()
        self.app.monthly()
        self.assertIn("Informe mensual de servicios", self.tg.text())
        self.assertIn("reforzar", self.tg.text())

    def test_status_and_learning_texts(self):
        self.app.plan_week()
        self.assertIn("en el plan: 2", self.app.status_text())
        self.assertIn("Pesos", self.app.learning_text())


if __name__ == "__main__":
    unittest.main()
