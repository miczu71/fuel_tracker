"""Zapytania do bazy współdzielone przez web UI i publisher MQTT."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from . import prices as pr, stats as st


def fetch_fillups(conn: sqlite3.Connection, vehicle_id: int,
                  include_drafts: bool = False) -> list[dict]:
    q = "SELECT * FROM fillups WHERE vehicle_id = ?"
    if not include_drafts:
        q += " AND draft = 0"
    q += " ORDER BY odometer DESC, date DESC"
    return [dict(r) for r in conn.execute(q, (vehicle_id,)).fetchall()]


def fetch_expenses(conn: sqlite3.Connection, vehicle_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT e.*, c.name AS category, c.tco_group AS tco_group
           FROM expenses e LEFT JOIN expense_categories c ON c.id = e.category_id
           WHERE e.vehicle_id = ? ORDER BY e.date DESC""",
        (vehicle_id,),
    ).fetchall()]


def _iso_local(date_str: str) -> str | None:
    """'YYYY-MM-DD HH:MM' → ISO8601 ze strefą (wymóg device_class timestamp)."""
    try:
        tz = ZoneInfo(os.environ.get("TZ", "Europe/Warsaw"))
        return datetime.fromisoformat(date_str.replace(" ", "T")).replace(
            tzinfo=tz).isoformat()
    except (ValueError, KeyError):
        return None


def sensor_values(conn: sqlite3.Connection, vehicle_id: int,
                  monthly_budget: float, now: datetime | None = None,
                  fuel_type: str = "PB95", price_region: str | None = None,
                  tank_capacity_l: float = 0.0,
                  lease_km_limit: int | None = None,
                  lease_start: str | None = None,
                  lease_end: str | None = None,
                  current_odometer: int | None = None) -> dict:
    """Wartości dla publishera MQTT (slugi z publisher._SENSORS)."""
    fillups = fetch_fillups(conn, vehicle_id)
    expenses = fetch_expenses(conn, vehicle_id)
    s = st.compute_stats(fillups)
    now = now or datetime.now()
    month = now.strftime("%Y-%m")
    month_cost = st.month_fuel_spend(fillups, month)

    values: dict = {
        "total_cost": s.total_cost,
        "total_volume": s.total_volume_l,
        "fillup_count": s.fillup_count,
        "avg_consumption": s.avg_consumption,
        "last_consumption": s.last_consumption,
        "cost_per_km": s.cost_per_km,
        "avg_price_per_l": s.avg_price_per_l,
        "expenses_total": round(sum(e["cost"] for e in expenses), 2),
        "month_fuel_cost": month_cost,
        "budget_left_month": round(monthly_budget - month_cost, 2)
                             if monthly_budget else None,
        # Tankowania opłacone prywatnie — zastępuje ręczny
        # input_number.suma_moich_wydatkow_na_paliwo w zysk_z_wynajmu_auta.
        "self_paid_fuel_total": round(sum(
            f["total_cost"] for f in fillups
            if f.get("paid_by") == "own"), 2),
        # Statystyki 0.4.0
        "estimated_range_km": st.estimated_range_km(s.avg_consumption,
                                                    tank_capacity_l),
        "month_forecast_cost": st.month_forecast_cost(fillups, now),
        "ytd_fuel_cost": st.ytd_fuel_cost(fillups, now.strftime("%Y")),
        "projected_annual_km": st.projected_annual_km(fillups),
        "best_station": st.best_station(fillups),
    }
    # Leasing per auto (0.8.0) — przebieg z odometer_entity (parametr
    # current_odometer), awaryjnie z ostatniego tankowania.
    odometer = current_odometer if current_odometer is not None else (
        s.last_fillup["odometer"] if s.last_fillup else None)
    values["lease_km_margin"] = st.lease_km_margin(
        lease_km_limit, lease_start, lease_end, odometer, now)
    values["lease_depletion_date"] = st.lease_depletion_date(
        lease_km_limit, odometer, values["projected_annual_km"], now)
    if price_region:
        region = pr.latest_price(conn, price_region, fuel_type)
        values["region_fuel_price"] = region["price"] if region else None
        values["price_vs_region"] = round(
            s.last_fillup["price_per_l"] - region["price"], 2) \
            if region and s.last_fillup and s.last_fillup["price_per_l"] \
            else None
    if s.last_fillup:
        f = s.last_fillup
        values.update({
            "last_fillup_date": _iso_local(f["date"]),
            "last_fillup_odometer": f["odometer"],
            "last_fillup_price": f["price_per_l"],
            "last_fillup_volume": f["volume_l"],
            "last_fillup_cost": f["total_cost"],
            "last_fillup_station": f["station"],
        })
    return values


def summary(conn: sqlite3.Connection, vehicle_id: int,
            monthly_budget: float) -> dict:
    """Podsumowanie dla pulpitu web UI."""
    fillups = fetch_fillups(conn, vehicle_id)
    expenses = fetch_expenses(conn, vehicle_id)
    s = st.compute_stats(fillups)
    month = datetime.now().strftime("%Y-%m")
    month_cost = st.month_fuel_spend(fillups, month)
    return {
        "fillup_count": s.fillup_count,
        "total_cost": s.total_cost,
        "total_volume_l": s.total_volume_l,
        "total_distance_km": s.total_distance_km,
        "avg_consumption": s.avg_consumption,
        "last_consumption": s.last_consumption,
        "cost_per_km": s.cost_per_km,
        "avg_price_per_l": s.avg_price_per_l,
        "last_fillup": s.last_fillup,
        "expenses_total": round(sum(e["cost"] for e in expenses), 2),
        "self_paid_fuel_total": round(sum(
            f["total_cost"] for f in fillups
            if f.get("paid_by") == "own"), 2),
        "month": month,
        "month_fuel_cost": month_cost,
        "monthly_budget": monthly_budget,
        "budget_left_month": round(monthly_budget - month_cost, 2)
                             if monthly_budget else None,
        "monthly": st.monthly_series(fillups, expenses),
        "consumption_series": [
            {"date": seg.end_date, "value": round(seg.l_per_100km, 2)}
            for seg in s.segments if seg.distance_km > 0
        ],
        "price_series": [
            {"date": f["date"], "value": f["price_per_l"]}
            for f in sorted(fillups, key=lambda x: x["date"]) if f["price_per_l"]
        ],
    }
