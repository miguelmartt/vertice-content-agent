"""Search Console por su API REST, en solo lectura y con la librería estándar.

La autenticación usa un refresh token de OAuth con el alcance
`webmasters.readonly`, que se obtiene una vez con `python -m vca gsc-auth`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Sequence

from . import http

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
API = "https://searchconsole.googleapis.com"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
MAX_ROWS_PER_CALL = 25000


class SearchConsole:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, site: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.site = site
        self._token: Optional[str] = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires:
                return self._token
            payload = http.request_json(
                "POST",
                TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            self._token = payload["access_token"]
            self._expires = time.time() + int(payload.get("expires_in", 3600)) - 120
            return self._token

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return http.request_json(
            "POST",
            API + path,
            json_body=body,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            timeout=60,
        ) or {}

    def query(
        self,
        start: date,
        end: date,
        dimensions: Sequence[str],
        row_limit: int = 5000,
        filters: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, Any]]:
        """Filas de rendimiento con las dimensiones pedidas, paginando si hace falta."""
        site = urllib.parse.quote(self.site, safe="")
        rows: List[Dict[str, Any]] = []
        start_row = 0
        while len(rows) < row_limit:
            batch_size = min(row_limit - len(rows), MAX_ROWS_PER_CALL)
            body: Dict[str, Any] = {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": list(dimensions),
                "rowLimit": batch_size,
                "startRow": start_row,
                "dataState": "all",
            }
            if filters:
                body["dimensionFilterGroups"] = [{"filters": filters}]
            batch = self._post(f"/webmasters/v3/sites/{site}/searchAnalytics/query", body).get("rows", [])
            for r in batch:
                row = dict(zip(dimensions, r.get("keys", [])))
                row.update(
                    clicks=int(r.get("clicks", 0)),
                    impressions=int(r.get("impressions", 0)),
                    ctr=float(r.get("ctr", 0.0)),
                    position=float(r.get("position", 0.0)),
                )
                rows.append(row)
            if len(batch) < batch_size:
                break
            start_row += len(batch)
        return rows

    def page_totals(self, page_url: str, start: date, end: date) -> Dict[str, Any]:
        rows = self.query(
            start, end, ["page"], row_limit=10,
            filters=[{"dimension": "page", "operator": "equals", "expression": page_url}],
        )
        if not rows:
            return {"clicks": 0, "impressions": 0, "position": None}
        r = rows[0]
        return {"clicks": r["clicks"], "impressions": r["impressions"], "position": r["position"]}

    def page_queries(self, page_url: str, start: date, end: date, limit: int = 25) -> List[Dict[str, Any]]:
        return self.query(
            start, end, ["query"], row_limit=limit,
            filters=[{"dimension": "page", "operator": "equals", "expression": page_url}],
        )

    def inspect(self, url: str) -> Dict[str, Any]:
        """Estado de indexación de una URL (URL Inspection API)."""
        data = self._post(
            "/v1/urlInspection/index:inspect",
            {"inspectionUrl": url, "siteUrl": self.site, "languageCode": "es-ES"},
        )
        status = data.get("inspectionResult", {}).get("indexStatusResult", {})
        return {
            "verdict": status.get("verdict", "UNKNOWN"),
            "coverage": status.get("coverageState", ""),
            "last_crawl": status.get("lastCrawlTime", ""),
        }


# --- Autorización inicial (se ejecuta una vez, en un equipo con navegador) ----


def load_client_secrets(path: str) -> Dict[str, str]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    block = data.get("installed") or data.get("web") or data
    return {"client_id": block["client_id"], "client_secret": block["client_secret"]}


def authorize(client_id: str, client_secret: str, open_browser: bool = True, timeout: int = 300) -> str:
    """Flujo OAuth de aplicación de escritorio con redirección a 127.0.0.1. Devuelve el refresh token."""
    received: Dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (nombre impuesto por http.server)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            received.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Listo. Ya puedes cerrar esta pestaña.".encode("utf-8"))

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = timeout
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/"
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    print("Abre esta URL e inicia sesión con la cuenta propietaria de Search Console:\n")
    print(url + "\n")
    if open_browser:
        webbrowser.open(url)
    deadline = time.time() + timeout
    while "code" not in received and "error" not in received and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if received.get("error"):
        raise RuntimeError(f"Google ha rechazado la autorización: {received['error']}")
    if received.get("state") != state or "code" not in received:
        raise RuntimeError("No se ha recibido un código de autorización válido.")
    payload = http.request_json(
        "POST",
        TOKEN_URL,
        data={
            "code": received["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
    )
    token = payload.get("refresh_token")
    if not token:
        raise RuntimeError("Google no ha devuelto refresh token; revoca el acceso de la app y repite.")
    return token
