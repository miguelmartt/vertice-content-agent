"""El agente: investigación, plan, redacción, publicación, medición e informes."""

from __future__ import annotations

import html
import json
import logging
import queue
import threading
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import http, learning, render, research, schemas
from .config import Config
from .db import DB, OPEN_STATUSES
from .github import GitHub, failed_summary
from .gsc import SearchConsole
from .llm import LLM
from .site import SiteData, SiteRepo
from .suggest import Suggest
from .telegram import Telegram, esc
from .util import fmt_num, normalize, slugify, now as tz_now

log = logging.getLogger("vca")

KIND_LABEL = {"new": "Artículo nuevo", "improve": "Mejora de un artículo"}
INTENT_LABEL = {
    "informational": "informativa",
    "commercial": "comercial",
    "comparison": "comparación",
    "local": "local",
}
PR_TRAILER = "\n\nGenerado por vertice-content-agent y aprobado por Miguel en Telegram."


def proposal_buttons(pid: int) -> List[List[Tuple[str, str]]]:
    return [[("✍️ Redactar", f"draft:{pid}"), ("💬 Ajustar", f"adjust:{pid}")], [("🗑 Descartar", f"reject:{pid}")]]


def review_buttons(pid: int) -> List[List[Tuple[str, str]]]:
    return [[("🚀 Publicar", f"publish:{pid}"), ("✏️ Pedir cambios", f"changes:{pid}")], [("🗑 Descartar", f"discard:{pid}")]]


def retry_buttons(pid: int) -> List[List[Tuple[str, str]]]:
    return [[("🔁 Reintentar", f"retry:{pid}")], [("🗑 Descartar", f"discard:{pid}")]]


