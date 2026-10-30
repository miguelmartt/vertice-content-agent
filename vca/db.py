"""Estado persistente en SQLite: propuestas, palabras clave, aprendizaje y trabajos."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS gsc_rows (
    snapshot TEXT NOT NULL,
    query TEXT NOT NULL,
    page TEXT NOT NULL,
    clicks INTEGER NOT NULL,
    impressions INTEGER NOT NULL,
    position REAL,
    PRIMARY KEY (snapshot, query, page)
);
CREATE TABLE IF NOT EXISTS keywords (
    keyword TEXT PRIMARY KEY,
    snapshot TEXT NOT NULL,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    position REAL,
    page TEXT,
    suggest_hits INTEGER NOT NULL DEFAULT 0,
    suggest_rank INTEGER,
    intent TEXT,
    covered_by TEXT,
    service TEXT,
    features TEXT,
    score REAL
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    kind TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT,
    target_keyword TEXT,
    keywords TEXT,
    intent TEXT,
    angle TEXT,
    outline TEXT,
    rationale TEXT,
    service TEXT,
    features TEXT,
    predicted REAL,
    status TEXT NOT NULL,
    feedback TEXT,
    rejection TEXT,
    draft TEXT,
    branch TEXT,
    pr_number INTEGER,
    pr_url TEXT,
    head_sha TEXT,
    publishing_since TEXT,
    merged_at TEXT,
    live_at TEXT,
    indexed_at TEXT,
    index_alerted INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_action TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
    proposal_id INTEGER NOT NULL,
    day INTEGER NOT NULL,
    measured TEXT NOT NULL,
    impressions INTEGER,
    clicks INTEGER,
    position REAL,
    baseline_impressions INTEGER,
    score REAL,
    PRIMARY KEY (proposal_id, day)
);
CREATE TABLE IF NOT EXISTS weights (
    name TEXT PRIMARY KEY,
    value REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS prefs (
    dim TEXT NOT NULL,
    value TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (dim, value)
);
CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    memo TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    task TEXT NOT NULL,
    ok INTEGER NOT NULL,
    duration_ms INTEGER,
    cost_usd REAL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    name TEXT PRIMARY KEY,
    last_run TEXT,
    ok INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
"""

JSON_FIELDS = {"keywords", "outline", "features", "feedback", "draft"}

