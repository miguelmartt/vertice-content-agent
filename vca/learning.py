"""Cómo aprende el agente.

1. Cada palabra clave candidata tiene unas señales (features) entre 0 y 1:
   impresiones en Search Console, oportunidad de posición, demanda en el
   autocompletado, encaje con un servicio, intención, etc.
2. La puntuación es la suma ponderada de esas señales, multiplicada por las
   preferencias de Miguel (lo que aprueba y lo que descarta, por categoría,
   intención y tipo de propuesta).
3. Cuando un artículo publicado cumple 28 y 56 días en producción, se mide en
   Search Console. La diferencia entre lo que predijo y lo que pasó ajusta los
   pesos (descenso de gradiente sobre un modelo lineal): las señales que
   acertaron ganan peso y las que fallaron lo pierden.
4. Cada mes, Claude resume lo aprendido en una memoria corta que se usa al
   planificar.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

DEFAULT_WEIGHTS: Dict[str, float] = {
    "bias": 0.05,
    "impressions": 0.30,
    "opportunity": 0.25,
    "demand": 0.25,
    "suggest_rank": 0.10,
    "service_fit": 0.15,
    "uncovered": 0.05,
    "local": 0.05,
    "commercial": 0.10,
    "comparison": 0.05,
}
LEARNING_RATE = 0.1
WEIGHT_BOUNDS = (-1.0, 1.5)
MILESTONES = (14, 28, 56)
GSC_LAG_DAYS = 3
# Peso del ajuste en cada hito: a los 14 días solo se informa.
UPDATE_FACTOR = {14: 0.0, 28: 0.5, 56: 1.0}
PREF_DIMS = ("category", "intent", "kind")


def merged_weights(stored: Dict[str, float]) -> Dict[str, float]:
    return {**DEFAULT_WEIGHTS, **{k: v for k, v in stored.items() if k in DEFAULT_WEIGHTS}}


def predict(weights: Dict[str, float], features: Dict[str, float]) -> float:
    value = sum(weights.get(k, 0.0) * float(v) for k, v in features.items())
    return max(0.0, min(1.5, value))


def pref_multiplier(prefs: Dict[Tuple[str, str], Tuple[int, int]], values: Dict[str, str]) -> float:
    """Media de (aprobadas + 1) / (decididas + 2) × 2 por dimensión: 1 sin datos, entre 0 y 2."""
    factors = []
    for dim in PREF_DIMS:
        approved, rejected = prefs.get((dim, values.get(dim, "")), (0, 0))
        factors.append(2 * (approved + 1) / (approved + rejected + 2))
    return sum(factors) / len(factors)


def score(weights: Dict[str, float], prefs: Dict[Tuple[str, str], Tuple[int, int]],
          features: Dict[str, float], values: Dict[str, str]) -> float:
    return round(predict(weights, features) * pref_multiplier(prefs, values), 4)


def outcome_score(impressions: int, clicks: int, position: Optional[float]) -> float:
    """Resultado de un artículo nuevo entre 0 y 1."""
    imp = min(1.0, math.log1p(max(impressions, 0)) / math.log1p(1000))
    clk = min(1.0, max(clicks, 0) / 20)
    pos = 0.0
    if position:
        pos = 1.0 if position <= 10 else 0.5 if position <= 20 else 0.0
    return round(0.6 * imp + 0.3 * clk + 0.1 * pos, 4)


def improve_score(before_impressions: int, after_impressions: int, before_clicks: int, after_clicks: int) -> float:
    """Resultado de una mejora entre 0 y 1: 0,5 es quedarse igual."""
    imp = math.tanh(math.log((after_impressions + 10) / (before_impressions + 10)))
    clk = math.tanh((after_clicks - before_clicks) / 5)
    return round(max(0.0, min(1.0, 0.5 + 0.35 * imp + 0.15 * clk)), 4)


def update_weights(weights: Dict[str, float], features: Dict[str, float], target: float,
                   factor: float = 1.0) -> Dict[str, float]:
    """Un paso de descenso de gradiente hacia el resultado medido."""
    if factor <= 0:
        return dict(weights)
    error = target - predict(weights, features)
    lo, hi = WEIGHT_BOUNDS
    updated = dict(weights)
    for name, value in features.items():
        if name in updated:
            updated[name] = round(max(lo, min(hi, updated[name] + LEARNING_RATE * factor * error * float(value))), 4)
    return updated


def describe_weights(weights: Dict[str, float]) -> Dict[str, Any]:
    """Pesos ordenados de mayor a menor, para el informe de aprendizaje."""
    return dict(sorted(weights.items(), key=lambda kv: kv[1], reverse=True))
