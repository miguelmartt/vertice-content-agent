"""Bot API de Telegram por long polling, sin puertos abiertos ni librerías externas."""

from __future__ import annotations

import html
import json
import secrets
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import http
from .util import split_message

API = "https://api.telegram.org/bot{token}/{method}"

Button = Tuple[str, str]  # (texto, callback_data)


def esc(text: Any) -> str:
    return html.escape(str(text), quote=False)


def keyboard(rows: Sequence[Sequence[Button]]) -> Dict[str, Any]:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, dry_run: bool = False, printer: Callable[[str], None] = print):
        self.token = token
        self.chat_id = str(chat_id)
        self.dry_run = dry_run
        self.printer = printer

    def call(self, method: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 30,
             body: Optional[bytes] = None, content_type: Optional[str] = None) -> Any:
        url = API.format(token=self.token, method=method)
        try:
            if body is not None:
                _, raw = http.request("POST", url, body=body, headers={"Content-Type": content_type or ""},
                                      timeout=timeout)
            else:
                _, raw = http.request("POST", url, json_body=payload or {}, timeout=timeout)
        except http.HttpError as exc:
            raise TelegramError(str(exc)) from None
        data = json.loads(raw.decode("utf-8"))
        if not data.get("ok"):
            raise TelegramError(data.get("description", "respuesta no válida de Telegram"))
        return data.get("result")

    def send(self, text: str, buttons: Optional[Sequence[Sequence[Button]]] = None) -> Optional[int]:
        """Envía un mensaje HTML (troceado si es largo). Devuelve el id del último trozo."""
        parts = split_message(text)
        if self.dry_run:
            self.printer("[telegram] " + text + (f"\n[botones] {buttons}" if buttons else ""))
            return None
        message_id = None
        for i, part in enumerate(parts):
            payload: Dict[str, Any] = {
                "chat_id": self.chat_id,
                "text": part,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if buttons and i == len(parts) - 1:
                payload["reply_markup"] = keyboard(buttons)
            message_id = self.call("sendMessage", payload)["message_id"]
        return message_id

    def edit_buttons(self, message_id: Optional[int], buttons: Optional[Sequence[Sequence[Button]]] = None) -> None:
        if self.dry_run or not message_id:
            return
        try:
            self.call("editMessageReplyMarkup", {
                "chat_id": self.chat_id,
                "message_id": message_id,
                "reply_markup": keyboard(buttons or []),
            })
        except TelegramError:
            pass  # el mensaje puede ser antiguo o no haber cambiado

    def answer(self, callback_id: str, text: str = "") -> None:
        if self.dry_run:
            return
        try:
            self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})
        except TelegramError:
            pass

    def send_document(self, filename: str, content: bytes, caption: str = "") -> None:
        if self.dry_run:
            self.printer(f"[telegram] documento {filename} ({len(content)} bytes) {caption}")
            return
        boundary = "vca" + secrets.token_hex(12)
        fields = {"chat_id": self.chat_id, "caption": caption[:1000], "parse_mode": "HTML"}
        lines: List[bytes] = []
        for name, value in fields.items():
            lines += [
                f"--{boundary}".encode(),
                f'Content-Disposition: form-data; name="{name}"'.encode(),
                b"",
                str(value).encode("utf-8"),
            ]
        lines += [
            f"--{boundary}".encode(),
            f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode(),
            b"Content-Type: text/html; charset=utf-8",
            b"",
            content,
            f"--{boundary}--".encode(),
            b"",
        ]
        self.call("sendDocument", body=b"\r\n".join(lines),
                  content_type=f"multipart/form-data; boundary={boundary}", timeout=60)

    def updates(self, offset: Optional[int], timeout: int = 50) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload, timeout=timeout + 15) or []

    def me(self) -> Dict[str, Any]:
        return self.call("getMe")
