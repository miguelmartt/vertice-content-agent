from __future__ import annotations

from typing import Any, Dict, List

from vca.site import SiteData

CATEGORIES = ["IA & ML", "DevOps", "Laravel", "Seguridad", "Performance", "Bases de Datos", "Negocio"]


def site_data() -> SiteData:
    return SiteData(
        posts=[
            {"slug": "pnpm-vs-npm-ventajas", "title": "npm o pnpm: diferencias reales y cuál usar en tu proyecto",
             "excerpt": "pnpm instala más rápido y evita dependencias fantasma.", "category": "DevOps",
             "date": "2026-05-26", "reading": "8 min"},
            {"slug": "laravel-queues-horizon-produccion", "title": "Laravel Queues y Horizon en producción",
             "excerpt": "Trabajo pesado fuera de la petición HTTP.", "category": "Laravel",
             "date": "2026-04-24", "reading": "9 min"},
        ],
        categories=list(CATEGORIES),
        services=[
            {"slug": "apps-medida", "name": "Software y apps a medida", "group": "software",
             "bullets": ["Paneles de gestión", "ERPs ligeros"],
             "seo": {"title": "Software de gestión y apps a medida para empresas", "service_type": "Desarrollo de software a medida"}},
            {"slug": "automatizacion", "name": "Automatización de procesos", "group": "software",
             "bullets": ["Emails y facturas"],
             "seo": {"title": "Automatización de procesos de negocio para PYMEs", "service_type": "Automatización de procesos de negocio"}},
        ],
        projects=[{"slug": "arraystock", "name": "ArrayStock", "tagline": "Inventario para bares"}],
    )


def good_draft(words_per_paragraph: int = 60, sections: int = 5) -> Dict[str, Any]:
    paragraph = " ".join(["palabra"] * words_per_paragraph)
    blocks: List[Dict[str, Any]] = []
    for i in range(sections):
        blocks.append({"type": "h2", "text": f"Sección {i + 1}"})
        blocks.extend({"type": "p", "text": paragraph} for _ in range(3))
    blocks.append({"type": "p", "text": "Más en [el servicio](/servicios/apps-medida)."})
    return {
        "headline": "Cómo elegir software de gestión a medida",
        "seoTitle": "Software de gestión a medida: cómo elegir",
        "description": "Qué mirar antes de encargar un software de gestión a medida: alcance, plazos, coste por fases y cómo evitar quedarte atado a un proveedor.",
        "excerpt": "Qué mirar antes de encargar un software de gestión a medida para que encaje con tu empresa y no te ate.",
        "keywords": ["software a medida", "gestión"],
        "cta": "¿Quieres que veamos qué necesita tu empresa?",
        "category": "Negocio",
        "intro": "La respuesta corta: empieza por el proceso, no por la herramienta.",
        "blocks": blocks,
        "needs_input": [],
    }
