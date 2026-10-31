"""Utilidades de texto, fechas y formato."""

from __future__ import annotations

import math
import os
import re
import unicodedata
from datetime import date, datetime, timezone, tzinfo
from typing import Dict, Iterable, List, Set, Tuple
from zoneinfo import ZoneInfo

# Lo que necesita cualquier proceso hijo para funcionar (las de Windows, para desarrollo local).
BASE_ENV = {
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PATHEXT", "COMSPEC",
}


def child_env(*extra: str, prefixes: Tuple[str, ...] = ()) -> Dict[str, str]:
    """Entorno mínimo para un subproceso: sin los tokens del agente que no le hacen falta."""
    keys = BASE_ENV | set(extra)
    return {k: v for k, v in os.environ.items() if k.upper() in keys or k.startswith(prefixes)}

STOPWORDS = {
    "a", "al", "ante", "con", "como", "cual", "cuales", "cuando", "de", "del", "desde",
    "donde", "el", "en", "entre", "es", "esta", "este", "esto", "hay", "la", "las", "le",
    "lo", "los", "mas", "me", "mi", "mis", "muy", "no", "o", "os", "para", "pero", "por",
    "que", "se", "sea", "ser", "si", "sin", "sobre", "son", "su", "sus", "te", "tu", "tus",
    "un", "una", "uno", "unos", "unas", "y", "ya", "yo", "the", "and", "for", "of", "to",
    "vs", "versus",
}


def zone(tz: str) -> tzinfo:
    """Zona horaria; si el sistema no trae la base de datos (Windows), la local."""
    try:
        return ZoneInfo(tz)
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def now(tz: str) -> datetime:
    return datetime.now(zone(tz))


def today(tz: str) -> date:
    return now(tz).date()


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Minúsculas, sin tildes y con los espacios colapsados."""
    text = strip_accents(text.lower())
    text = re.sub(r"[^a-z0-9ñ+#. ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> Set[str]:
    return {t for t in normalize(text).replace(".", " ").split() if len(t) > 1 and t not in STOPWORDS}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", strip_accents(text.lower())).strip("-")
    return re.sub(r"-{2,}", "-", slug)[:60].strip("-")


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def word_count(texts: Iterable[str]) -> int:
    return sum(len(re.findall(r"\w+", t)) for t in texts)


def reading_time(words: int) -> str:
    return f"{max(3, math.ceil(words / 200))} min"


def fmt_num(value: float, decimals: int = 0) -> str:
    """Número con formato español: 1.234 o 7,5."""
    if decimals == 0:
        return f"{int(round(value)):,}".replace(",", ".")
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def split_message(text: str, limit: int = 3900) -> List[str]:
    """Trocea un mensaje largo por párrafos para no pasar del límite de Telegram."""
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    current = ""
    for block in text.split("\n"):
        candidate = f"{current}\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(block) > limit:
            parts.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        parts.append(current)
    return parts
