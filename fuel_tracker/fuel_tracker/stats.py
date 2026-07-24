"""Silnik statystyk — spalanie segmentami między pełnymi bakami.

Spalanie liczone segmentami: segment domyka tankowanie z pełnym bakiem,
a otwiera poprzednie tankowanie z pełnym bakiem. Zużyte paliwo segmentu to
suma wolumenów wszystkich tankowań PO otwierającym (wyłącznie) aż do
domykającego (włącznie) — bo przy domknięciu bak wraca do pełna.
Wpis z flagą missed_previous przerywa łańcuch (dystans niewiarygodny).
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
    """Serie miesięczne: [{month: 'YYYY-MM', fuel: PLN (karta),
    fuel_own: PLN (prywatne), expenses: PLN, volume: L}]."""
    def blank(m: str) -> dict:
        return {"month": m, "fuel": 0.0, "fuel_own": 0.0,
                "expenses": 0.0, "volume": 0.0}

    months: dict[str, dict] = {}
    for f in fillups:
        m = f["date"][:7]
        d = months.setdefault(m, blank(m))
        key = "fuel_own" if f.get("paid_by") == "own" else "fuel"
        d[key] += f["total_cost"]
        d["volume"] += f["volume_l"]
    for e in expenses:
        m = e["date"][:7]
        d = months.setdefault(m, blank(m))
        d["expenses"] += e["cost"]
    out = sorted(months.values(), key=lambda d: d["month"])
    for d in out:
        d["fuel"] = round(d["fuel"], 2)
        d["fuel_own"] = round(d["fuel_own"], 2)
        d["expenses"] = round(d["expenses"], 2)
        d["volume"] = round(d["volume"], 2)
    return out


def month_fuel_spend(fillups: list[dict], month: str) -> float:
    """Wydatki na paliwo w miesiącu 'YYYY-MM'."""
    return round(sum(f["total_cost"] for f in fillups if f["date"][:7] == month), 2)


# ── Statystyki rozszerzone (0.4.0): sensory + strona Statystyki ──────────────

def estimated_range_km(avg_consumption: Optional[float],
                       tank_capacity_l: float) -> Optional[int]:
    """Zasięg na pełnym baku przy średnim spalaniu."""
    if not avg_consumption or not tank_capacity_l:
        return None
    return round(100.0 * tank_capacity_l / avg_consumption)


def ytd_fuel_cost(fillups: list[dict], year: str) -> float:
    """Wydatki na paliwo od początku roku 'YYYY'."""
    return round(sum(f["total_cost"] for f in fillups
                     if f["date"][:4] == year), 2)


def month_forecast_cost(fillups: list[dict], now: datetime) -> Optional[float]:
    """Prognoza kosztu paliwa w bieżącym miesiącu (tempo dotychczasowe)."""
    spent = month_fuel_spend(fillups, now.strftime("%Y-%m"))
    if not spent:
        return None
    days_in_month = monthrange(now.year, now.month)[1]
    return round(spent / now.day * days_in_month, 2)


def projected_annual_km(fillups: list[dict]) -> Optional[int]:
    """Roczne tempo przebiegu z całej historii (odometr vs czas)."""
    dated = sorted((f for f in fillups if f["date"]), key=lambda f: f["date"])
    if len(dated) < 2:
        return None
    try:
        t0 = datetime.fromisoformat(dated[0]["date"].replace(" ", "T"))
        t1 = datetime.fromisoformat(dated[-1]["date"].replace(" ", "T"))
    except ValueError:
        return None
    days = (t1 - t0).total_seconds() / 86400
    if days < 30:  # za krótka historia na sensowną ekstrapolację
        return None
    return round((dated[-1]["odometer"] - dated[0]["odometer"]) / days * 365)


# ── Leasing per auto (0.8.0) ──────────────────────────────────────────────

def lease_km_margin(lease_km_limit: Optional[int], lease_start: Optional[str],
                    lease_end: Optional[str], current_odometer: Optional[int],
                    now: datetime) -> Optional[float]:
    """Zapas km do limitu leasingu — ta sama krzywa co sensor.odo_vs_budget
    (template.yaml): limit × (teraz-start)/(koniec-start) − przebieg."""
    if not (lease_km_limit and lease_start and lease_end) \
            or current_odometer is None:
        return None
    try:
        start = datetime.fromisoformat(lease_start)
        end = datetime.fromisoformat(lease_end)
    except ValueError:
        return None
    span = (end - start).total_seconds()
    if span <= 0:
        return None
    elapsed = (now - start).total_seconds()
    return round(lease_km_limit * (elapsed / span) - current_odometer, 1)


def lease_depletion_date(lease_km_limit: Optional[int],
                         current_odometer: Optional[int],
                         annual_km: Optional[int],
                         now: datetime) -> Optional[str]:
    """Prognoza daty wyczerpania limitu km przy obecnym tempie
    (projected_annual_km)."""
    if not lease_km_limit or current_odometer is None or not annual_km:
        return None
    remaining = lease_km_limit - current_odometer
    if remaining <= 0:
        return now.strftime("%Y-%m-%d")
    days = remaining / annual_km * 365
    return (now + timedelta(days=days)).strftime("%Y-%m-%d")


def station_ranking(fillups: list[dict]) -> list[dict]:
    """Ranking stacji: wizyty, litry, suma, śr. cena — kolejność wg wizyt."""
    by_station: dict[str, dict] = {}
    for f in fillups:
        name = (f.get("station") or "").strip()
        if not name:
            continue
        d = by_station.setdefault(name, {
            "station": name, "visits": 0, "volume_l": 0.0, "total_cost": 0.0})
        d["visits"] += 1
        d["volume_l"] += f["volume_l"]
        d["total_cost"] += f["total_cost"]
    out = sorted(by_station.values(),
                 key=lambda d: (-d["visits"], d["station"]))
    for d in out:
        d["avg_price"] = round(d["total_cost"] / d["volume_l"], 2) \
            if d["volume_l"] else None
        d["volume_l"] = round(d["volume_l"], 1)
        d["total_cost"] = round(d["total_cost"], 2)
    return out


def best_station(fillups: list[dict], min_visits: int = 2) -> Optional[str]:
    """Stacja z najniższą średnią ceną litra (min. min_visits tankowań)."""
    ranked = [s for s in station_ranking(fillups)
              if s["visits"] >= min_visits and s["avg_price"]]
    if not ranked:
        return None
    return min(ranked, key=lambda s: s["avg_price"])["station"]


def record_entries(fillups: list[dict]) -> dict:
    """Rekordy do strony Statystyki (None gdy za mało danych)."""
    rows = [dict(f) for f in fillups]
    segments = build_segments(
        sorted(rows, key=lambda f: (f["odometer"], f["date"])))
    valid = [s for s in segments if s.distance_km > 0 and s.volume_l > 0]
    priced = [f for f in rows if f.get("price_per_l")]

    def seg_dict(s: Segment) -> dict:
        return {"date": s.end_date, "distance_km": s.distance_km,
                "volume_l": s.volume_l,
                "l_per_100km": round(s.l_per_100km, 2)}

    return {
        "best_consumption": seg_dict(min(valid, key=lambda s: s.l_per_100km))
                            if valid else None,
        "worst_consumption": seg_dict(max(valid, key=lambda s: s.l_per_100km))
                             if valid else None,
        "longest_segment": seg_dict(max(valid, key=lambda s: s.distance_km))
                           if valid else None,
        "cheapest_fillup": min(
            ({"date": f["date"], "price_per_l": f["price_per_l"],
              "station": f.get("station")} for f in priced),
            key=lambda f: f["price_per_l"]) if priced else None,
        "most_expensive_fillup": max(
            ({"date": f["date"], "price_per_l": f["price_per_l"],
              "station": f.get("station")} for f in priced),
            key=lambda f: f["price_per_l"]) if priced else None,
    }


def monthly_km(fillups: list[dict]) -> list[dict]:
    """Przebieg per miesiąc: różnica max odometrów kolejnych miesięcy."""
    max_odo: dict[str, int] = {}
    for f in fillups:
        m = f["date"][:7]
        max_odo[m] = max(max_odo.get(m, 0), f["odometer"])
    months = sorted(max_odo)
    out = []
    for prev, cur in zip(months, months[1:]):
        out.append({"month": cur, "km": max_odo[cur] - max_odo[prev]})
    return out


FLUIDS_CATEGORY = "Płyny"


# ── Koszt posiadania / TCO (0.13.0) ───────────────────────────────────────

_TCO_GROUPS = ("fluids", "service", "insurance", "fees", "other")
_MONTH_DAYS = 30.0  # przybliżenie kalendarzowe — upraszcza rata→koszt/miesiąc


def tco_breakdown(fillups: list[dict], expenses: list[dict],
                  monthly_rate: Optional[float] = None) -> dict:
    """Rozbicie kosztu posiadania na całej dostępnej historii (fillups +
    expenses): paliwo, wydatki per grupa TCO (expense_categories.tco_group),
    rata leasingu (monthly_rate × liczba miesięcy rozpiętości dat) oraz
    koszt/km i koszt/miesiąc.

    Dystans i koszt paliwa liczone przez compute_stats() — ta sama
    definicja co reszta statystyk, zero rozjazdu między kartami. Brak
    (lub pojedynczy) wpis z datą zwraca strukturę z samymi None/zerami,
    żeby /api/statistics nie musiało się tym martwić przy świeżym pojeździe.
    """
    s = compute_stats(fillups)
    fuel_total = s.total_cost
    distance_km = s.total_distance_km

    dated = [f["date"] for f in fillups if f.get("date")] + \
            [e["date"] for e in expenses if e.get("date")]
    period_months: Optional[float] = None
    if len(dated) >= 2:
        try:
            t0 = datetime.fromisoformat(min(dated).replace(" ", "T"))
            t1 = datetime.fromisoformat(max(dated).replace(" ", "T"))
        except ValueError:
            t0 = t1 = None
        if t0 is not None:
            days = (t1 - t0).total_seconds() / 86400
            if days > 0:
                period_months = days / _MONTH_DAYS

    by_group = {g: 0.0 for g in _TCO_GROUPS}
    for e in expenses:
        group = e.get("tco_group")
        by_group[group if group in _TCO_GROUPS else "other"] += e["cost"]
    by_group = {g: round(v, 2) for g, v in by_group.items()}
    expenses_total = round(sum(by_group.values()), 2)

    # monthly_rate=0 równoważne brakowi ustawionej raty (konwencja jak
    # monthly_budget w queries.summary) — leasing pomijany, nie liczony jako 0.
    lease_total = round(monthly_rate * period_months, 2) \
        if monthly_rate and period_months else None

    grand_total = round(fuel_total + expenses_total + (lease_total or 0), 2)

    def per_km(value: Optional[float]) -> Optional[float]:
        # Zero to prawidłowa wartość (np. brak wydatków w tym okresie) —
        # rozróżniamy ją od braku danych (dystans nieznany/zerowy → None).
        if value is None or not distance_km:
            return None
        return round(value / distance_km, 4)

    return {
        "period_months": round(period_months, 2) if period_months else None,
        "distance_km": distance_km,
        "fuel_total": fuel_total,
        "expenses_total": expenses_total,
        "by_group": by_group,
        "lease_total": lease_total,
        "grand_total": grand_total,
        "cost_per_km": {
            "fuel": per_km(fuel_total),
            "expenses": per_km(expenses_total),
            "lease": per_km(lease_total),
            "total": per_km(grand_total),
        },
        "cost_per_month": round(grand_total / period_months, 2)
                          if period_months else None,
        "cost_per_100km": round(100 * grand_total / distance_km, 2)
                          if distance_km else None,
    }


def monthly_report(fillups: list[dict], expenses: list[dict]) -> list[dict]:
    """Raport miesięczny do weryfikacji zestawienia ORLEN Flota.

    Kolumny: paliwo z karty / prywatne / płyny / inne wydatki / litry / km.
    """
    months: dict[str, dict] = {}

    def row(m: str) -> dict:
        return months.setdefault(m, {
            "month": m, "fuel_card": 0.0, "fuel_own": 0.0, "fluids": 0.0,
            "other_expenses": 0.0, "volume_l": 0.0, "km": 0})

    for f in fillups:
        d = row(f["date"][:7])
        if f.get("paid_by") == "own":
            d["fuel_own"] += f["total_cost"]
        else:
            d["fuel_card"] += f["total_cost"]
        d["volume_l"] += f["volume_l"]
    for e in expenses:
        d = row(e["date"][:7])
        key = "fluids" if e.get("category") == FLUIDS_CATEGORY \
            else "other_expenses"
        d[key] += e["cost"]
    for k in monthly_km(fillups):
        row(k["month"])["km"] = k["km"]

    out = sorted(months.values(), key=lambda d: d["month"], reverse=True)
    for d in out:
        for key in ("fuel_card", "fuel_own", "fluids", "other_expenses"):
            d[key] = round(d[key], 2)
        d["volume_l"] = round(d["volume_l"], 1)
    return out
