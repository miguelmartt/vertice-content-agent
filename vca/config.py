"""Configuración desde variables de entorno (en el VPS, /etc/vertice-content-agent/env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional


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
    "gsc": ("VCA_GSC_CLIENT_ID", "VCA_GSC_CLIENT_SECRET", "VCA_GSC_REFRESH_TOKEN", "VCA_GSC_SITE", "VCA_SITE_URL"),
    "github": ("VCA_GITHUB_TOKEN", "VCA_GITHUB_REPO"),
    "profile": ("VCA_BRAND", "VCA_OWNER", "VCA_AUTHOR"),
}


def load_env_file(path: str, environ: Optional[Dict[str, str]] = None) -> None:
    """Carga un archivo KEY=valor como datos, igual que EnvironmentFile= de systemd.

    No lo interpreta un shell: un valor con $, comillas invertidas o $(…) se queda tal cual.
    """
    target = os.environ if environ is None else environ
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if not key.replace("_", "").isalnum() or key[0].isdigit():
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            target[key] = value


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
    deploy_command: str
    brand: str
    owner: str
    author: str
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
            gsc_site=_get(env, "VCA_GSC_SITE", ""),
            site_url=_get(env, "VCA_SITE_URL", "").rstrip("/"),
            github_token=_get(env, "VCA_GITHUB_TOKEN", ""),
            github_repo=_get(env, "VCA_GITHUB_REPO", ""),
            github_base=_get(env, "VCA_GITHUB_BASE", "main"),
            git_author_name=_get(env, "VCA_GIT_AUTHOR_NAME", "Content Agent"),
            git_author_email=_get(env, "VCA_GIT_AUTHOR_EMAIL", "content-agent@localhost"),
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
            deploy_command=_get(env, "VCA_DEPLOY_COMMAND", ""),
            brand=_get(env, "VCA_BRAND", ""),
            owner=_get(env, "VCA_OWNER", ""),
            author=_get(env, "VCA_AUTHOR", ""),
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
