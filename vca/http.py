"""Cliente HTTP mínimo con la librería estándar, reintentos y errores sin secretos."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Mapping, Optional, Tuple

USER_AGENT = "vertice-content-agent/0.1 (+https://verticedev.es)"
RETRY_STATUS = {429, 500, 502, 503, 504}


def redact(text: str) -> str:
    """Quita tokens de Telegram y cabeceras de autorización de un texto de error."""
    text = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot***", text)
    return re.sub(r"(?i)(bearer|token)\s+[A-Za-z0-9._-]+", r"\1 ***", text)


class HttpError(Exception):
    def __init__(self, status: int, body: str, url: str):
        self.status = status
        self.body = body
        self.url = redact(url.split("?", 1)[0])
        super().__init__(f"HTTP {status} en {self.url}: {redact(body[:300])}")


def request(
    method: str,
    url: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    data: Optional[Mapping[str, Any]] = None,
    json_body: Any = None,
    body: Optional[bytes] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 30,
    retries: int = 2,
    backoff: float = 2.0,
) -> Tuple[int, bytes]:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    hdrs: Dict[str, str] = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
    elif data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")

    attempt = 0
    while True:
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            if exc.code in RETRY_STATUS and attempt < retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff * (2 ** attempt)
                time.sleep(min(delay, 60))
                attempt += 1
                continue
            raise HttpError(exc.code, payload, url) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                attempt += 1
                continue
            raise HttpError(0, str(getattr(exc, "reason", exc)), url) from None


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    _, payload = request(method, url, **kwargs)
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))
