"""Investigación semanal: qué se busca y qué oportunidades hay.

Junta las consultas reales de Search Console (90 días) con el autocompletado
de Google a partir de semillas (servicios, consultas principales y las que
añadas en VCA_SEEDS), calcula las señales de cada palabra clave y la puntúa.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import learning
from .site import SiteData
from .suggest import Suggestion
from .util import jaccard, normalize, tokens

INTENT_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("local", re.compile(r"\b(murcia|cartagena|lorca|molina de segura|alicante|region de murcia)\b")),
    ("commercial", re.compile(
        r"\b(precio|precios|cuanto cuesta|coste|presupuesto|contratar|agencia|freelance|desarrollador|"
        r"consultor|consultoria|empresa|empresas|pyme|pymes|a medida|servicio|servicios)\b")),
    ("comparison", re.compile(r"\b(vs|versus|diferencia|diferencias|mejor|mejores|alternativa|alternativas)\b|\so\s")),
]


def classify_intent(keyword: str) -> str:
    norm = normalize(keyword)
    for intent, pattern in INTENT_PATTERNS:
        if pattern.search(f" {norm} "):
            return intent
    return "informational"


@dataclass
class Candidate:
    keyword: str
    impressions: int = 0
    clicks: int = 0
    position: Optional[float] = None
    page: str = ""
    suggest_hits: int = 0
    suggest_rank: Optional[int] = None
    intent: str = ""
    covered_by: str = ""
    service: str = ""
    features: Dict[str, float] = field(default_factory=dict)
    score: float = 0.0

    def as_row(self) -> Dict[str, Any]:
        return asdict(self)


def aggregate_gsc(rows: Iterable[Dict[str, Any]]) -> Dict[str, Candidate]:
    """Una candidata por consulta: impresiones y clics sumados, posición ponderada y página principal."""
    out: Dict[str, Candidate] = {}
    best_page: Dict[str, int] = {}
    weighted: Dict[str, float] = {}
    for r in rows:
        key = normalize(r["query"])
        if not key:
            continue
        cand = out.setdefault(key, Candidate(keyword=r["query"].strip().lower()))
        cand.impressions += int(r["impressions"])
        cand.clicks += int(r["clicks"])
        weighted[key] = weighted.get(key, 0.0) + float(r.get("position") or 0) * int(r["impressions"])
        if int(r["impressions"]) > best_page.get(key, -1):
            best_page[key] = int(r["impressions"])
            cand.page = r.get("page", "")
    for key, cand in out.items():
        if cand.impressions:
            cand.position = round(weighted[key] / cand.impressions, 1)
    return out


def seeds_from(site: SiteData, gsc: Dict[str, Candidate], extra: Iterable[str], limit: int = 30) -> List[str]:
    seeds: List[str] = []
    for service in site.services:
        seeds.append(service.get("name", ""))
        seeds.append(service.get("seo", {}).get("service_type", ""))
    seeds.extend(c.keyword for c in sorted(gsc.values(), key=lambda c: c.impressions, reverse=True)[:15])
    seeds.extend(extra)
    unique: List[str] = []
    seen = set()
    for seed in seeds:
        key = normalize(seed)
        if key and key not in seen and len(key.split()) <= 6:
            seen.add(key)
            unique.append(seed.strip().lower())
    return unique[:limit]


def _post_tokens(post: Dict[str, Any]) -> set:
    return tokens(" ".join([post["title"], post.get("excerpt", ""), post["slug"].replace("-", " ")]))


def coverage(cand: Candidate, site: SiteData, site_url: str) -> str:
    """Artículo que ya cubre la palabra clave: por la página de Search Console o por solapamiento de términos."""
    prefix = f"{site_url}/blog/"
    if cand.page.startswith(prefix) and cand.position and cand.position <= 20:
        return cand.page[len(prefix):].strip("/")
    kw = tokens(cand.keyword)
    best, best_slug = 0.0, ""
    for post in site.posts:
        pt = _post_tokens(post)
        if len(kw & pt) >= 2:
            sim = jaccard(kw, pt)
            if sim > best:
                best, best_slug = sim, post["slug"]
    return best_slug if best >= 0.15 else ""


def match_service(keyword: str, site: SiteData) -> str:
    kw = tokens(keyword)
    best, best_slug = 0, ""
    for s in site.services:
        text = " ".join([s.get("name", ""), s.get("seo", {}).get("title", ""), s.get("seo", {}).get("service_type", ""),
                         " ".join(s.get("bullets", []))])
        overlap = len(kw & tokens(text))
        if overlap > best:
            best, best_slug = overlap, s["slug"]
    return best_slug if best >= 2 or (best == 1 and len(kw) == 1) else ""


def features(c: Candidate) -> Dict[str, float]:
    position = c.position or 0.0
    if 4 <= position <= 20:
        opportunity = 1.0
    elif 20 < position <= 40:
        opportunity = 0.5
    else:
        opportunity = 0.0
    return {
        "bias": 1.0,
        "impressions": round(min(1.0, math.log1p(c.impressions) / math.log1p(1000)), 4),
        "opportunity": opportunity,
        "demand": round(min(1.0, c.suggest_hits / 4), 4),
        "suggest_rank": round(1 - (c.suggest_rank - 1) / 10, 4) if c.suggest_rank and c.suggest_rank <= 10 else 0.0,
        "service_fit": 1.0 if c.service else 0.0,
        "uncovered": 0.0 if c.covered_by else 1.0,
        "local": 1.0 if c.intent == "local" else 0.0,
        "commercial": 1.0 if c.intent == "commercial" else 0.0,
        "comparison": 1.0 if c.intent == "comparison" else 0.0,
    }


def is_noise(keyword: str) -> bool:
    norm = normalize(keyword)
    return len(norm) < 3 or len(norm) > 80 or not tokens(norm)


def build_candidates(
    gsc_rows: List[Dict[str, Any]],
    suggestions: Dict[str, Suggestion],
    site: SiteData,
    site_url: str,
    weights: Dict[str, float],
    prefs: Dict[Tuple[str, str], Tuple[int, int]],
) -> List[Candidate]:
    cands = aggregate_gsc(gsc_rows)
    for key, s in suggestions.items():
        cand = cands.setdefault(key, Candidate(keyword=s.keyword))
        cand.suggest_hits += s.hits
        cand.suggest_rank = min(cand.suggest_rank or 99, s.best_rank)
    out = []
    for cand in cands.values():
        if is_noise(cand.keyword):
            continue
        cand.intent = classify_intent(cand.keyword)
        cand.covered_by = coverage(cand, site, site_url)
        cand.service = match_service(cand.keyword, site)
        cand.features = features(cand)
        kind = "improve" if cand.covered_by else "new"
        category = (site.post(cand.covered_by) or {}).get("category", "")
        cand.score = learning.score(weights, prefs, cand.features,
                                    {"category": category, "intent": cand.intent, "kind": kind})
        out.append(cand)
    return sorted(out, key=lambda c: c.score, reverse=True)
