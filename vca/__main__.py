"""Línea de órdenes: `python -m vca <orden>`."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import subprocess
import sys
from datetime import timedelta
from typing import List, Optional

from . import __version__
from .config import Config, ConfigError
from .gsc import authorize, load_client_secrets


def setup_logging() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def cmd_run(cfg: Config) -> int:
    from .app import App

    missing = cfg.missing("telegram", "gsc") + ([] if cfg.dry_run else cfg.missing("github"))
    if missing:
        print("Faltan variables: " + ", ".join(missing), file=sys.stderr)
        return 2
    app = App(cfg)

    def stop(*_: object) -> None:
        app.stop.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    app.run_forever()
    return 0


def cmd_check(cfg: Config) -> int:
    """Comprueba configuración y conexiones sin cambiar nada (salvo clonar la web en el estado)."""
    from .app import App

    ok = True

    def line(passed: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"{'✔' if passed else '✘'} {label}{': ' + detail if detail else ''}")

    for feature in ("telegram", "gsc", "github"):
        missing = cfg.missing(feature)
        line(not missing, f"Variables de {feature}", ", ".join(missing) if missing else "completas")
    for label, binary, args in (("git", cfg.git_bin, ["--version"]), ("PHP", cfg.php_bin, ["--version"]),
                                ("Claude Code", cfg.claude_bin, ["--version"])):
        path = shutil.which(binary)
        if not path:
            line(False, label, f"no encuentro {binary}")
            continue
        out = subprocess.run([path, *args], capture_output=True, text=True, timeout=60)
        line(out.returncode == 0, label, (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else "")
    line(bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")), "CLAUDE_CODE_OAUTH_TOKEN",
         "definido" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else "falta (sale de `claude setup-token`)")

    app = App(cfg)
    if cfg.telegram_token:
        try:
            me = app.tg.me()
            line(True, "Telegram", f"@{me.get('username')}")
        except Exception as exc:
            line(False, "Telegram", str(exc)[:200])
    if app.gsc:
        try:
            end = app.today() - timedelta(days=2)
            rows = app.gsc.query(end - timedelta(days=6), end, ["query"], row_limit=5)
            line(True, "Search Console", f"{cfg.gsc_site}, {len(rows)} consultas de muestra")
        except Exception as exc:
            line(False, "Search Console", str(exc)[:200])
    if cfg.github_token:
        try:
            info = app.github.repo_info()
            line(True, "GitHub", f"{info.get('full_name')} (rama por defecto {info.get('default_branch')})")
        except Exception as exc:
            line(False, "GitHub", str(exc)[:200])
    try:
        app.site.sync()
        site = app.site.load()
        line(True, "Web", f"{len(site.posts)} artículos, {len(site.services)} servicios, {len(site.projects)} proyectos")
    except Exception as exc:
        line(False, "Web", str(exc)[:300])
    print(f"Claude: {app.llm.calls_left_today()} llamadas disponibles hoy (tope {cfg.llm_max_calls_per_day})")
    return 0 if ok else 1


def cmd_simple(cfg: Config, action: str) -> int:
    from .app import App

    app = App(cfg)
    if action == "plan":
        app.plan_week()
    elif action == "learn":
        app.learn_daily()
    elif action == "report":
        app.monthly()
    elif action == "status":
        print(app.status_text())
        print()
        print(app.learning_text())
    return 0


def cmd_research(cfg: Config, limit: int) -> int:
    """Investiga y muestra las mejores candidatas en consola, sin llamar a Claude ni a Telegram."""
    from . import learning, research
    from .app import App
    from .suggest import Suggest

    app = App(cfg)
    app.site.sync()
    site = app.site.load()
    end = app.today() - timedelta(days=2)
    rows = app.require_gsc().query(end - timedelta(days=89), end, ["query", "page"], row_limit=5000)
    suggest = Suggest(cfg.suggest_lang, cfg.suggest_country, cfg.suggest_delay, cfg.suggest_max_requests)
    suggestions = suggest.expand(research.seeds_from(site, research.aggregate_gsc(rows), cfg.seeds))
    cands = research.build_candidates(rows, suggestions, site, cfg.site_url,
                                      learning.merged_weights(app.db.weights()), app.db.prefs())
    print(f"{len(rows)} filas de Search Console, {suggest.requests} consultas de autocompletado, {len(cands)} candidatas\n")
    for c in cands[:limit]:
        pos = f"{c.position:.1f}" if c.position else "-"
        print(f"{c.score:5.2f}  {c.keyword[:60]:60}  imp {c.impressions:5}  pos {pos:>5}  auto {c.suggest_hits:2}  "
              f"{c.intent:13} {('cubre ' + c.covered_by) if c.covered_by else ''}")
    return 0


def cmd_gsc_auth(client_secrets: str, no_browser: bool) -> int:
    creds = load_client_secrets(client_secrets)
    token = authorize(creds["client_id"], creds["client_secret"], open_browser=not no_browser)
    print("\nAñade estas líneas a /etc/vertice-content-agent/env en el VPS:\n")
    print(f"VCA_GSC_CLIENT_ID={creds['client_id']}")
    print(f"VCA_GSC_CLIENT_SECRET={creds['client_secret']}")
    print(f"VCA_GSC_REFRESH_TOKEN={token}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vca", description="Agente de contenido de VérticeDev")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="servicio: bot de Telegram, programador y trabajador")
    sub.add_parser("check", help="comprueba configuración y conexiones")
    sub.add_parser("plan", help="investiga y manda el plan por Telegram ahora")
    research = sub.add_parser("research", help="investiga y muestra las candidatas en consola")
    research.add_argument("--limit", type=int, default=40)
    sub.add_parser("learn", help="mediciones y aprendizaje ahora")
    sub.add_parser("report", help="informe de servicios y memoria de aprendizajes ahora")
    sub.add_parser("status", help="estado en consola")
    auth = sub.add_parser("gsc-auth", help="obtiene el refresh token de Search Console (en tu ordenador)")
    auth.add_argument("--client-secrets", required=True, help="JSON del cliente OAuth «Aplicación de escritorio»")
    auth.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    setup_logging()
    if args.command == "gsc-auth":
        return cmd_gsc_auth(args.client_secrets, args.no_browser)
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.command == "run":
        return cmd_run(cfg)
    if args.command == "check":
        return cmd_check(cfg)
    if args.command == "research":
        return cmd_research(cfg, args.limit)
    return cmd_simple(cfg, args.command)


if __name__ == "__main__":
    sys.exit(main())
