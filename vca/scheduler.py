"""Programación de trabajos con especificaciones sencillas.

Formatos admitidos (hora local de VCA_TIMEZONE):
  "every 15m"   cada 15 minutos
  "07:30"       todos los días a las 07:30
  "mon 08:00"   cada lunes a las 08:00 (mon..sun o lun..dom)
  "1 09:00"     el día 1 de cada mes a las 09:00
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "lun": 0, "mar": 1, "mie": 2, "jue": 3, "vie": 4, "sab": 5, "dom": 6,
}


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class Schedule:
    kind: str  # interval | daily | weekly | monthly
    minutes: int = 0
    hour: int = 0
    minute: int = 0
    weekday: int = 0
    day: int = 1


def parse(spec: str) -> Schedule:
    text = spec.strip().lower()
    m = re.fullmatch(r"every\s+(\d+)\s*m", text)
    if m:
        return Schedule("interval", minutes=max(1, int(m.group(1))))
    m = re.fullmatch(r"(?:(\w+)\s+)?(\d{1,2}):(\d{2})", text)
    if not m:
        raise ScheduleError(f"Programación no válida: {spec!r}")
    prefix, hour, minute = m.group(1), int(m.group(2)), int(m.group(3))
    if hour > 23 or minute > 59:
        raise ScheduleError(f"Hora no válida: {spec!r}")
    if prefix is None:
        return Schedule("daily", hour=hour, minute=minute)
    if prefix in WEEKDAYS:
        return Schedule("weekly", hour=hour, minute=minute, weekday=WEEKDAYS[prefix])
    if prefix.isdigit() and 1 <= int(prefix) <= 31:
        return Schedule("monthly", hour=hour, minute=minute, day=int(prefix))
    raise ScheduleError(f"Programación no válida: {spec!r}")


def last_due(s: Schedule, now: datetime) -> datetime:
    """Último momento programado igual o anterior a `now` (no aplica a intervalos)."""
    at = now.replace(hour=s.hour, minute=s.minute, second=0, microsecond=0)
    if s.kind == "daily":
        return at if at <= now else at - timedelta(days=1)
    if s.kind == "weekly":
        at -= timedelta(days=(now.weekday() - s.weekday) % 7)
        return at if at <= now else at - timedelta(days=7)
    if s.kind == "monthly":
        year, month = now.year, now.month
        for _ in range(2):
            day = min(s.day, calendar.monthrange(year, month)[1])
            candidate = at.replace(year=year, month=month, day=day)
            if candidate <= now:
                return candidate
            year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        return candidate
    raise ScheduleError("last_due no se aplica a intervalos")


def is_due(s: Schedule, now: datetime, last_run: Optional[datetime]) -> bool:
    if s.kind == "interval":
        return last_run is None or now - last_run >= timedelta(minutes=s.minutes)
    if last_run is None:
        return False  # la primera vez solo se registra; así un reinicio no dispara nada atrasado
    return last_due(s, now) > last_run