# Estados de una propuesta, en orden de vida.
STATUSES = (
    "proposed",    # en el plan, esperando decisión
    "approved",    # aprobada, en cola para redactar
    "drafting",    # redactando
    "review",      # borrador y PR listos, esperando decisión
    "publishing",  # rama actualizada, esperando a CI para fusionar
    "merged",      # fusionado en main, esperando al despliegue
    "live",        # visible en producción, en medición
    "measured",    # medición final hecha
    "rejected",    # descartada en el plan
    "discarded",   # borrador descartado
    "expired",     # nadie la decidió en una semana
    "failed",      # error; se puede reintentar
)
OPEN_STATUSES = ("proposed", "approved", "drafting", "review", "publishing", "failed")


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def init(self) -> None:
        self.conn().executescript(SCHEMA)
        self.conn().commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        conn = self.conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        conn = self.conn()
        conn.executemany(sql, rows)
        conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn().execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        row = self.conn().execute(sql, params).fetchone()
        return dict(row) if row else None

    # --- meta ---------------------------------------------------------------

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        if value is None:
            self.execute("DELETE FROM meta WHERE key = ?", (key,))
            return
        self.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    # --- eventos ------------------------------------------------------------

    def log_event(self, at: datetime, kind: str, detail: Any = None) -> None:
        text = detail if isinstance(detail, str) or detail is None else json.dumps(detail, ensure_ascii=False)
        self.execute("INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)", (at.isoformat(), kind, text))

    # --- propuestas ---------------------------------------------------------

    @staticmethod
    def _encode(fields: Dict[str, Any]) -> Dict[str, Any]:
        return {
            k: json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS and v is not None else v
            for k, v in fields.items()
        }

    @staticmethod
    def _decode(row: Dict[str, Any]) -> Dict[str, Any]:
        for key in JSON_FIELDS:
            if row.get(key):
                row[key] = json.loads(row[key])
        return row

    def add_proposal(self, at: datetime, fields: Dict[str, Any]) -> int:
        data = self._encode({**fields, "created": at.isoformat(), "updated": at.isoformat()})
        data.setdefault("status", "proposed")
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        return int(self.execute(f"INSERT INTO proposals ({cols}) VALUES ({marks})", list(data.values())).lastrowid)

    def update_proposal(self, pid: int, at: datetime, **fields: Any) -> None:
        data = self._encode({**fields, "updated": at.isoformat()})
        sets = ", ".join(f"{k} = ?" for k in data)
        self.execute(f"UPDATE proposals SET {sets} WHERE id = ?", [*data.values(), pid])

    def proposal(self, pid: int) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT * FROM proposals WHERE id = ?", (pid,))
        return self._decode(row) if row else None

    def proposals(self, statuses: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        if statuses:
            marks = ", ".join("?" for _ in statuses)
            rows = self.query(f"SELECT * FROM proposals WHERE status IN ({marks}) ORDER BY id", list(statuses))
        else:
            rows = self.query("SELECT * FROM proposals ORDER BY id")
        return [self._decode(r) for r in rows]

    def recent_decisions(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.query(
            "SELECT id, kind, slug, title, category, intent, target_keyword, status, rejection, feedback "
            "FROM proposals WHERE status NOT IN ('proposed', 'expired') ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [self._decode(r) for r in rows]

    def status_counts(self) -> Dict[str, int]:
        rows = self.query("SELECT status, COUNT(*) AS n FROM proposals GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    # --- Search Console y palabras clave ------------------------------------

    def save_gsc_snapshot(self, snapshot: str, rows: Iterable[Dict[str, Any]]) -> None:
        self.execute("DELETE FROM gsc_rows WHERE snapshot = ?", (snapshot,))
        self.executemany(
            "INSERT OR REPLACE INTO gsc_rows (snapshot, query, page, clicks, impressions, position) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (snapshot, r["query"], r["page"], int(r["clicks"]), int(r["impressions"]), r.get("position"))
                for r in rows
            ],
        )

    def latest_gsc_snapshot(self) -> List[Dict[str, Any]]:
        row = self.one("SELECT MAX(snapshot) AS s FROM gsc_rows")
        if not row or not row["s"]:
            return []
        return self.query("SELECT * FROM gsc_rows WHERE snapshot = ?", (row["s"],))

    def save_keywords(self, snapshot: str, rows: Iterable[Dict[str, Any]]) -> None:
        self.execute("DELETE FROM keywords")
        self.executemany(
            "INSERT OR REPLACE INTO keywords (keyword, snapshot, impressions, clicks, position, page, suggest_hits, "
            "suggest_rank, intent, covered_by, service, features, score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r["keyword"], snapshot, r["impressions"], r["clicks"], r["position"], r["page"],
                    r["suggest_hits"], r["suggest_rank"], r["intent"], r["covered_by"], r["service"],
                    json.dumps(r["features"]), r["score"],
                )
                for r in rows
            ],
        )

    def keyword(self, keyword: str) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT * FROM keywords WHERE keyword = ?", (keyword,))
        if row and row.get("features"):
            row["features"] = json.loads(row["features"])
        return row

    # --- aprendizaje --------------------------------------------------------

    def weights(self) -> Dict[str, float]:
        return {r["name"]: r["value"] for r in self.query("SELECT name, value FROM weights")}

    def set_weights(self, weights: Dict[str, float]) -> None:
        self.executemany(
            "INSERT INTO weights (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            list(weights.items()),
        )

    def bump_pref(self, dim: str, value: str, approved: bool) -> None:
        if not value:
            return
        column = "approved" if approved else "rejected"
        self.execute(
            f"INSERT INTO prefs (dim, value, {column}) VALUES (?, ?, 1) "
            f"ON CONFLICT(dim, value) DO UPDATE SET {column} = {column} + 1",
            (dim, value),
        )

    def prefs(self) -> Dict[Tuple[str, str], Tuple[int, int]]:
        return {
            (r["dim"], r["value"]): (r["approved"], r["rejected"])
            for r in self.query("SELECT dim, value, approved, rejected FROM prefs")
        }

    def add_outcome(self, pid: int, day: int, at: datetime, impressions: int, clicks: int,
                    position: Optional[float], baseline: Optional[int], score: float) -> None:
        self.execute(
            "INSERT OR REPLACE INTO outcomes (proposal_id, day, measured, impressions, clicks, position, "
            "baseline_impressions, score) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, day, at.isoformat(), impressions, clicks, position, baseline, score),
        )

    def has_outcome(self, pid: int, day: int) -> bool:
        return self.one("SELECT 1 FROM outcomes WHERE proposal_id = ? AND day = ?", (pid, day)) is not None

    def outcomes(self) -> List[Dict[str, Any]]:
        return self.query(
            "SELECT o.*, p.kind, p.slug, p.title, p.category, p.intent, p.predicted, p.target_keyword "
            "FROM outcomes o JOIN proposals p ON p.id = o.proposal_id ORDER BY o.proposal_id, o.day"
        )

    def add_insight(self, at: datetime, memo: List[str]) -> None:
        self.execute("INSERT INTO insights (created, memo) VALUES (?, ?)", (at.isoformat(), json.dumps(memo, ensure_ascii=False)))

    def latest_insight(self) -> List[str]:
        row = self.one("SELECT memo FROM insights ORDER BY id DESC LIMIT 1")
        return json.loads(row["memo"]) if row else []

    # --- uso de la IA -------------------------------------------------------

    def log_llm(self, at: datetime, task: str, ok: bool, duration_ms: int,
                cost_usd: Optional[float], error: Optional[str]) -> None:
        self.execute(
            "INSERT INTO llm_calls (at, task, ok, duration_ms, cost_usd, error) VALUES (?, ?, ?, ?, ?, ?)",
            (at.isoformat(), task, int(ok), duration_ms, cost_usd, error),
        )

    def llm_calls_on(self, day_iso: str) -> int:
        row = self.one("SELECT COUNT(*) AS n FROM llm_calls WHERE substr(at, 1, 10) = ?", (day_iso,))
        return int(row["n"]) if row else 0

    # --- trabajos programados ----------------------------------------------

    def job(self, name: str) -> Optional[Dict[str, Any]]:
        return self.one("SELECT * FROM jobs WHERE name = ?", (name,))

    def set_job(self, name: str, last_run: datetime, ok: Optional[bool] = None, error: Optional[str] = None) -> None:
        self.execute(
            "INSERT INTO jobs (name, last_run, ok, error) VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
            "last_run = excluded.last_run, ok = excluded.ok, error = excluded.error",
            (name, last_run.isoformat(), None if ok is None else int(ok), error),
        )
