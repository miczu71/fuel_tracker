"""Matematyka segmentów spalania (jak Fuelio)."""
from fuel_tracker import stats as st


def F(id, odo, vol, full=1, missed=0, date=None, cost=0.0):
    return {
        "id": id, "date": date or f"2025-01-{id:02d} 10:00", "odometer": odo,
        "volume_l": vol, "total_cost": cost, "price_per_l": 6.0,
        "full_tank": full, "missed_previous": missed, "draft": 0,
        "station": None,
    }


def test_simple_full_to_full():
    # 636 km na 52.47 L → 8.25 (przypadek z realnego eksportu)
    fillups = [F(1, 30826, 12.64), F(2, 31462, 52.47)]
    segs = st.build_segments(sorted(fillups, key=lambda f: f["odometer"]))
    assert len(segs) == 1
    assert round(segs[0].l_per_100km, 2) == 8.25


def test_partials_between_fulls():
    # Pełny @1000, partial 10L @1300, pełny 20L @1500:
    # segment 1000→1500, wolumen 30 L → 6.0 L/100km
    fillups = [F(1, 1000, 40), F(2, 1300, 10, full=0), F(3, 1500, 20)]
    segs = st.build_segments(fillups)
    assert len(segs) == 1
    assert segs[0].distance_km == 500
    assert segs[0].volume_l == 30
    assert segs[0].l_per_100km == 6.0


def test_missed_breaks_chain():
    fillups = [F(1, 1000, 40), F(2, 1500, 30, missed=1), F(3, 2000, 35)]
    segs = st.build_segments(fillups)
    # 1000→1500 niewiarygodny (missed); 1500→2000 OK (missed był pełny)
    assert len(segs) == 1
    assert segs[0].start_odo == 1500
    assert segs[0].end_odo == 2000
    assert segs[0].volume_l == 35


def test_missed_partial_resets_anchor():
    fillups = [F(1, 1000, 40), F(2, 1500, 10, full=0, missed=1), F(3, 2000, 35)]
    # Partial z missed → brak kotwicy pełnego baku; kolejny pełny tylko otwiera.
    assert st.build_segments(fillups) == []


def test_leading_partials_ignored():
    fillups = [F(1, 100, 10, full=0), F(2, 300, 30), F(3, 700, 28)]
    segs = st.build_segments(fillups)
    assert len(segs) == 1
    assert segs[0].start_odo == 300


def test_overall_avg_is_vol_over_dist():
    # Dwa segmenty: 500km/30L i 500km/40L → (70/1000)*100 = 7.0
    fillups = [F(1, 1000, 40), F(2, 1500, 30), F(3, 2000, 40)]
    s = st.compute_stats(fillups)
    assert s.avg_consumption == 7.0
    assert s.last_consumption == 8.0


def test_totals_and_cost_per_km():
    fillups = [F(1, 1000, 40, cost=240.0), F(2, 1500, 30, cost=180.0)]
    s = st.compute_stats(fillups)
    assert s.total_cost == 420.0
    assert s.total_volume_l == 70.0
    assert s.total_distance_km == 500
    assert s.cost_per_km == 0.84
    assert s.fillup_count == 2


def test_empty_and_single():
    assert st.compute_stats([]).fillup_count == 0
    s = st.compute_stats([F(1, 1000, 40, cost=240)])
    assert s.avg_consumption is None
    assert s.cost_per_km is None


def test_zero_distance_guard():
    # Dwa wpisy z tym samym przebiegiem nie mogą dać dzielenia przez zero.
    fillups = [F(1, 1000, 40), F(2, 1000, 5, date="2025-01-02 11:00")]
    s = st.compute_stats(fillups)
    assert s.avg_consumption is None


def test_monthly_series():
    fillups = [F(1, 1000, 40, cost=240, date="2025-01-05 10:00"),
               F(2, 1500, 30, cost=180, date="2025-02-05 10:00")]
    expenses = [{"date": "2025-01-20 12:00", "cost": 89.98}]
    series = st.monthly_series(fillups, expenses)
    assert series[0] == {"month": "2025-01", "fuel": 240.0, "fuel_own": 0.0,
                         "expenses": 89.98, "volume": 40.0}
    assert series[1]["fuel"] == 180.0
    assert st.month_fuel_spend(fillups, "2025-02") == 180.0


def test_monthly_series_splits_private_fuel():
    # Tankowanie paid_by=own ląduje w fuel_own, nie w fuel (karta).
    own = F(2, 1500, 30, cost=180, date="2025-01-15 10:00")
    own["paid_by"] = "own"
    fillups = [F(1, 1000, 40, cost=240, date="2025-01-05 10:00"), own]
    series = st.monthly_series(fillups, [])
    assert series[0]["fuel"] == 240.0
    assert series[0]["fuel_own"] == 180.0
    assert series[0]["volume"] == 70.0
    # month_fuel_spend (budżet) nadal liczy całość paliwa
    assert st.month_fuel_spend(fillups, "2025-01") == 420.0


def test_segment_consumption_by_fillup():
    fillups = [F(1, 1000, 40), F(2, 1300, 10, full=0), F(3, 1500, 20)]
    m = st.segment_consumption_by_fillup(fillups)
    assert m == {3: 6.0}
