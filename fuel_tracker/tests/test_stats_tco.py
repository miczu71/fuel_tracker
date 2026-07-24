"""Silnik TCO (koszt posiadania, 0.13.0): stats.tco_breakdown."""
from fuel_tracker import stats as st


def _f(date, odo, vol=40.0, price=6.0, station=None, full=1, missed=0):
    return {"date": date, "odometer": odo, "volume_l": vol,
            "price_per_l": price, "total_cost": round(vol * price, 2),
            "full_tank": full, "missed_previous": missed, "station": station,
            "id": odo}


def _e(date, cost, tco_group):
    return {"date": date, "cost": cost, "tco_group": tco_group}


# Ta sama historia co test_stats_extended.FILLUPS: 2100 km / 90 dni
# (2026 nie jest przestępny) → period_months = 3.0 dokładnie.
FILLUPS = [
    _f("2026-01-10 10:00", 10000, vol=40, price=6.20),   # 248.00
    _f("2026-02-10 10:00", 10700, vol=42, price=6.00),   # 252.00
    _f("2026-03-10 10:00", 11400, vol=41, price=5.80),   # 237.80
    _f("2026-04-10 10:00", 12100, vol=43, price=6.50),   # 279.50
]
# fuel_total = 1017.30, distance_km = 2100

EXPENSES = [
    _e("2026-01-15 09:00", 200.0, "fluids"),
    _e("2026-02-20 09:00", 500.0, "service"),
    _e("2026-03-05 09:00", 150.0, "insurance"),
]
# expenses_total = 850.0


def test_tco_full_breakdown_with_lease():
    r = st.tco_breakdown(FILLUPS, EXPENSES, monthly_rate=1000.0)
    assert r["period_months"] == 3.0
    assert r["distance_km"] == 2100
    assert r["fuel_total"] == 1017.3
    assert r["expenses_total"] == 850.0
    assert r["by_group"] == {"fluids": 200.0, "service": 500.0,
                             "insurance": 150.0, "fees": 0.0, "other": 0.0}
    assert r["lease_total"] == 3000.0
    assert r["grand_total"] == 4867.3
    assert r["cost_per_km"] == {
        "fuel": round(1017.3 / 2100, 4),
        "expenses": round(850.0 / 2100, 4),
        "lease": round(3000.0 / 2100, 4),
        "total": round(4867.3 / 2100, 4),
    }
    assert r["cost_per_month"] == round(4867.3 / 3.0, 2)
    assert r["cost_per_100km"] == round(100 * 4867.3 / 2100, 2)


def test_tco_without_monthly_rate_omits_lease():
    r = st.tco_breakdown(FILLUPS, EXPENSES, monthly_rate=None)
    assert r["lease_total"] is None
    assert r["cost_per_km"]["lease"] is None
    assert r["grand_total"] == round(1017.3 + 850.0, 2)


def test_tco_zero_monthly_rate_treated_as_no_lease():
    r = st.tco_breakdown(FILLUPS, EXPENSES, monthly_rate=0)
    assert r["lease_total"] is None


def test_tco_zero_value_group_is_zero_not_none_per_km():
    # Grupa bez wydatków w tym okresie: 0.0 PLN, ale koszt/km policzalny
    # (dystans > 0) — 0.0, nie None (rozróżnienie "brak danych" vs "zero").
    r = st.tco_breakdown(FILLUPS, [], monthly_rate=None)
    assert r["expenses_total"] == 0.0
    assert r["cost_per_km"]["expenses"] == 0.0


def test_tco_unknown_tco_group_bucketed_as_other():
    expenses = [_e("2026-02-01 09:00", 77.0, "nieznana-grupa"),
                _e("2026-02-02 09:00", 23.0, None)]
    r = st.tco_breakdown(FILLUPS, expenses, monthly_rate=None)
    assert r["by_group"]["other"] == 100.0


def test_tco_empty_history_returns_safe_defaults():
    r = st.tco_breakdown([], [], monthly_rate=1000.0)
    assert r["period_months"] is None
    assert r["distance_km"] == 0
    assert r["fuel_total"] == 0.0
    assert r["expenses_total"] == 0.0
    assert r["lease_total"] is None
    assert r["grand_total"] == 0.0
    assert r["cost_per_km"] == {"fuel": None, "expenses": None,
                               "lease": None, "total": None}
    assert r["cost_per_month"] is None
    assert r["cost_per_100km"] is None


def test_tco_single_fillup_zero_distance_guards_division():
    r = st.tco_breakdown([FILLUPS[0]], [], monthly_rate=1000.0)
    assert r["distance_km"] == 0
    assert r["fuel_total"] == 248.0
    assert r["cost_per_km"] == {"fuel": None, "expenses": None,
                               "lease": None, "total": None}
    assert r["cost_per_100km"] is None
    # Jeden wpis dat = brak rozpiętości → okres nieznany, rata pominięta.
    assert r["period_months"] is None
    assert r["lease_total"] is None