def metric_line(kw: Optional[Dict[str, Any]]) -> str:
    if not kw:
        return "sin datos de búsqueda"
    parts = []
    if kw.get("impressions"):
        pos = f" · pos. {fmt_num(kw['position'], 1)}" if kw.get("position") else ""
        parts.append(f"GSC 90 días: {fmt_num(kw['impressions'])} impr. · {fmt_num(kw.get('clicks') or 0)} clics{pos}")
    if kw.get("suggest_hits"):
        parts.append(f"autocompletado: {kw['suggest_hits']} veces")
    return " · ".join(parts) or "sin datos de búsqueda"


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = DB(cfg.db_path)
        self.db.init()
        self.tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id, dry_run=not cfg.telegram_token)
        self.gsc: Optional[SearchConsole] = None
        if not cfg.missing("gsc"):
            self.gsc = SearchConsole(cfg.gsc_client_id, cfg.gsc_client_secret, cfg.gsc_refresh_token, cfg.gsc_site)
        self.github = GitHub(cfg.github_token, cfg.github_repo)
        self.site = SiteRepo(
            cfg.site_dir, cfg.github_repo, cfg.github_token, cfg.github_base, cfg.git_bin, cfg.php_bin,
            cfg.git_author_name, cfg.git_author_email,
        )
        self.llm = LLM(cfg.claude_bin, cfg.sandbox_dir, self.db, cfg.timezone,
                       cfg.llm_max_calls_per_day, cfg.llm_timeout)
        self.tasks: "queue.Queue[Tuple[str, Callable[..., Any], tuple]]" = queue.Queue()
        self.stop = threading.Event()
        self.busy: Optional[str] = None

    # --- utilidades ---------------------------------------------------------

    def now(self) -> datetime:
        return tz_now(self.cfg.timezone)

    def today(self) -> date:
        return self.now().date()

    def page_url(self, slug: str) -> str:
        return f"{self.cfg.site_url}/blog/{slug}"

    def enqueue(self, name: str, fn: Callable[..., Any], *args: Any) -> None:
        log.info("En cola: %s", name)
        self.tasks.put((name, fn, args))

    def run_task(self, name: str, fn: Callable[..., Any], *args: Any) -> None:
        self.busy = name
        try:
            fn(*args)
        except Exception as exc:  # el servicio no se cae por un trabajo
            log.exception("Error en %s", name)
            self.db.log_event(self.now(), "error", {"task": name, "error": str(exc)[:500]})
            self.tg.send(f"⚠️ Error en <b>{esc(name)}</b>: {esc(http.redact(str(exc))[:800])}")
        finally:
            self.busy = None

    def worker_loop(self) -> None:
        while not self.stop.is_set():
            try:
                name, fn, args = self.tasks.get(timeout=1)
            except queue.Empty:
                continue
            self.run_task(name, fn, *args)

    def require_gsc(self) -> SearchConsole:
        if not self.gsc:
            raise RuntimeError("Search Console no está configurado (VCA_GSC_*)")
        return self.gsc

    def fail(self, pid: int, error: str, buttons: bool = True) -> None:
        p = self.db.proposal(pid) or {}
        self.db.update_proposal(pid, self.now(), status="failed", last_error=error[:1000])
        self.tg.send(f"❌ «{esc(p.get('title', pid))}»: {esc(error[:1500])}", retry_buttons(pid) if buttons else None)

    # --- plan semanal -------------------------------------------------------

    def plan_week(self) -> None:
        cfg = self.cfg
        self.tg.send("🔎 Empiezo la investigación: Search Console y autocompletado de Google. Tardo unos minutos.")
        self.site.sync()
        site = self.site.load()
        self.expire_old()

        end = self.today() - timedelta(days=2)
        start = end - timedelta(days=89)
        rows = self.require_gsc().query(start, end, ["query", "page"], row_limit=5000)
        snapshot = end.isoformat()
        self.db.save_gsc_snapshot(snapshot, rows)

        suggest = Suggest(cfg.suggest_lang, cfg.suggest_country, cfg.suggest_delay, cfg.suggest_max_requests)
        seeds = research.seeds_from(site, research.aggregate_gsc(rows), cfg.seeds)
        suggestions = suggest.expand(seeds)

        weights = learning.merged_weights(self.db.weights())
        prefs = self.db.prefs()
        cands = research.build_candidates(rows, suggestions, site, cfg.site_url, weights, prefs)
        self.db.save_keywords(snapshot, [c.as_row() for c in cands[:500]])

        context = self.plan_context(site, cands[:40], rows, start, end)
        result = self.llm.run("plan", schemas.load_prompt("plan"), json.dumps(context, ensure_ascii=False, indent=1),
                              schemas.plan(site.categories), cfg.model_plan)
        stored = self.accept_proposals(result.get("proposals") or [], site, weights, prefs)

        memo = self.db.latest_insight()
        lines = [
            f"📋 <b>Plan de la semana</b> ({self.today().strftime('%d-%m-%Y')})",
            f"Search Console: {fmt_num(len(rows))} filas del {start.strftime('%d-%m')} al {end.strftime('%d-%m')}.",
            f"Autocompletado: {suggest.requests} consultas, {fmt_num(len(suggestions))} sugerencias"
            + (" (Google me ha frenado; sigo la semana que viene)." if suggest.blocked else "."),
            f"Candidatas puntuadas: {fmt_num(len(cands))}. Propuestas: {len(stored)}.",
        ]
        if memo:
            lines.append(f"🧠 Lo que tengo en cuenta: {esc(memo[0])}")
        notes = (result.get("service_notes") or "").strip()
        if notes:
            lines.append(f"💼 Servicios: {esc(notes)}")
        if not stored:
            lines.append("Esta semana no tengo ninguna propuesta que merezca la pena.")
        self.tg.send("\n".join(lines))
        for p in stored:
            self.send_proposal(p)
        self.db.log_event(self.now(), "plan", {"proposals": [p["id"] for p in stored], "candidates": len(cands)})

    def expire_old(self) -> None:
        limit = (self.now() - timedelta(days=7)).isoformat()
        for p in self.db.proposals(["proposed"]):
            if p["created"] < limit:
                self.db.update_proposal(p["id"], self.now(), status="expired")

    def plan_context(self, site: SiteData, cands: List[research.Candidate], rows: List[Dict[str, Any]],
                     start: date, end: date) -> Dict[str, Any]:
        pages: Dict[str, Dict[str, float]] = {}
        for r in rows:
            page = pages.setdefault(r["page"], {"impresiones": 0, "clics": 0, "pos_x_imp": 0.0})
            page["impresiones"] += r["impressions"]
            page["clics"] += r["clicks"]
            page["pos_x_imp"] += r["position"] * r["impressions"]
        articles = []
        for post in site.posts:
            m = pages.get(self.page_url(post["slug"]), {})
            imp = int(m.get("impresiones", 0))
            articles.append({
                "slug": post["slug"], "titulo": post["title"], "categoria": post["category"],
                "fecha": post.get("modified") or post["date"], "resumen": post.get("excerpt", ""),
                "impresiones_90d": imp, "clics_90d": int(m.get("clics", 0)),
                "posicion_media": round(m["pos_x_imp"] / imp, 1) if imp else None,
            })
        open_titles = [p["title"] for p in self.db.proposals(OPEN_STATUSES)]
        return {
            "fecha": self.today().isoformat(),
            "ventana_search_console": f"{start.isoformat()} a {end.isoformat()}",
            "num_propuestas": self.cfg.proposals_per_week,
            "categorias": site.categories,
            "articulos": articles,
            "servicios": [
                {"slug": s["slug"], "nombre": s["name"], "grupo": s["group"], "seo_title": s.get("seo", {}).get("title", "")}
                for s in site.services
            ],
            "proyectos_portfolio": [
                {"slug": p["slug"], "nombre": p["name"], "resumen": p.get("tagline", ""), "stack": p.get("stack_card", [])}
                for p in site.projects
            ],
            "candidatas": [
                {
                    "keyword": c.keyword, "impresiones": c.impressions, "clics": c.clicks, "posicion": c.position,
                    "pagina": c.page, "autocompletado_veces": c.suggest_hits, "autocompletado_puesto": c.suggest_rank,
                    "intencion": c.intent, "cubierta_por": c.covered_by, "servicio": c.service, "puntuacion": c.score,
                }
                for c in cands
            ],
            "memoria_aprendizajes": self.db.latest_insight(),
            "decisiones_recientes": [
                {"titulo": d["title"], "tipo": d["kind"], "categoria": d["category"], "estado": d["status"],
                 "motivo": d.get("rejection") or "", "comentarios": d.get("feedback") or []}
                for d in self.db.recent_decisions()
            ],
            "propuestas_abiertas": open_titles,
        }

    def accept_proposals(self, raw: List[Dict[str, Any]], site: SiteData, weights: Dict[str, float],
                         prefs: Dict[Tuple[str, str], Tuple[int, int]]) -> List[Dict[str, Any]]:
        taken = {p["slug"] for p in self.db.proposals(OPEN_STATUSES)}
        stored = []
        for item in raw:
            if len(stored) >= self.cfg.proposals_per_week:
                break
            kind = item.get("kind")
            if kind == "improve":
                slug = item.get("existing_slug") or item.get("slug") or ""
                if slug not in site.post_slugs:
                    continue
            else:
                kind = "new"
                slug = slugify(item.get("slug") or item.get("title") or "")
                if not slug or slug in site.post_slugs:
                    continue
            if slug in taken:
                continue
            category = item.get("category")
            if category not in site.categories:
                category = (site.post(slug) or {}).get("category") or site.categories[-1]
            kw = self.db.keyword(item.get("target_keyword", "")) or self.find_keyword(item.get("target_keyword", ""))
            cand = research.Candidate(keyword=item.get("target_keyword", ""))
            cand.intent = item.get("intent") or research.classify_intent(cand.keyword)
            features = (kw or {}).get("features") or research.features(cand)
            values = {"category": category, "intent": cand.intent, "kind": kind}
            pid = self.db.add_proposal(self.now(), {
                "kind": kind,
                "slug": slug,
                "title": item.get("title", "").strip(),
                "category": category,
                "target_keyword": item.get("target_keyword", ""),
                "keywords": item.get("secondary_keywords") or [],
                "intent": cand.intent,
                "angle": item.get("angle", ""),
                "outline": item.get("outline") or [],
                "rationale": item.get("rationale", ""),
                "service": item.get("service", ""),
                "features": features,
                "predicted": learning.score(weights, prefs, features, values),
            })
            taken.add(slug)
            stored.append(self.db.proposal(pid))
        return stored

    def find_keyword(self, keyword: str) -> Optional[Dict[str, Any]]:
        target = normalize(keyword)
        for row in self.db.query("SELECT keyword FROM keywords"):
            if normalize(row["keyword"]) == target:
                return self.db.keyword(row["keyword"])
        return None

    def send_proposal(self, p: Dict[str, Any]) -> None:
        kw = self.db.keyword(p["target_keyword"]) or self.find_keyword(p["target_keyword"])
        outline = "; ".join(p.get("outline") or [])
        lines = [
            f"<b>{KIND_LABEL[p['kind']]}</b> · {esc(p['category'])}",
            f"<b>{esc(p['title'])}</b>",
            f"🎯 <i>{esc(p['target_keyword'])}</i> · intención {INTENT_LABEL.get(p['intent'], p['intent'])}",
            f"📊 {esc(metric_line(kw))}",
            f"💡 {esc(p['angle'])}",
            f"🧩 {esc(outline)}",
            f"Por qué: {esc(p['rationale'])}",
        ]
        if p["kind"] == "improve":
            lines.insert(2, f"📄 {self.page_url(p['slug'])}")
        if p.get("service"):
            lines.append(f"🔗 Servicio: {esc(p['service'])}")
        lines.append(f"Puntuación: {fmt_num(p['predicted'] or 0, 2)}")
        self.tg.send("\n".join(lines), proposal_buttons(p["id"]))

    # --- redacción ----------------------------------------------------------

    def draft(self, pid: int) -> None:
        p = self.db.proposal(pid)
        if not p or p["status"] not in ("approved", "review", "failed", "drafting"):
            return
        self.db.update_proposal(pid, self.now(), status="drafting", last_action="draft", last_error=None)
        self.tg.send(f"✍️ Redactando «{esc(p['title'])}». Suele tardar unos minutos.")
        self.site.sync()
        site = self.site.load()
        prompt = json.dumps(self.draft_context(p, site), ensure_ascii=False, indent=1)
        schema = schemas.draft(site.categories)
        system = schemas.load_prompt("draft")
        draft = self.llm.run("draft", system, prompt, schema, self.cfg.model_draft)
        errors = self.validate(draft, p, site)
        if errors:
            fix = json.dumps({
                "instruccion": "Corrige estos errores y devuelve el borrador completo, sin perder contenido.",
                "errores": errors,
                "borrador": draft,
                "rutas_internas": sorted(site.paths),
            }, ensure_ascii=False, indent=1)
            draft = self.llm.run("draft-fix", system, fix, schema, self.cfg.model_draft)
            errors = self.validate(draft, p, site)
        if errors:
            self.fail(pid, "El borrador no pasa la validación:\n• " + "\n• ".join(errors))
            return
        draft = render.finalize(draft)
        self.db.update_proposal(pid, self.now(), draft=draft)
        sha, number, url = self.push(p, draft, site)
        self.db.update_proposal(pid, self.now(), status="review", head_sha=sha, pr_number=number, pr_url=url,
                                branch=self.branch(p))
        self.send_review(self.db.proposal(pid), site)

    def draft_context(self, p: Dict[str, Any], site: SiteData) -> Dict[str, Any]:
        titles = {f"/blog/{x['slug']}": x["title"] for x in site.posts}
        titles.update({f"/servicios/{s['slug']}": s["name"] for s in site.services})
        titles.update({f"/portfolio/{x['slug']}": x["name"] for x in site.projects})
        context: Dict[str, Any] = {
            "tipo": p["kind"],
            "slug": p["slug"],
            "titulo_propuesto": p["title"],
            "categoria": p["category"],
            "palabra_clave": p["target_keyword"],
            "palabras_secundarias": p.get("keywords") or [],
            "intencion": p["intent"],
            "angulo": p["angle"],
            "estructura": p.get("outline") or [],
            "por_que": p["rationale"],
            "servicio_relacionado": p.get("service") or "",
            "comentarios_de_miguel": p.get("feedback") or [],
            "rutas_internas": [{"ruta": path, "titulo": titles.get(path, "")} for path in sorted(site.paths)],
            "servicios": [{"slug": s["slug"], "nombre": s["name"], "lead": s.get("lead", "")} for s in site.services],
            "proyectos_portfolio": [
                {"slug": x["slug"], "nombre": x["name"], "resumen": x.get("summary_card", ""),
                 "stack": x.get("stack_card", []), "tipo": x.get("kind", "")}
                for x in site.projects
            ],
            "categorias": site.categories,
        }
        if p["kind"] == "improve":
            post = site.post(p["slug"]) or {}
            context["articulo_actual"] = {
                "titulo": post.get("title", ""),
                "resumen": post.get("excerpt", ""),
                "fecha": post.get("date", ""),
                **self.site.read_article(p["slug"]),
            }
            if self.gsc:
                end = self.today() - timedelta(days=2)
                context["consultas_de_la_pagina_90d"] = self.gsc.page_queries(
                    self.page_url(p["slug"]), end - timedelta(days=89), end)
        if p.get("draft") and p.get("feedback"):
            context["borrador_anterior"] = p["draft"]
        return context

    def validate(self, draft: Dict[str, Any], p: Dict[str, Any], site: SiteData) -> List[str]:
        return render.validate(
            draft, kind=p["kind"], slug=p["slug"], existing_slugs=site.post_slugs,
            categories=site.categories, site_paths=site.paths, min_words=self.cfg.min_words,
        )

    def branch(self, p: Dict[str, Any]) -> str:
        return f"contenido/{p['slug']}"

    def registry_entry(self, p: Dict[str, Any], draft: Dict[str, Any], site: SiteData) -> Dict[str, Any]:
        today = self.today().isoformat()
        entry = {
            "slug": p["slug"],
            "title": draft["headline"],
            "excerpt": draft["excerpt"],
            "category": draft["category"],
            "date": today,
            "reading": draft["reading"],
        }
        if p["kind"] == "improve":
            post = site.post(p["slug"]) or {}
            entry["date"] = post.get("date", today)
            entry["modified"] = today
        return entry

    def push(self, p: Dict[str, Any], draft: Dict[str, Any], site: SiteData) -> Tuple[str, Optional[int], Optional[str]]:
        """Escribe el artículo sobre origin/main, sube la rama y abre o actualiza el PR."""
        view = render.render_view(draft, site.paths)
        paths = self.site.write_article(p["slug"], view, self.registry_entry(p, draft, site), p["kind"])
        self.site.lint(paths)
        verb = "nuevo artículo" if p["kind"] == "new" else "mejora de"
        title = f"blog: {verb} «{draft['headline']}»" if p["kind"] == "new" else f"blog: {verb} {p['slug']}"
        sha = self.site.commit_branch(self.branch(p), paths, title + PR_TRAILER, push=not self.cfg.dry_run)
        if self.cfg.dry_run:
            return sha, None, None
        number, url = self.github.open_pr(self.branch(p), self.cfg.github_base, title, self.pr_body(p, draft))
        return sha, number, url

    def pr_body(self, p: Dict[str, Any], draft: Dict[str, Any]) -> str:
        lines = [
            f"**{KIND_LABEL[p['kind']]}** propuesto por vertice-content-agent.",
            "",
            f"- Palabra clave: {p['target_keyword']} (intención {INTENT_LABEL.get(p['intent'], p['intent'])})",
            f"- Por qué: {p['rationale']}",
            f"- Title: {draft['seoTitle']} ({len(draft['seoTitle'])})",
            f"- Description: {draft['description']} ({len(draft['description'])})",
            f"- Lectura: {draft['reading']}",
        ]
        if draft.get("needs_input"):
            lines += ["", "**Pendiente de confirmar por Miguel:**"] + [f"- {q}" for q in draft["needs_input"]]
        lines += ["", "Se fusiona desde Telegram cuando Miguel lo aprueba y CI está en verde."]
        return "\n".join(lines)

    def send_review(self, p: Dict[str, Any], site: SiteData) -> None:
        d = p["draft"]
        words = render.draft_words(d)
        lines = [
            f"📝 <b>Borrador listo</b> · {KIND_LABEL[p['kind']]}",
            f"<b>{esc(d['headline'])}</b>",
            f"Title ({len(d['seoTitle'])}): {esc(d['seoTitle'])}",
            f"Description ({len(d['description'])}): {esc(d['description'])}",
            f"{fmt_num(words)} palabras · {esc(d['reading'])} · {esc(d['category'])}",
            f"🎯 {esc(p['target_keyword'])}",
        ]
        if p.get("pr_url"):
            lines.append(f'PR: <a href="{esc(p["pr_url"])}">#{p["pr_number"]}</a> (CI en marcha)')
        elif self.cfg.dry_run:
            lines.append("Modo de prueba: no he subido la rama ni abierto PR.")
        if d.get("needs_input"):
            lines.append("⚠️ <b>Necesito que me confirmes:</b>")
            lines += [f"• {esc(q)}" for q in d["needs_input"]]
            lines.append("Respóndeme con «✏️ Pedir cambios».")
        self.tg.send("\n".join(lines), review_buttons(p["id"]))
        preview = render.render_preview(d, site.paths).encode("utf-8")
        self.tg.send_document(f"{p['slug']}.html", preview, "Borrador para leer en el móvil")

    # --- publicación --------------------------------------------------------

    def publish(self, pid: int) -> None:
        """Rehace la rama sobre el main actual con la fecha de hoy y espera a CI para fusionar."""
        p = self.db.proposal(pid)
        if not p or p["status"] not in ("review", "failed") or not p.get("draft"):
            return
        self.db.update_proposal(pid, self.now(), last_action="publish", last_error=None)
        self.site.sync()
        site = self.site.load()
        if p["kind"] == "new" and p["slug"] in site.post_slugs:
            self.fail(pid, f"Ya hay un artículo con el slug {p['slug']} en main")
            return
        errors = self.validate(p["draft"], p, site)
        if errors:
            self.fail(pid, "Con el main actual el borrador ya no valida:\n• " + "\n• ".join(errors))
            return
        sha, number, url = self.push(p, p["draft"], site)
        if self.cfg.dry_run:
            self.db.update_proposal(pid, self.now(), status="merged", merged_at=self.now().isoformat(), head_sha=sha)
            self.tg.send(f"🧪 Modo de prueba: «{esc(p['title'])}» habría quedado fusionado.")
            return
        self.db.update_proposal(pid, self.now(), status="publishing", head_sha=sha, pr_number=number, pr_url=url,
                                publishing_since=self.now().isoformat())
        self.tg.send(f'⏳ Rama actualizada. Espero a que CI pase para fusionar: <a href="{esc(url)}">PR #{number}</a>')

    def ci_watch(self) -> None:
        for p in self.db.proposals(["publishing"]):
            try:
                checks = self.github.checks(p["head_sha"])
            except http.HttpError as exc:
                log.warning("No puedo leer CI del PR %s: %s", p["pr_number"], exc)
                continue
            since = datetime.fromisoformat(p["publishing_since"])
            if checks["ok"]:
                self.merge(p)
            elif checks["failed"]:
                self.db.update_proposal(p["id"], self.now(), status="review")
                self.tg.send(
                    f"❌ CI ha fallado en «{esc(p['title'])}» ({esc(failed_summary(checks['failed']))}). "
                    f'<a href="{esc(p["pr_url"])}">Ver PR</a>',
                    review_buttons(p["id"]),
                )
            elif self.now() - since > timedelta(minutes=self.cfg.ci_timeout_minutes):
                self.db.update_proposal(p["id"], self.now(), status="review")
                self.tg.send(
                    f"⌛ CI no ha terminado en {self.cfg.ci_timeout_minutes} min para «{esc(p['title'])}». "
                    f'<a href="{esc(p["pr_url"])}">Ver PR</a>. Pulsa publicar de nuevo cuando quieras.',
                    review_buttons(p["id"]),
                )

    def merge(self, p: Dict[str, Any]) -> None:
        title = f"Merge {self.branch(p)}: {p['draft']['headline']}"
        try:
            self.github.merge(p["pr_number"], title)
        except http.HttpError as exc:
            self.db.update_proposal(p["id"], self.now(), status="review", last_error=str(exc)[:500])
            self.tg.send(f"❌ GitHub no me deja fusionar «{esc(p['title'])}»: {esc(str(exc)[:300])}",
                         review_buttons(p["id"]))
            return
        self.github.delete_branch(self.branch(p))
        self.db.update_proposal(p["id"], self.now(), status="merged", merged_at=self.now().isoformat())
        self.tg.send(
            f"✅ Fusionado en main: «{esc(p['draft']['headline'])}».\n"
            "Para publicarlo, ejecuta en el VPS <code>cd /var/www/verticedev &amp;&amp; bash deploy.sh</code>. "
            "Te aviso cuando lo vea en producción."
        )

    def discard(self, pid: int) -> None:
        p = self.db.proposal(pid)
        if not p:
            return
        if p.get("pr_number") and not self.cfg.dry_run:
            try:
                self.github.close(p["pr_number"])
                self.github.delete_branch(self.branch(p))
            except http.HttpError as exc:
                log.warning("No he podido cerrar el PR %s: %s", p["pr_number"], exc)
        self.db.update_proposal(pid, self.now(), status="discarded")
        self.tg.send(f"🗑 Descartado «{esc(p['title'])}».")

    def livecheck(self) -> None:
        for p in self.db.proposals(["merged"]):
            url = self.page_url(p["slug"])
            try:
                _, body = http.request("GET", url, timeout=20, retries=0)
            except http.HttpError:
                continue
            if p["draft"]["headline"] in html.unescape(body.decode("utf-8", errors="replace")):
                self.db.update_proposal(p["id"], self.now(), status="live", live_at=self.now().isoformat())
                self.tg.send(
                    f"🌐 Ya está en producción: {url}\n"
                    "Si quieres acelerar la indexación: Search Console → Inspeccionar URL → Solicitar indexación."
                )

    # --- medición y aprendizaje ----------------------------------------------

    def learn_daily(self) -> None:
        self.livecheck()
        if not self.gsc:
            return
        today = self.today()
        for p in self.db.proposals(["live"]):
            live = date.fromisoformat(p["live_at"][:10])
            age = (today - live).days
            url = self.page_url(p["slug"])
            if not p.get("indexed_at") and age >= 3 and (age == 3 or age % 7 == 0):
                self.check_index(p, url, age)
            for day in learning.MILESTONES:
                if age >= day + learning.GSC_LAG_DAYS and not self.db.has_outcome(p["id"], day):
                    self.measure(p, url, live, day)

    def check_index(self, p: Dict[str, Any], url: str, age: int) -> None:
        result = self.require_gsc().inspect(url)
        if result["verdict"] == "PASS":
            self.db.update_proposal(p["id"], self.now(), indexed_at=self.now().isoformat())
            self.tg.send(f"✅ Google ya ha indexado «{esc(p['title'])}» ({age} días).")
        elif age >= 14 and not p.get("index_alerted"):
            self.db.update_proposal(p["id"], self.now(), index_alerted=1)
            self.tg.send(
                f"⚠️ «{esc(p['title'])}» lleva {age} días sin indexar ({esc(result['coverage'] or 'sin estado')}). "
                "Suele ser señal de que Google no le ve valor suficiente: lo tendré en cuenta al aprender."
            )

    def measure(self, p: Dict[str, Any], url: str, live: date, day: int) -> None:
        gsc = self.require_gsc()
        end = live + timedelta(days=day - 1)
        after = gsc.page_totals(url, live, end)
        baseline = None
        if p["kind"] == "improve":
            before = gsc.page_totals(url, live - timedelta(days=day), live - timedelta(days=1))
            baseline = before["impressions"]
            result = learning.improve_score(before["impressions"], after["impressions"], before["clicks"], after["clicks"])
        else:
            result = learning.outcome_score(after["impressions"], after["clicks"], after["position"])
        self.db.add_outcome(p["id"], day, self.now(), after["impressions"], after["clicks"], after["position"],
                            baseline, result)
        factor = learning.UPDATE_FACTOR.get(day, 0.0)
        if factor and p.get("features"):
            old = learning.merged_weights(self.db.weights())
            new = learning.update_weights(old, p["features"], result, factor)
            self.db.set_weights(new)
            self.db.log_event(self.now(), "weights", {"proposal": p["id"], "day": day, "target": result,
                                                       "before": old, "after": new})
        if day == learning.MILESTONES[-1]:
            self.db.update_proposal(p["id"], self.now(), status="measured")
        pos = fmt_num(after["position"], 1) if after["position"] else "—"
        extra = f" (antes: {fmt_num(baseline)} impr.)" if baseline is not None else ""
        self.tg.send(
            f"📈 «{esc(p['title'])}» a los {day} días: {fmt_num(after['impressions'])} impresiones{extra}, "
            f"{fmt_num(after['clicks'])} clics, posición {pos}. "
            f"Predije {fmt_num(p['predicted'] or 0, 2)}; resultado {fmt_num(result, 2)}."
        )

    # --- informe mensual -----------------------------------------------------

    def monthly(self) -> None:
        self.services_report()
        self.refresh_insights()

    def services_report(self) -> None:
        self.site.sync()
        site = self.site.load()
        rows = self.db.latest_gsc_snapshot()
        if not rows and self.gsc:
            end = self.today() - timedelta(days=2)
            rows = self.gsc.query(end - timedelta(days=89), end, ["query", "page"], row_limit=5000)
        by_service: Dict[str, Dict[str, Any]] = {
            s["slug"]: {"servicio": s["name"], "impresiones": 0, "clics": 0, "consultas": {}} for s in site.services
        }
        unmatched: Dict[str, int] = {}
        for r in rows:
            slug = research.match_service(r["query"], site)
            if slug:
                bucket = by_service[slug]
                bucket["impresiones"] += r["impressions"]
                bucket["clics"] += r["clicks"]
                bucket["consultas"][r["query"]] = bucket["consultas"].get(r["query"], 0) + r["impressions"]
            else:
                unmatched[r["query"]] = unmatched.get(r["query"], 0) + r["impressions"]
        for bucket in by_service.values():
            top = sorted(bucket["consultas"].items(), key=lambda kv: kv[1], reverse=True)[:10]
            bucket["consultas"] = [{"consulta": q, "impresiones": n} for q, n in top]
        suggest_rows = self.db.query(
            "SELECT keyword, suggest_hits, service FROM keywords WHERE suggest_hits > 0 ORDER BY suggest_hits DESC LIMIT 60")
        context = {
            "servicios": [{"slug": s["slug"], "nombre": s["name"], "grupo": s["group"], "lead": s.get("lead", "")}
                          for s in site.services],
            "busquedas_por_servicio": by_service,
            "consultas_sin_servicio": [{"consulta": q, "impresiones": n}
                                       for q, n in sorted(unmatched.items(), key=lambda kv: kv[1], reverse=True)[:40]],
            "autocompletado": suggest_rows,
            "total_impresiones": sum(r["impressions"] for r in rows),
        }
        report = self.llm.run("report", schemas.load_prompt("report"), json.dumps(context, ensure_ascii=False, indent=1),
                              schemas.REPORT, self.cfg.model_plan)
        icons = {"mantener": "🟢", "reforzar": "🟡", "reorientar": "🔁", "revisar": "🔴"}
        lines = ["💼 <b>Informe mensual de servicios</b>", esc(report.get("summary", ""))]
        for s in report.get("services", []):
            lines.append(f"\n{icons.get(s['verdict'], '•')} <b>{esc(s['slug'])}</b> · {esc(s['verdict'])}")
            lines.append(f"{esc(s['evidence'])}\n→ {esc(s['suggestion'])}")
        if report.get("gaps"):
            lines.append("\n<b>Huecos sin servicio</b>")
            for g in report["gaps"]:
                lines.append(f"• {esc(g['topic'])}: {esc(g['evidence'])} → {esc(g['suggestion'])}")
        self.tg.send("\n".join(lines))

    def refresh_insights(self) -> None:
        outcomes = self.db.outcomes()
        decisions = self.db.recent_decisions(40)
        if not outcomes and len(decisions) < 3:
            self.tg.send("🧠 Aún no hay datos suficientes para sacar aprendizajes: sigo midiendo.")
            return
        context = {
            "resultados": outcomes,
            "decisiones": decisions,
            "pesos": learning.describe_weights(learning.merged_weights(self.db.weights())),
            "memoria_anterior": self.db.latest_insight(),
        }
        result = self.llm.run("insights", schemas.load_prompt("insights"),
                              json.dumps(context, ensure_ascii=False, indent=1, default=str),
                              schemas.INSIGHTS, self.cfg.model_plan)
        memo = [m.strip() for m in result.get("memo", []) if m.strip()][:8]
        if memo:
            self.db.add_insight(self.now(), memo)
            self.tg.send("🧠 <b>Lo que he aprendido</b>\n" + "\n".join(f"• {esc(m)}" for m in memo))

    # --- estado -------------------------------------------------------------

    def status_text(self) -> str:
        counts = self.db.status_counts()
        labels = [
            ("proposed", "en el plan"), ("approved", "aprobadas"), ("drafting", "redactando"),
            ("review", "borradores por revisar"), ("publishing", "esperando a CI"),
            ("merged", "fusionadas sin desplegar"), ("live", "en producción y en medición"),
            ("measured", "medidas"), ("failed", "con error"),
        ]
        lines = ["📊 <b>Estado</b>"]
        lines += [f"• {label}: {counts[key]}" for key, label in labels if counts.get(key)]
        if len(lines) == 1:
            lines.append("• sin propuestas todavía; usa /investigar")
        for name, label in (("plan", "Plan"), ("learn", "Medición"), ("report", "Informe")):
            job = self.db.job(name)
            if job and job.get("last_run"):
                state = "" if job.get("ok") is None else (" ✅" if job["ok"] else " ❌")
                lines.append(f"{label}: {job['last_run'][:16].replace('T', ' ')}{state}")
        lines.append(f"Claude: {self.llm.calls_left_today()} llamadas disponibles hoy")
        if self.busy:
            lines.append(f"Ahora mismo: {esc(self.busy)}")
        return "\n".join(lines)

    def learning_text(self) -> str:
        weights = learning.describe_weights(learning.merged_weights(self.db.weights()))
        lines = ["🧠 <b>Aprendizaje</b>", "Pesos de las señales (más alto, más cuenta):"]
        lines += [f"• {esc(k)}: {fmt_num(v, 2)}" for k, v in weights.items()]
        prefs = self.db.prefs()
        if prefs:
            lines.append("Tus decisiones (aprobadas / descartadas):")
            lines += [f"• {esc(dim)} {esc(val)}: {a} / {r}" for (dim, val), (a, r) in sorted(prefs.items())]
        memo = self.db.latest_insight()
        if memo:
            lines.append("Memoria:")
            lines += [f"• {esc(m)}" for m in memo]
        return "\n".join(lines)

    # --- bucle principal ----------------------------------------------------

    def run_forever(self) -> None:
        from .bot import Bot
        from .scheduler import is_due, parse

        jobs = [
            ("plan", parse(self.cfg.plan_schedule), self.plan_week),
            ("learn", parse(self.cfg.learn_schedule), self.learn_daily),
            ("report", parse(self.cfg.report_schedule), self.monthly),
            ("ci", parse("every 2m"), self.ci_watch),
            ("live", parse("every 15m"), self.livecheck),
        ]
        bot = Bot(self)
        threads = [
            threading.Thread(target=self.worker_loop, name="worker", daemon=True),
            threading.Thread(target=bot.poll_forever, name="telegram", daemon=True),
        ]
        for t in threads:
            t.start()
        log.info("vertice-content-agent en marcha")
        self.tg.send("🟢 Agente de contenido en marcha. /ayuda para ver qué puedo hacer.")
        while not self.stop.is_set():
            current = self.now()
            for name, schedule, fn in jobs:
                job = self.db.job(name)
                last = datetime.fromisoformat(job["last_run"]) if job and job.get("last_run") else None
                if last is None and schedule.kind != "interval":
                    self.db.set_job(name, current)
                    continue
                if is_due(schedule, current, last):
                    self.db.set_job(name, current)
                    self.enqueue(name, self.tracked(name, fn))
            self.stop.wait(30)
        log.info("Parando")

    def tracked(self, name: str, fn: Callable[[], None]) -> Callable[[], None]:
        def wrapper() -> None:
            started = self.now()
            try:
                fn()
            except Exception as exc:
                self.db.set_job(name, started, ok=False, error=str(exc)[:500])
                raise
            self.db.set_job(name, started, ok=True)
        return wrapper
