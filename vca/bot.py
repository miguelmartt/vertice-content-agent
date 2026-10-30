"""Conversación con Miguel por Telegram: comandos, botones y respuestas de texto.

Solo atiende al chat configurado en VCA_TELEGRAM_CHAT_ID; el resto se ignora.
Lo rápido (estado, ayuda) se responde aquí; lo pesado va a la cola del
trabajador para no bloquear el bot.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

from .telegram import TelegramError, esc

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger("vca.bot")

HELP = """🤖 <b>Agente de contenido de VérticeDev</b>

Cada semana investigo qué se busca (Search Console y autocompletado de Google), te propongo temas y, si apruebas uno, lo redacto y abro el PR. Al pulsar «Publicar» espero a que CI pase y lo fusiono; el despliegue lo lanzas tú.

/plan · propuestas y borradores abiertos
/investigar · hacer la investigación y el plan ahora
/estado · qué estoy haciendo y qué tengo pendiente
/aprendizaje · qué he aprendido y cómo puntúo
/informe · informe de servicios ahora
/cancelar · olvidar la respuesta que estoy esperando
/ayuda · este mensaje"""

ASK = {
    "adjust": "💬 ¿Qué quieres cambiar del enfoque? Escríbemelo y lo redacto con eso en cuenta.",
    "reason": "🗑 Descartada. ¿Por qué? Una frase me sirve para aprender (o /saltar).",
    "changes": "✏️ ¿Qué cambio? Escríbemelo y rehago el borrador.",
}


class Bot:
    def __init__(self, app: "App"):
        self.app = app
        self.db = app.db
        self.tg = app.tg
        self.chat_id = str(app.cfg.telegram_chat_id)

    # --- bucle --------------------------------------------------------------

    def poll_forever(self) -> None:
        if not self.app.cfg.telegram_token:
            log.warning("Sin VCA_TELEGRAM_TOKEN: el bot no escucha")
            return
        offset = self.db.get_meta("tg_offset")
        backoff = 5
        while not self.app.stop.is_set():
            try:
                updates = self.tg.updates(offset)
                backoff = 5
            except TelegramError as exc:
                log.warning("Telegram: %s", exc)
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                self.db.set_meta("tg_offset", offset)
                try:
                    self.handle(upd)
                except Exception:
                    log.exception("Error atendiendo a Telegram")

    # --- despacho -----------------------------------------------------------

    def handle(self, upd: Dict[str, Any]) -> None:
        if "callback_query" in upd:
            cq = upd["callback_query"]
            chat = str(cq.get("message", {}).get("chat", {}).get("id", ""))
            if chat != self.chat_id:
                log.warning("Botón de un chat no autorizado: %s", chat)
                return
            self.on_button(cq)
            return
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id", "")) != self.chat_id:
            log.warning("Mensaje de un chat no autorizado: %s", msg.get("chat", {}).get("id"))
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text.startswith("/"):
            self.on_command(text.split()[0].split("@")[0].lower())
        else:
            self.on_text(text)

    def on_command(self, command: str) -> None:
        app = self.app
        if command in ("/start", "/ayuda", "/help"):
            self.tg.send(HELP)
        elif command == "/estado":
            self.tg.send(app.status_text())
        elif command == "/aprendizaje":
            self.tg.send(app.learning_text())
        elif command == "/plan":
            self.show_open()
        elif command == "/investigar":
            self.tg.send("🔎 Apuntado: hago la investigación y el plan ahora.")
            app.enqueue("plan", app.tracked("plan", app.plan_week))
        elif command == "/informe":
            self.tg.send("💼 Apuntado: preparo el informe de servicios.")
            app.enqueue("report", app.tracked("report", app.monthly))
        elif command in ("/cancelar", "/saltar"):
            self.db.set_meta("awaiting", None)
            self.tg.send("Hecho.")
        else:
            self.tg.send("No conozco ese comando. /ayuda")

    def show_open(self) -> None:
        open_items = self.db.proposals(["proposed", "approved", "drafting", "review", "publishing", "merged", "failed"])
        if not open_items:
            self.tg.send("No hay nada abierto. /investigar para preparar un plan.")
            return
        labels = {
            "proposed": "por decidir", "approved": "en cola", "drafting": "redactando", "review": "borrador por revisar",
            "publishing": "esperando a CI", "merged": "fusionado, falta desplegar", "failed": "con error",
        }
        self.tg.send("📋 <b>Abierto</b>\n" + "\n".join(
            f"• #{p['id']} {esc(p['title'])} · {labels[p['status']]}" for p in open_items))
        for p in open_items:
            if p["status"] == "proposed":
                self.app.send_proposal(p)

    # --- botones ------------------------------------------------------------

    def on_button(self, cq: Dict[str, Any]) -> None:
        action, _, raw_id = (cq.get("data") or "").partition(":")
        message_id = cq.get("message", {}).get("message_id")
        try:
            pid = int(raw_id)
        except ValueError:
            self.tg.answer(cq["id"], "Botón no válido")
            return
        p = self.db.proposal(pid)
        if not p:
            self.tg.answer(cq["id"], "Ya no existe")
            return
        app = self.app
        expected = {
            "draft": ("proposed",), "adjust": ("proposed",), "reject": ("proposed",),
            "publish": ("review",), "changes": ("review", "failed"), "discard": ("review", "failed", "proposed"),
            "retry": ("failed",),
        }
        if action not in expected:
            self.tg.answer(cq["id"], "Acción desconocida")
            return
        if p["status"] not in expected[action]:
            self.tg.answer(cq["id"], f"Ya está en estado «{p['status']}»")
            self.tg.edit_buttons(message_id)
            return
        self.tg.answer(cq["id"])
        self.tg.edit_buttons(message_id)
        values = {"category": p["category"], "intent": p["intent"], "kind": p["kind"]}

        if action == "draft":
            self.approve(p, values)
            app.enqueue(f"redactar #{pid}", app.draft, pid)
        elif action == "adjust":
            self.db.set_meta("awaiting", {"mode": "adjust", "id": pid})
            self.tg.send(ASK["adjust"])
        elif action == "reject":
            self.db.update_proposal(pid, app.now(), status="rejected")
            for dim, value in values.items():
                self.db.bump_pref(dim, value, approved=False)
            self.db.set_meta("awaiting", {"mode": "reason", "id": pid})
            self.tg.send(ASK["reason"])
        elif action == "publish":
            self.tg.send(f"🚀 Publico «{esc(p['title'])}»: rehago la rama sobre main y espero a CI.")
            app.enqueue(f"publicar #{pid}", app.publish, pid)
        elif action == "changes":
            self.db.set_meta("awaiting", {"mode": "changes", "id": pid})
            self.tg.send(ASK["changes"])
        elif action == "discard":
            if p["status"] == "proposed":
                for dim, value in values.items():
                    self.db.bump_pref(dim, value, approved=False)
            app.enqueue(f"descartar #{pid}", app.discard, pid)
        elif action == "retry":
            last = p.get("last_action") or "draft"
            fn = app.publish if last == "publish" else app.draft
            self.db.update_proposal(pid, app.now(), status="review" if last == "publish" else "approved")
            self.tg.send(f"🔁 Reintento «{esc(p['title'])}».")
            app.enqueue(f"reintentar #{pid}", fn, pid)

    def approve(self, p: Dict[str, Any], values: Dict[str, str]) -> None:
        self.db.update_proposal(p["id"], self.app.now(), status="approved")
        for dim, value in values.items():
            self.db.bump_pref(dim, value, approved=True)
        self.tg.send(f"👍 Apuntado: redacto «{esc(p['title'])}».")

    # --- texto libre --------------------------------------------------------

    def on_text(self, text: str) -> None:
        awaiting: Optional[Dict[str, Any]] = self.db.get_meta("awaiting")
        if not awaiting:
            self.tg.send("No estoy esperando ninguna respuesta. /ayuda")
            return
        self.db.set_meta("awaiting", None)
        pid = int(awaiting["id"])
        p = self.db.proposal(pid)
        if not p:
            return
        app = self.app
        mode = awaiting["mode"]
        feedback = list(p.get("feedback") or []) + [text]
        if mode == "reason":
            self.db.update_proposal(pid, app.now(), rejection=text)
            self.tg.send("Gracias, lo tendré en cuenta.")
        elif mode == "adjust" and p["status"] == "proposed":
            self.db.update_proposal(pid, app.now(), feedback=feedback)
            self.approve(p, {"category": p["category"], "intent": p["intent"], "kind": p["kind"]})
            app.enqueue(f"redactar #{pid}", app.draft, pid)
        elif mode == "changes" and p["status"] in ("review", "failed"):
            self.db.update_proposal(pid, app.now(), feedback=feedback, status="approved")
            self.tg.send(f"✏️ Rehago «{esc(p['title'])}» con tus cambios.")
            app.enqueue(f"rehacer #{pid}", app.draft, pid)
        else:
            self.tg.send(f"«{esc(p['title'])}» ya no está esperando eso (estado {p['status']}).")
