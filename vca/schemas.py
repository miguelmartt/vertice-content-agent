"""JSON Schema de las respuestas de Claude: plan, borrador, informe y memoria."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .render import BLOCK_TYPES, CODE_LANGUAGES

PROMPTS = Path(__file__).parent / "prompts"

STR: Dict[str, Any] = {"type": "string"}
STR_LIST: Dict[str, Any] = {"type": "array", "items": {"type": "string"}}
INTENTS = ["informational", "commercial", "comparison", "local"]


def load_prompt(name: str, **values: str) -> str:
    """Prompt de sistema con {{brand}}, {{site_url}}, {{owner}} y {{author}} rellenados."""
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def plan(categories: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["new", "improve"]},
                        "slug": STR,
                        "existing_slug": STR,
                        "title": STR,
                        "category": {"type": "string", "enum": list(categories)},
                        "target_keyword": STR,
                        "secondary_keywords": STR_LIST,
                        "intent": {"type": "string", "enum": INTENTS},
                        "angle": STR,
                        "outline": STR_LIST,
                        "rationale": STR,
                        "service": STR,
                    },
                    "required": ["kind", "slug", "title", "category", "target_keyword", "intent",
                                 "angle", "outline", "rationale"],
                },
            },
            "service_notes": STR,
        },
        "required": ["proposals"],
    }


def draft(categories: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "headline": STR,
            "seoTitle": STR,
            "description": STR,
            "excerpt": STR,
            "keywords": STR_LIST,
            "cta": STR,
            "category": {"type": "string", "enum": list(categories)},
            "intro": STR,
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": sorted(BLOCK_TYPES)},
                        "text": STR,
                        "items": STR_LIST,
                        "language": {"type": "string", "enum": sorted(CODE_LANGUAGES)},
                        "code": STR,
                        "headers": STR_LIST,
                        "rows": {"type": "array", "items": STR_LIST},
                    },
                    "required": ["type"],
                },
            },
            "needs_input": STR_LIST,
        },
        "required": ["headline", "seoTitle", "description", "excerpt", "keywords", "cta", "category",
                     "intro", "blocks", "needs_input"],
    }


REPORT: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": STR,
        "services": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slug": STR,
                    "verdict": {"type": "string", "enum": ["mantener", "reforzar", "reorientar", "revisar"]},
                    "evidence": STR,
                    "suggestion": STR,
                },
                "required": ["slug", "verdict", "evidence", "suggestion"],
            },
        },
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"topic": STR, "evidence": STR, "suggestion": STR},
                "required": ["topic", "evidence", "suggestion"],
            },
        },
    },
    "required": ["summary", "services", "gaps"],
}

INSIGHTS: Dict[str, Any] = {
    "type": "object",
    "properties": {"memo": STR_LIST},
    "required": ["memo"],
}
