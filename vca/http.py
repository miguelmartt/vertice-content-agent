"""Cliente HTTP mínimo con la librería estándar, reintentos y errores sin secretos."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Mapping, Optional, Tuple

USER_AGENT = "vertice-content-agent (+https://github.com/miguelmartt/vertice-content-agent)"
RETRY_STATUS = {429, 500, 502, 503, 504}


SECRET_PATTERNS = [
    (re.compile(r"bot\d+:[A-Za-z0-9_-]+"), "bot***"),
    (re.compile(r"(?i)(bearer|token|basic)\s+[A-Za-z0-9._~+/=-]+"), r"\1 ***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+"), "***"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]+"), "***"),
    (re.compile(r"1//[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9._-]+|GOCSPX-[A-Za-z0-9_-]+"), "***"),
]


def redact(text: str) -> str:
    """Quita tokens (Telegram, GitHub, Claude, Google) y cabeceras de autorización de un texto."""
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class _NoAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Si una redirección cambia de host o de esquema, no le pasa la cabecera Authorization."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        old, target = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if new is not None and (old.scheme, old.netloc) != (target.scheme, target.netloc):
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(_NoAuthOnRedirect)


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
            with _opener.open(req, timeout=timeout) as resp:
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
