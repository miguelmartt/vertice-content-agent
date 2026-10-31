"""Del borrador estructurado de la IA a la vista Blade del blog, y su validación.

La IA nunca escribe HTML: devuelve bloques (h2, párrafo, lista, código, tabla,
recuadro) con un marcado mínimo en línea (**negrita**, `código` y
[texto](/ruta)). Aquí se escapa todo y se genera el HTML, así que el resultado
cumple las reglas del sitio: sin style, sin script, sin Blade inyectado y con
enlaces internos solo a páginas que existen.

El texto de la IA es una entrada no fiable (lo pueden orientar las búsquedas que
lee). Por eso no se «escapa Blade», que tiene demasiados casos raros: @, { y }
se codifican como entidades HTML, el navegador los pinta igual y Blade no ve
ninguna directiva, eco ni comentario. `validate` comprueba al final que en la
vista no queda sintaxis de Blade fuera de la plantilla.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from .util import SLUG_RE, reading_time, word_count

# Lenguajes que carga resources/js/prism.js (más los que trae el núcleo de Prism).
CODE_LANGUAGES = {"php", "bash", "sql", "yaml", "json", "ini", "python", "docker", "javascript", "css", "markup"}
BLOCK_TYPES = {"h2", "h3", "p", "ul", "ol", "code", "callout", "table"}

# El job check-debug de CI rechaza esto en cualquier .blade.php, también dentro de un bloque de código.
DEBUG_RE = re.compile(r"\b(dd|dump|var_dump|print_r)\s*\(")
FORBIDDEN_RENDERED = [
    (re.compile(r"style\s*=", re.I), "atributo style"),
    (re.compile(r"<style", re.I), "etiqueta <style>"),
    (re.compile(r"<script", re.I), "etiqueta <script>"),
    (re.compile(r"console\.log\s*\("), "console.log("),
]
# Lo único de Blade que puede haber en la vista: la plantilla y los url() de enlaces internos ya comprobados.
INTERNAL_PATH_RE = re.compile(r"^/(?:[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*)?$")
TEMPLATE_RE = re.compile(
    r"@extends\('layouts\.article', \[|@section\('(?:intro|body)'\)|@endsection"
    r"|\{\{ url\('/(?:[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*)?'\) \}\}"
)
BLADE_CHARS_RE = re.compile(r"[@{}]")
BLADE_ENTITIES = {"@": "&#64;", "{": "&#123;", "}": "&#125;"}
# Campos que van como parámetros de @extends: ahí no sirven entidades, así que se rechazan.
METADATA_KEYS = ("headline", "seoTitle", "description", "excerpt", "cta")

INDENT = "    "


def php_str(value: str) -> str:
    """Literal de cadena PHP entre comillas simples."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def blade_safe(text: str) -> str:
    """Escapa HTML y codifica @, { y } como entidades: Blade no encuentra nada que compilar."""
    return BLADE_CHARS_RE.sub(lambda m: BLADE_ENTITIES[m.group(0)], html.escape(text, quote=True))


def _clean_path(url: str) -> str:
    path = url.split("#", 1)[0].split("?", 1)[0]
    return path.rstrip("/") or "/"


