"""Importer Drivvo — mapowanie pól pt-BR, fallback volume, dedup z CSV."""
from unittest.mock import MagicMock

from fuel_tracker import importer_drivvo as imp, queries


def make_client(refuellings=(), services=(), expenses=()):
    client = MagicMock(spec=imp.DrivvoClient)
    client.refuellings.return_value = list(refuellings)
    client.services.return_value = list(services)
    client.expenses.return_value = list(expenses)
    return client


def test_expenses_and_services_mapped(conn, vehicle_id):
    client = make_client(
        services=[{"id": 1, "data": "2025-01-15 10:00:00", "odometro": 1200,
                   "valor_total": 300.0, "descricao": "wymiana oleju",
                   "tipo_servico": "Serwis"}],
        expenses=[{"id": 2, "data": "2025-02-01 09:00:00", "odometro": 1500,
                   "valor_total": 45.5, "descricao": None,
                   "tipo_despesa": "Estacionamento"}],
    )
    report = imp.import_expenses(conn, vehicle_id, client, 123)
    assert report.expenses_added == 2
    rows = queries.fetch_expenses(conn, vehicle_id)
    by_cost = {r["cost"]: r for r in rows}
    assert by_cost[300.0]["category"] == "Serwis"
    assert by_cost[300.0]["date"] == "2025-01-15 10:00"
    assert by_cost[45.5]["category"] == "Inne"  # nieznany typ → Inne


def test_expenses_idempotent(conn, vehicle_id):
    client = make_client(expenses=[{
        "id": 2, "data": "2025-02-01 09:00:00", "odometro": 1500,
        "valor_total": 45.5, "descricao": "parking", "tipo_despesa": "Parking"}])
    imp.import_expenses(conn, vehicle_id, client, 123)
    report2 = imp.import_expenses(conn, vehicle_id, client, 123)
    assert report2.expenses_added == 0
    assert report2.expenses_skipped == 1


def test_refuelling_volume_fallback_and_dedup(conn, vehicle_id):
    # Wpis już zaimportowany z Fuelio CSV (data ucięta do minut + ten sam odometr).
    conn.execute(
        """INSERT INTO fillups (vehicle_id, date, odometer, volume_l,
           price_per_l, total_cost, full_tank, source)
           VALUES (?, '2025-01-01 12:00', 1000, 40.0, 6.0, 240.0, 1, 'fuelio_csv')""",
        (vehicle_id,))
    conn.commit()
    client = make_client(refuellings=[
        {"id": 10, "data": "2025-01-01 12:00:33", "odometro": 1000,
         "volume": 0, "preco": 6.0, "valor_total": 240.0, "tanque_cheio": 1,
         "posto_combustivel": {"nome": "Stacja A"}},
        {"id": 11, "data": "2025-03-05 08:00:00", "odometro": 2500,
         "volume": 0, "preco": 5.0, "valor_total": 100.0, "tanque_cheio": 0,
         "posto_combustivel": None},
    ])
    report = imp.import_refuellings(conn, vehicle_id, client, 123)
    assert report.fillups_skipped == 1   # dedup po (date, odometer)
    assert report.fillups_added == 1
    row = conn.execute(
        "SELECT * FROM fillups WHERE odometer = 2500").fetchone()
    assert row["volume_l"] == 20.0       # fallback valor_total/preco
    assert row["full_tank"] == 0
    assert row["source"] == "drivvo_api"


def test_expenses_nested_tipos_despesa(conn, vehicle_id):
    # Realny format web API: kwoty w tipos_despesa[].valor, id w id_despesa.
    client = make_client(expenses=[{
        "id_despesa": 5176262, "data": "2024-11-21 20:03:24", "odometro": 2723,
        "observacao": "", "tipos_despesa": [{"nome": "Płyny", "valor": 89.98}]}])
    report = imp.import_expenses(conn, vehicle_id, client, 123)
    assert report.expenses_added == 1
    row = queries.fetch_expenses(conn, vehicle_id)[0]
    assert row["cost"] == 89.98
    assert row["category"] == "Eksploatacja"      # Płyny → Eksploatacja
    assert row["description"] == "Płyny"
    assert row["source_uid"] == "5176262:0"
    assert row["date"] == "2024-11-21 20:03"


def test_expenses_dedup_by_odometer_and_cost(conn, vehicle_id):
    # Wpis z Fuelio CSV: data rozni sie o minute, opis wielkoscia liter.
    conn.execute(
        """INSERT INTO expenses (vehicle_id, date, odometer, category_id,
           description, cost, source) VALUES (?, '2024-11-21 20:04', 2723,
           2, 'plyny', 89.98, 'fuelio_csv')""", (vehicle_id,))
    conn.commit()
    client = make_client(expenses=[{
        "id_despesa": 5176262, "data": "2024-11-21 20:03:24", "odometro": 2723,
        "tipos_despesa": [{"nome": "Płyny", "valor": 89.98}]}])
    report = imp.import_expenses(conn, vehicle_id, client, 123)
    assert report.expenses_added == 0
    assert report.expenses_skipped == 1
