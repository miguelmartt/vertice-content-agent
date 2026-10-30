"""Autocompletado de Google como señal gratuita de demanda.

Es un endpoint público sin documentación oficial: se consulta con pausas, con
un tope de peticiones por ejecución, y si Google responde 429 se deja de pedir
hasta la siguiente ejecución.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set

from . import http
from .util import normalize

URL = "https://suggestqueries.google.com/complete/search"

PREFIXES = ("", "qué es", "cómo")
SUFFIXES = ("precio", "para empresas", "murcia", "vs", "ejemplos")


@dataclass
class Suggestion:
    keyword: str
    hits: int = 0
    best_rank: int = 99
    seeds: Set[str] = field(default_factory=set)


class Suggest:
    def __init__(
        self,
        lang: str = "es",
        country: str = "es",
        delay: float = 1.0,
        max_requests: int = 300,
        fetch: Optional[Callable[[str], List[str]]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.lang = lang
        self.country = country
        self.delay = delay
        self.max_requests = max_requests
        self.fetch = fetch or self._fetch
        self.sleep = sleep
        self.requests = 0
        self.blocked = False

    def _fetch(self, query: str) -> List[str]:
        _, payload = http.request(
            "GET",
            URL,
            params={"client": "firefox", "hl": self.lang, "gl": self.country, "ie": "utf-8", "oe": "utf-8", "q": query},
            timeout=15,
            retries=0,
        )
        data = json.loads(payload.decode("utf-8", errors="replace"))
        return [s for s in data[1] if isinstance(s, str)] if len(data) > 1 else []

    def suggestions(self, query: str) -> List[str]:
        if self.blocked or self.requests >= self.max_requests:
            return []
        if self.requests:
            self.sleep(self.delay)
        self.requests += 1
        try:
            return self.fetch(query)
        except http.HttpError as exc:
            if exc.status == 429:
                self.blocked = True
            return []
        except (ValueError, IndexError):
            return []

    def expand(self, seeds: Iterable[str]) -> Dict[str, Suggestion]:
        """Sugerencias para cada semilla con sus modificadores, agregadas por palabra clave normalizada."""
        found: Dict[str, Suggestion] = {}
        for seed in seeds:
            queries = [f"{p} {seed}".strip() for p in PREFIXES] + [f"{seed} {s}" for s in SUFFIXES]
            for query in queries:
                for rank, text in enumerate(self.suggestions(query), start=1):
                    key = normalize(text)
                    if not key:
                        continue
                    item = found.setdefault(key, Suggestion(keyword=text.strip().lower()))
                    item.hits += 1
                    item.best_rank = min(item.best_rank, rank)
                    item.seeds.add(seed)
                if self.blocked or self.requests >= self.max_requests:
                    return found
        return found
