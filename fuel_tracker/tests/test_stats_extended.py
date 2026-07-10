"""Statystyki 0.4.0: zasięg, prognozy, rekordy, ranking stacji."""
from datetime import datetime, timedelta

from fuel_tracker import stats as st


def _f(date, odo, vol=40.0, price=6.0, station=None, full=1, missed=0):
    return {"date": date, "odometer": odo, "volume_l": vol,
            "price_per_l": price, "total_cost": round(vol * price, 2),
            "full_tank": full, "missed_previous": missed, "station": station,
            "id": odo}


FILLUPS = [
    _f("2026-01-10 10:00", 10000, vol=40, price=6.20, station="Orlen A"),
    _f("2026-02-10 10:00", 10700, vol=42, price=6.00, station="BP B"),
    _f("2026-03-10 10:00", 11400, vol=41, price=5.80, station="Orlen A"),
    _f("2026-04-10 10:00", 12100, vol=43, price=6.50, station="Shell C"),
]


def test_estimated_range():
    assert st.estimated_range_km(6.0, 66.0) == 1100
    assert st.estimated_range_km(None, 66.0) is None
    assert st.estimated_range_km(6.0, 0) is None


def test_ytd_fuel_cost():
    assert st.ytd_fuel_cost(FILLUPS, "2026") == round(
        40 * 6.20 + 42 * 6.00 + 41 * 5.80 + 43 * 6.50, 2)
    assert st.ytd_fuel_cost(FILLUPS, "2025") == 0.0


def test_month_forecast_cost():
    # 248 PLN wydane do 10. dnia kwietnia (30 dni) → 248/10*30
    fills = [_f("2026-04-05 10:00", 100, vol=40, price=6.20)]
    now = datetime(2026, 4, 10)
    assert st.month_forecast_cost(fills, now) == round(248.0 / 10 * 30, 2)
    assert st.month_forecast_cost([], now) is None


def test_projected_annual_km():
    # 2100 km w 90 dni → ~8517 km/rok
    assert st.projected_annual_km(FILLUPS) == round(2100 / 90 * 365)
    assert st.projected_annual_km(FILLUPS[:1]) is None
    short = [_f("2026-01-01 10:00", 100), _f("2026-01-20 10:00", 600)]
    assert st.projected_annual_km(short) is None  # < 30 dni historii


def test_station_ranking_and_best():
    ranking = st.station_ranking(FILLUPS)
    assert ranking[0]["station"] == "Orlen A"
    assert ranking[0]["visits"] == 2
    assert ranking[0]["avg_price"] == round(
        (40 * 6.20 + 41 * 5.80) / 81, 2)
    # best: jedyna stacja z min. 2 wizytami
    assert st.best_station(FILLUPS) == "Orlen A"
    assert st.best_station(FILLUPS, min_visits=5) is None


def test_record_entries():
    r = st.record_entries(FILLUPS)
    # 3 segmenty po 700 km; spalanie = vol/7
    assert r["best_consumption"]["l_per_100km"] == round(41 / 7, 2)
    assert r["worst_consumption"]["l_per_100km"] == round(43 / 7, 2)
    assert r["longest_segment"]["distance_km"] == 700
    assert r["cheapest_fillup"]["price_per_l"] == 5.80
    assert r["most_expensive_fillup"]["station"] == "Shell C"
    empty = st.record_entries([])
    assert empty["best_consumption"] is None


def test_monthly_km():
    km = st.monthly_km(FILLUPS)
    assert km == [{"month": "2026-02", "km": 700},
                  {"month": "2026-03", "km": 700},
                  {"month": "2026-04", "km": 700}]


# ── Leasing per auto (0.8.0) ──────────────────────────────────────────────

def test_lease_km_margin_matches_odo_vs_budget_formula():
    """Ta sama krzywa co sensor.odo_vs_budget (template.yaml:632-643)."""
    now = datetime(2026, 7, 10)
    start = datetime(2024, 10, 10)
    end = datetime(2028, 10, 9)
    limit = 90000
    odometer = 50000
    expected = limit * ((now - start).total_seconds()
                       / (end - start).total_seconds()) - odometer
    result = st.lease_km_margin(limit, "2024-10-10", "2028-10-09", odometer, now)
    assert result == round(expected, 1)


def test_lease_km_margin_none_when_lease_fields_missing():
    assert st.lease_km_margin(None, None, None, 1000, datetime(2026, 1, 1)) is None


def test_lease_km_margin_none_when_odometer_missing():
    assert st.lease_km_margin(
        90000, "2024-10-10", "2028-10-09", None, datetime(2026, 1, 1)) is None


def test_lease_depletion_date_none_without_annual_km():
    assert st.lease_depletion_date(90000, 50000, None, datetime(2026, 1, 1)) is None


def test_lease_depletion_date_projects_forward():
    now = datetime(2026, 1, 1)
    result = st.lease_depletion_date(90000, 80000, 20000, now)
    expected = (now + timedelta(days=10000 / 20000 * 365)).strftime("%Y-%m-%d")
    assert result == expected


def test_lease_depletion_date_today_when_limit_already_exceeded():
    now = datetime(2026, 1, 1)
    assert st.lease_depletion_date(90000, 95000, 20000, now) == now.strftime("%Y-%m-%d")
