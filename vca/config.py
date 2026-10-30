"""Configuración desde variables de entorno (en el VPS, /etc/vertice-content-agent/env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional


class ConfigError(Exception):
    pass


def _get(env: Mapping[str, str], name: str, default: Optional[str] = None) -> Optional[str]:
    value = (env.get(name) or "").strip()
    return value if value else default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} debe ser un número entero: {raw!r}") from None


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        raise ConfigError(f"{name} debe ser un número: {raw!r}") from None


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = _get(env, name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "si", "sí", "on")


# Variables obligatorias por funcionalidad. `check` y `run` las comprueban.
REQUIRED = {
    "telegram": ("VCA_TELEGRAM_TOKEN", "VCA_TELEGRAM_CHAT_ID"),
    "gsc": ("VCA_GSC_CLIENT_ID", "VCA_GSC_CLIENT_SECRET", "VCA_GSC_REFRESH_TOKEN"),
    "github": ("VCA_GITHUB_TOKEN",),
}


@dataclass
class Config:
    state_dir: Path
    timezone: str
    dry_run: bool
    telegram_token: str
    telegram_chat_id: str
    gsc_client_id: str
    gsc_client_secret: str
    gsc_refresh_token: str
    gsc_site: str
    site_url: str
    github_token: str
    github_repo: str
    github_base: str
    git_author_name: str
    git_author_email: str
    php_bin: str
    git_bin: str
    claude_bin: str
    model_plan: str
    model_draft: str
    llm_max_calls_per_day: int
    llm_timeout: int
    plan_schedule: str
    learn_schedule: str
    report_schedule: str
    proposals_per_week: int
    seeds: List[str]
    suggest_delay: float
    suggest_max_requests: int
    suggest_lang: str
    suggest_country: str
    ci_timeout_minutes: int
    min_words: int
    env: Mapping[str, str]

    @property
    def db_path(self) -> Path:
        return self.state_dir / "vca.sqlite3"

    @property
    def site_dir(self) -> Path:
        return self.state_dir / "site"

    @property
    def sandbox_dir(self) -> Path:
        return self.state_dir / "llm-sandbox"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        env = dict(os.environ if env is None else env)
        seeds = [s.strip() for s in (_get(env, "VCA_SEEDS") or "").split(",") if s.strip()]
        return cls(
            state_dir=Path(_get(env, "VCA_STATE_DIR", "/var/lib/vertice-content-agent")),
            timezone=_get(env, "VCA_TIMEZONE", "Europe/Madrid"),
            dry_run=_bool(env, "VCA_DRY_RUN"),
            telegram_token=_get(env, "VCA_TELEGRAM_TOKEN", ""),
            telegram_chat_id=_get(env, "VCA_TELEGRAM_CHAT_ID", ""),
            gsc_client_id=_get(env, "VCA_GSC_CLIENT_ID", ""),
            gsc_client_secret=_get(env, "VCA_GSC_CLIENT_SECRET", ""),
            gsc_refresh_token=_get(env, "VCA_GSC_REFRESH_TOKEN", ""),
            gsc_site=_get(env, "VCA_GSC_SITE", "sc-domain:verticedev.es"),
            site_url=_get(env, "VCA_SITE_URL", "https://verticedev.es").rstrip("/"),
            github_token=_get(env, "VCA_GITHUB_TOKEN", ""),
            github_repo=_get(env, "VCA_GITHUB_REPO", "miguelmartt/verticedev"),
            github_base=_get(env, "VCA_GITHUB_BASE", "main"),
            git_author_name=_get(env, "VCA_GIT_AUTHOR_NAME", "VérticeDev Content Agent"),
            git_author_email=_get(env, "VCA_GIT_AUTHOR_EMAIL", "agente@verticedev.es"),
            php_bin=_get(env, "VCA_PHP_BIN", "php"),
            git_bin=_get(env, "VCA_GIT_BIN", "git"),
            claude_bin=_get(env, "VCA_CLAUDE_BIN", "claude"),
            model_plan=_get(env, "VCA_MODEL_PLAN", "sonnet"),
            model_draft=_get(env, "VCA_MODEL_DRAFT", "sonnet"),
            llm_max_calls_per_day=_int(env, "VCA_LLM_MAX_CALLS_PER_DAY", 20),
            llm_timeout=_int(env, "VCA_LLM_TIMEOUT", 900),
            plan_schedule=_get(env, "VCA_PLAN_SCHEDULE", "mon 08:00"),
            learn_schedule=_get(env, "VCA_LEARN_SCHEDULE", "07:30"),
            report_schedule=_get(env, "VCA_REPORT_SCHEDULE", "1 09:00"),
            proposals_per_week=_int(env, "VCA_PROPOSALS_PER_WEEK", 3),
            seeds=seeds,
            suggest_delay=_float(env, "VCA_SUGGEST_DELAY", 1.0),
            suggest_max_requests=_int(env, "VCA_SUGGEST_MAX_REQUESTS", 300),
            suggest_lang=_get(env, "VCA_SUGGEST_LANG", "es"),
            suggest_country=_get(env, "VCA_SUGGEST_COUNTRY", "es"),
            ci_timeout_minutes=_int(env, "VCA_CI_TIMEOUT_MINUTES", 30),
            min_words=_int(env, "VCA_MIN_WORDS", 900),
            env=env,
        )

    def missing(self, *features: str) -> List[str]:
        """Variables obligatorias sin valor para las funcionalidades pedidas."""
        return [
            name
            for feature in features
            for name in REQUIRED[feature]
            if not _get(self.env, name)
        ]
