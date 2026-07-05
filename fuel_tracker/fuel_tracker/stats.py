"""Silnik statystyk w stylu Fuelio.

Spalanie liczone segmentami: segment domyka tankowanie z pełnym bakiem,
a otwiera poprzednie tankowanie z pełnym bakiem. Zużyte paliwo segmentu to
suma wolumenów wszystkich tankowań PO otwierającym (wyłącznie) aż do
domykającego (włącznie) — bo przy domknięciu bak wraca do pełna.
Wpis z flagą missed_previous przerywa łańcuch (dystans niewiarygodny).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Segment:
    start_odo: int
    end_odo: int
    volume_l: float
    end_date: str

    @property
    def distance_km(self) -> int:
        return self.end_odo - self.start_odo

    @property
    def l_per_100km(self) -> float:
        return 100.0 * self.volume_l / self.distance_km


@dataclass
class Stats:
    fillup_count: int = 0
    total_cost: float = 0.0
    total_volume_l: float = 0.0
    avg_consumption: Optional[float] = None      # Σvol/Σdist po segmentach
    last_consumption: Optional[float] = None     # ostatni segment
    cost_per_km: Optional[float] = None
    total_distance_km: int = 0
    avg_price_per_l: Optional[float] = None
    last_fillup: Optional[dict] = None
    segments: list[Segment] = field(default_factory=list)


def build_segments(fillups: list[dict]) -> list[Segment]:
    """fillups: wiersze posortowane rosnąco po odometer, bez draftów."""
    segments: list[Segment] = []
    prev_full_odo: Optional[int] = None
    pending_volume = 0.0
    for f in fillups:
        if f["missed_previous"]:
            # Dystans od poprzedniego pełnego baku niewiarygodny — restart łańcucha.
            prev_full_odo = f["odometer"] if f["full_tank"] else None
            pending_volume = 0.0
            continue
        pending_volume += f["volume_l"]
        if f["full_tank"]:
            if prev_full_odo is not None and f["odometer"] > prev_full_odo:
                segments.append(Segment(
                    start_odo=prev_full_odo,
                    end_odo=f["odometer"],
                    volume_l=round(pending_volume, 3),
                    end_date=f["date"],
                ))
            prev_full_odo = f["odometer"]
            pending_volume = 0.0
    return segments


def compute_stats(fillups: list[dict]) -> Stats:
    """fillups: wiersze (dict/sqlite3.Row) niedraftowe, w dowolnej kolejności."""
    rows = sorted((dict(f) for f in fillups), key=lambda f: (f["odometer"], f["date"]))
    stats = Stats()
    if not rows:
        return stats

    stats.fillup_count = len(rows)
    stats.total_cost = round(sum(f["total_cost"] for f in rows), 2)
    stats.total_volume_l = round(sum(f["volume_l"] for f in rows), 3)
    stats.total_distance_km = rows[-1]["odometer"] - rows[0]["odometer"]
    stats.last_fillup = rows[-1]
    if stats.total_volume_l > 0:
        stats.avg_price_per_l = round(stats.total_cost / stats.total_volume_l, 2)
    if stats.total_distance_km > 0:
        stats.cost_per_km = round(stats.total_cost / stats.total_distance_km, 4)

    stats.segments = build_segments(rows)
    valid = [s for s in stats.segments if s.distance_km > 0 and s.volume_l > 0]
    if valid:
        total_dist = sum(s.distance_km for s in valid)
        total_vol = sum(s.volume_l for s in valid)
        stats.avg_consumption = round(100.0 * total_vol / total_dist, 2)
        stats.last_consumption = round(valid[-1].l_per_100km, 2)
    return stats


def segment_consumption_by_fillup(fillups: list[dict]) -> dict[int, float]:
    """Mapa id tankowania domykającego segment → L/100km (do tabeli historii)."""
    rows = sorted((dict(f) for f in fillups), key=lambda f: (f["odometer"], f["date"]))
    by_end_odo = {s.end_odo: s for s in build_segments(rows)}
    result: dict[int, float] = {}
    for f in rows:
        seg = by_end_odo.get(f["odometer"])
        if seg and f["full_tank"] and seg.distance_km > 0:
            result[f["id"]] = round(seg.l_per_100km, 2)
    return result


def monthly_series(fillups: list[dict], expenses: list[dict]) -> list[dict]:
    """Serie miesięczne: [{month: 'YYYY-MM', fuel: PLN, expenses: PLN, volume: L}]."""
    months: dict[str, dict] = {}
    for f in fillups:
        m = f["date"][:7]
        d = months.setdefault(m, {"month": m, "fuel": 0.0, "expenses": 0.0, "volume": 0.0})
        d["fuel"] += f["total_cost"]
        d["volume"] += f["volume_l"]
    for e in expenses:
        m = e["date"][:7]
        d = months.setdefault(m, {"month": m, "fuel": 0.0, "expenses": 0.0, "volume": 0.0})
        d["expenses"] += e["cost"]
    out = sorted(months.values(), key=lambda d: d["month"])
    for d in out:
        d["fuel"] = round(d["fuel"], 2)
        d["expenses"] = round(d["expenses"], 2)
        d["volume"] = round(d["volume"], 2)
    return out


def month_fuel_spend(fillups: list[dict], month: str) -> float:
    """Wydatki na paliwo w miesiącu 'YYYY-MM'."""
    return round(sum(f["total_cost"] for f in fillups if f["date"][:7] == month), 2)