EXTERNAL_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?(?:/[^\s\"'<>`]*)?$")


def render_inline(
    text: str,
    site_paths: Set[str],
    errors: Optional[List[str]] = None,
    absolute: bool = False,
    origin: str = "",
) -> str:
    """Escapa el texto y aplica **negrita**, `código` y [texto](url).

    Con `absolute` genera HTML normal para la vista previa (enlaces internos con `origin`);
    sin él, HTML para una vista Blade.
    """
    errors = errors if errors is not None else []
    escape = (lambda s: html.escape(s, quote=True)) if absolute else blade_safe
    escaped = escape(text.strip())

    codes: List[str] = []

    def keep_code(match: "re.Match[str]") -> str:
        codes.append(f"<code>{match.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    out = re.sub(r"`([^`]+)`", keep_code, escaped)

    def link(match: "re.Match[str]") -> str:
        label, url = match.group(1), html.unescape(match.group(2)).strip()
        if url.startswith("/"):
            path = _clean_path(url)
            if not INTERNAL_PATH_RE.match(path) or path not in site_paths:
                errors.append(f"Enlace interno a una página que no existe: {path}")
                return label
            if absolute:
                return f'<a href="{html.escape(origin + path, quote=True)}">{label}</a>'
            return f"<a href=\"{{{{ url('{path}') }}}}\">{label}</a>"
        if EXTERNAL_URL_RE.match(url):
            # Enlaces que propone la IA: sin transmitir autoridad y sin acceso a la página de origen.
            return f'<a href="{escape(url)}" target="_blank" rel="nofollow noopener noreferrer">{label}</a>'
        errors.append(f"Enlace no permitido (solo rutas internas o https): {url}")
        return label

    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], out)


def _render_code(block: Dict[str, Any], errors: List[str]) -> List[str]:
    language = (block.get("language") or "").strip().lower()
    if language == "html":
        language = "markup"
    if language not in CODE_LANGUAGES:
        errors.append(f"Lenguaje de código no cargado en Prism: {language or '(vacío)'}")
        language = "bash"
    code = (block.get("code") or "").rstrip()
    return [f'{INDENT}<pre><code class="language-{language}">{blade_safe(code)}</code></pre>']


def _render_table(block: Dict[str, Any], site_paths: Set[str], errors: List[str]) -> List[str]:
    headers = [str(h) for h in block.get("headers") or []]
    rows = block.get("rows") or []
    if not headers or not rows:
        errors.append("Tabla sin cabeceras o sin filas")
        return []
    i2, i3, i4, i5 = INDENT * 2, INDENT * 3, INDENT * 4, INDENT * 5
    lines = [f'{INDENT}<div class="table-wrap">', f'{i2}<table class="table-stack">', f"{i3}<thead>", f"{i4}<tr>"]
    lines += [f'{i5}<th scope="col">{render_inline(h, site_paths, errors)}</th>' for h in headers]
    lines += [f"{i4}</tr>", f"{i3}</thead>", f"{i3}<tbody>"]
    for row in rows:
        cells = [str(c) for c in row]
        if len(cells) != len(headers):
            errors.append("Una fila de la tabla no tiene tantas celdas como cabeceras")
        lines.append(f"{i4}<tr>")
        for header, cell in zip(headers, cells):
            label = blade_safe(header)
            lines.append(f'{i5}<td data-label="{label}">{render_inline(cell, site_paths, errors)}</td>')
        lines.append(f"{i4}</tr>")
    lines += [f"{i3}</tbody>", f"{i2}</table>", f"{INDENT}</div>"]
    return lines


def render_blocks(blocks: Iterable[Dict[str, Any]], site_paths: Set[str], errors: List[str]) -> str:
    chunks: List[List[str]] = []
    for block in blocks:
        kind = block.get("type")
        text = block.get("text") or ""
        if kind in ("h2", "h3", "p"):
            chunks.append([f"{INDENT}<{kind}>{render_inline(text, site_paths, errors)}</{kind}>"])
        elif kind in ("ul", "ol"):
            items = [i for i in block.get("items") or [] if str(i).strip()]
            if not items:
                errors.append("Lista vacía")
                continue
            lines = [f"{INDENT}<{kind}>"]
            lines += [f"{INDENT * 2}<li>{render_inline(str(i), site_paths, errors)}</li>" for i in items]
            lines.append(f"{INDENT}</{kind}>")
            chunks.append(lines)
        elif kind == "code":
            chunks.append(_render_code(block, errors))
        elif kind == "callout":
            chunks.append([
                f'{INDENT}<div class="callout callout-accent">',
                f"{INDENT * 2}<p>{render_inline(text, site_paths, errors)}</p>",
                f"{INDENT}</div>",
            ])
        elif kind == "table":
            chunks.append(_render_table(block, site_paths, errors))
        else:
            errors.append(f"Tipo de bloque desconocido: {kind}")
    return "\n\n".join("\n".join(c) for c in chunks if c)


def render_view(draft: Dict[str, Any], site_paths: Set[str], errors: Optional[List[str]] = None) -> str:
    """Vista Blade completa para resources/views/blog/<slug>.blade.php."""
    errors = errors if errors is not None else []
    raw_keywords = draft.get("keywords") or []
    keywords = ", ".join(php_str(str(k)) for k in (raw_keywords if isinstance(raw_keywords, list) else []))
    params = [
        f"{INDENT}'headline' => {php_str(draft['headline'])},",
        f"{INDENT}'seoTitle' => {php_str(draft['seoTitle'])},",
        f"{INDENT}'description' => {php_str(draft['description'])},",
        f"{INDENT}'keywords' => [{keywords}],",
        f"{INDENT}'cta' => {php_str(draft['cta'])},",
    ]
    intro = render_inline(draft.get("intro") or "", site_paths, errors)
    body = render_blocks(draft.get("blocks") or [], site_paths, errors)
    return (
        "@extends('layouts.article', [\n" + "\n".join(params) + "\n])\n\n"
        f"@section('intro')\n{INDENT}{intro}\n@endsection\n\n"
        f"@section('body')\n{body}\n@endsection\n"
    )


def render_preview(draft: Dict[str, Any], site_paths: Set[str], origin: str = "") -> str:
    """HTML autónomo para leer el borrador en el móvil (se manda por Telegram, no se publica)."""
    errors: List[str] = []
    parts: List[str] = []

    def inline(text: str) -> str:
        return render_inline(text, site_paths, errors, absolute=True, origin=origin)

    for block in draft.get("blocks") or []:
        kind = block.get("type")
        text = block.get("text") or ""
        if kind in ("h2", "h3", "p"):
            parts.append(f"<{kind}>{inline(text)}</{kind}>")
        elif kind in ("ul", "ol"):
            items = "".join(f"<li>{inline(str(i))}</li>" for i in block.get("items") or [])
            parts.append(f"<{kind}>{items}</{kind}>")
        elif kind == "code":
            parts.append(f"<pre><code>{html.escape(block.get('code') or '')}</code></pre>")
        elif kind == "callout":
            parts.append(f"<blockquote>{inline(text)}</blockquote>")
        elif kind == "table":
            head = "".join(f"<th>{html.escape(str(h))}</th>" for h in block.get("headers") or [])
            rows = "".join(
                "<tr>" + "".join(f"<td>{inline(str(c))}</td>" for c in r) + "</tr>"
                for r in block.get("rows") or []
            )
            parts.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
    title = html.escape(draft.get("headline", ""))
    meta = (
        f"<p class=meta><b>Title:</b> {html.escape(draft.get('seoTitle', ''))}<br>"
        f"<b>Description:</b> {html.escape(draft.get('description', ''))}</p>"
    )
    style = (
        "body{font:17px/1.6 system-ui,sans-serif;max-width:720px;margin:0 auto;padding:16px;color:#0a0a0a}"
        "pre{background:#161617;color:#f5f5f7;padding:12px;overflow:auto;border-radius:8px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #c7c7cc;padding:6px}"
        "blockquote{border-left:4px solid #c5ff3a;margin:0;padding:4px 12px;background:#f5f5f7}"
        ".meta{color:#6e6e73;font-size:14px}"
    )
    intro = inline(draft.get("intro") or "")
    return (
        f"<!doctype html><html lang=es><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{title}</title>"
        f"<style>{style}</style><h1>{title}</h1>{meta}<p><strong>{intro}</strong></p>{''.join(parts)}</html>"
    )


def draft_words(draft: Dict[str, Any]) -> int:
    texts = [draft.get("intro") or ""]
    for block in draft.get("blocks") or []:
        texts.append(block.get("text") or "")
        texts.extend(str(i) for i in block.get("items") or [])
        for row in block.get("rows") or []:
            texts.extend(str(c) for c in row)
    return word_count(texts)


def validate(
    draft: Dict[str, Any],
    *,
    kind: str,
    slug: str,
    existing_slugs: Set[str],
    categories: Iterable[str],
    site_paths: Set[str],
    min_words: int,
) -> List[str]:
    """Errores que impiden publicar el borrador; vacío si está bien."""
    errors: List[str] = []
    for key in ("headline", "seoTitle", "description", "excerpt", "cta", "intro", "category"):
        if not str(draft.get(key) or "").strip():
            errors.append(f"Falta el campo {key}")
    if errors:
        return errors

    for key in METADATA_KEYS:
        if BLADE_CHARS_RE.search(draft[key]):
            errors.append(f"{key} no puede contener @, {{ ni }} (Blade los interpretaría)")
    keywords = draft.get("keywords") or []
    if not isinstance(keywords, list) or len(keywords) > 12:
        errors.append("keywords debe ser una lista de 12 palabras clave como máximo")
    elif any(not isinstance(k, str) or len(k) > 60 or BLADE_CHARS_RE.search(k) for k in keywords):
        errors.append("Cada palabra clave debe ser un texto de 60 caracteres como máximo, sin @, { ni }")
    if len(draft["seoTitle"]) > 60:
        errors.append(f"seoTitle tiene {len(draft['seoTitle'])} caracteres; máximo 60")
    if not 120 <= len(draft["description"]) <= 155:
        errors.append(f"description tiene {len(draft['description'])} caracteres; debe tener entre 120 y 155")
    if len(draft["headline"]) > 90:
        errors.append(f"headline tiene {len(draft['headline'])} caracteres; máximo 90")
    if not 80 <= len(draft["excerpt"]) <= 230:
        errors.append(f"excerpt tiene {len(draft['excerpt'])} caracteres; debe tener entre 80 y 230")
    if draft["category"] not in set(categories):
        errors.append(f"Categoría no admitida: {draft['category']}")
    if not SLUG_RE.match(slug) or len(slug) > 60:
        errors.append(f"Slug no válido: {slug}")
    if kind == "new" and slug in existing_slugs:
        errors.append(f"Ya existe un artículo con el slug {slug}")
    if kind == "improve" and slug not in existing_slugs:
        errors.append(f"No existe el artículo a mejorar: {slug}")

    blocks = draft.get("blocks") or []
    unknown = sorted({str(b.get("type")) for b in blocks} - BLOCK_TYPES)
    if unknown:
        errors.append(f"Tipos de bloque no admitidos: {', '.join(unknown)}")
    if sum(1 for b in blocks if b.get("type") == "h2") < 4:
        errors.append("El cuerpo necesita al menos 4 secciones <h2>")
    words = draft_words(draft)
    if words < min_words:
        errors.append(f"El artículo tiene {words} palabras; mínimo {min_words}")

    view = render_view(draft, site_paths, errors)
    if DEBUG_RE.search(view):
        errors.append("Contiene dd(, dump(, var_dump( o print_r(: CI lo rechaza incluso dentro de un bloque de código")
    for pattern, label in FORBIDDEN_RENDERED:
        if pattern.search(view):
            errors.append(f"La vista contiene {label}")
    if BLADE_CHARS_RE.search(TEMPLATE_RE.sub("", view)):
        errors.append("La vista contiene sintaxis de Blade fuera de la plantilla")
    return sorted(set(errors), key=errors.index)


def external_links(draft: Dict[str, Any]) -> List[str]:
    """Dominios externos a los que enlaza el borrador, para enseñarlos al revisarlo."""
    texts = [draft.get("intro") or ""]
    for block in draft.get("blocks") or []:
        texts.append(block.get("text") or "")
        texts.extend(str(i) for i in block.get("items") or [])
        for row in block.get("rows") or []:
            texts.extend(str(c) for c in row)
    domains = re.findall(r"\]\(https://([^/)\s:]+)", "\n".join(texts))
    return sorted(set(d.lower() for d in domains))


def finalize(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Campos derivados: tiempo de lectura."""
    return {**draft, "reading": reading_time(draft_words(draft))}
