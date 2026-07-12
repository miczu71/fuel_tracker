"""Punkt startowy add-onu: migracje, auto-import CSV, MQTT, scheduler, Flask."""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from . import __version__, backup, csv_fuelio, db as dbm, ha_client, notifications
from . import prices, queries
from . import settings as settingsm
from .publisher import MQTTPublisher, device_id_for_vehicle
from .web import create_app

logger = logging.getLogger("fuel_tracker")


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, default)
    return default if v in ("", "null", "None") else v


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, _env("LOG_LEVEL", "info").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def auto_import_share(db_path: str, vehicle_id: int, share_dir: str,
                      default_fuel_type: str) -> None:
    """Importuje pliki CSV z <share>/import/ i przenosi je do <share>/imported/."""
    import_dir = Path(share_dir) / "import"
    done_dir = Path(share_dir) / "imported"
    if not import_dir.is_dir():
        return
    conn = dbm.get_conn(db_path)
    try:
        for csv_file in sorted(import_dir.glob("*.csv")):
            try:
                text = csv_file.read_text(encoding="utf-8-sig")
                report = csv_fuelio.import_into(
                    conn, vehicle_id, text, default_fuel_type)
                logger.info("Auto-import %s: %s", csv_file.name,
                            report.as_dict())
                done_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(csv_file),
                            done_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{csv_file.name}")
            except Exception:
                logger.exception("Auto-import %s nieudany", csv_file.name)
    finally:
        conn.close()


def main() -> None:
    _setup_logging()
    logger.info("Fuel Tracker %s startuje", __version__)

    db_path = _env("DB_PATH", "/data/fuel_tracker.db")
    share_dir = _env("BACKUP_SHARE", "/share/fuel_tracker")
    default_fuel = _env("DEFAULT_FUEL_TYPE", "PB95")
    budget = float(_env("MONTHLY_FUEL_BUDGET", "0") or 0)
    vehicle_name = _env("VEHICLE_NAME", "Skoda Superb")
    price_region = _env("PRICE_REGION", "dolnośląskie")
    tank_capacity = float(_env("TANK_CAPACITY_L", "66") or 66)

    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    # Encje HA i budżet są teraz per pojazd (0.11.0, migracja #9) — opcje
    # Supervisora zasilają tylko JEDYNY pojazd świeżej instalacji; przy
    # upgrade'zie te wartości już siedzą w bazie (backfill migracji), więc
    # ensure_vehicle() i tak zwraca istniejący pojazd bez ich nadpisania.
    dbm.ensure_vehicle(
        conn, vehicle_name, tank_capacity, default_fuel,
        odometer_entity=_env("ODOMETER_ENTITY") or None,
        fuel_level_entity=_env("FUEL_LEVEL_ENTITY") or None,
        location_entity=_env("LOCATION_ENTITY") or None,
        monthly_fuel_budget=budget)
    settingsm.seed_from_options(conn, {
        "price_region": price_region,
        "notify_service": _env("NOTIFY_SERVICE"),
    })
    # Pojazdy: cykl życia (0.8.0) — aktywny pojazd żyje w settings, nie jest
    # już zamrożony na jednym id; startowa rozdzielczość tylko dla
    # jednorazowego auto-importu CSV przy starcie (aplikacja jeszcze nie
    # przyjmuje żądań, więc nie ma czego przełączać) i nazwy urządzenia MQTT
    # domyślnego (thin wrapper publish() — pętla multi-vehicle poniżej i tak
    # woła publish_for_vehicle() z właściwą nazwą każdego auta).
    active_id = dbm.resolve_active_vehicle_id(
        conn, int(settingsm.get_settings(conn).get("active_vehicle_id") or 0))
    active_vehicle = dbm.get_vehicle(conn, active_id) if active_id else None
    active_vehicle_name = active_vehicle["name"] if active_vehicle else vehicle_name
    conn.close()

    auto_import_share(db_path, active_id, share_dir, default_fuel)

    mqtt_host = _env("MQTT_HOST", "core-mosquitto")
    mqtt_port = int(_env("MQTT_PORT", "1883") or 1883)
    mqtt_user = _env("MQTT_USER")
    mqtt_password = _env("MQTT_PASSWORD")
    if not mqtt_user:
        svc = ha_client.get_mqtt_service()
        if svc:
            mqtt_host = svc.get("host") or mqtt_host
            mqtt_port = int(svc.get("port") or mqtt_port)
            mqtt_user = svc.get("username") or ""
            mqtt_password = svc.get("password") or ""
            logger.info("MQTT: dane brokera z usługi Supervisora (%s)", mqtt_host)

    mqtt_pub = MQTTPublisher(
        host=mqtt_host,
        port=mqtt_port,
        user=mqtt_user,
        password=mqtt_password,
        device_name=active_vehicle_name,
        version=__version__,
    )
    mqtt_pub.connect()

    def _current_odometer(vehicle: dict) -> int | None:
        entity = vehicle.get("odometer_entity")
        if not entity:
            return None
        data = ha_client.get_state(entity)
        try:
            return int(float(data["state"])) if data else None
        except (KeyError, TypeError, ValueError):
            return None

    def publish_sensors() -> None:
        """Pełny multi-vehicle (0.11.0): publikuje sensory MQTT i ewaluuje
        alerty dla KAŻDEGO nie-zarchiwizowanego pojazdu, nie tylko aktywnego.
        Błąd jednego auta (np. martwa encja HA) nie może zablokować reszty —
        stąd try/except w pętli, a nie wokół całej funkcji."""
        c = dbm.get_conn(db_path)
        try:
            s = settingsm.get_settings(c)
            active_id = dbm.resolve_active_vehicle_id(
                c, int(s.get("active_vehicle_id") or 0))
            for vehicle in dbm.list_vehicles(c, include_archived=False):
                try:
                    values = queries.sensor_values(
                        c, vehicle["id"], vehicle["monthly_fuel_budget"],
                        fuel_type=vehicle["fuel_type"],
                        price_region=s["price_region"],
                        tank_capacity_l=vehicle["tank_capacity_l"],
                        lease_km_limit=vehicle["lease_km_limit"],
                        lease_start=vehicle["lease_start"],
                        lease_end=vehicle["lease_end"],
                        current_odometer=_current_odometer(vehicle))
                    device_id = device_id_for_vehicle(vehicle["id"], active_id)
                    mqtt_pub.publish_for_vehicle(device_id, vehicle["name"], values)
                except Exception:
                    logger.exception(
                        "Publikacja MQTT nieudana dla pojazdu %s", vehicle["id"])
                    continue
                try:
                    # Alerty liczone z tych samych wartości co sensory MQTT;
                    # błąd powiadomień nie może zepsuć publikacji tego auta
                    # ani przerwać pętli po pozostałych pojazdach.
                    notifications.evaluate(c, s, values, vehicle["id"],
                                          ha_client.notify)
                except Exception:
                    logger.exception(
                        "Ewaluacja alertów nieudana dla pojazdu %s", vehicle["id"])
        except Exception:
            logger.exception("Publikacja MQTT nieudana")
        finally:
            c.close()

    def refresh_prices() -> None:
        c = dbm.get_conn(db_path)
        try:
            region = settingsm.get_settings(c)["price_region"]
            if prices.refresh(c, region):
                publish_sensors()
        except Exception:
            logger.exception("Odświeżenie cen paliw nieudane")
        finally:
            c.close()

    scheduler = BackgroundScheduler(timezone=_env("TZ", "Europe/Warsaw"))
    scheduler.add_job(publish_sensors, "interval", minutes=15,
                      next_run_time=datetime.now())
    scheduler.add_job(refresh_prices, "interval", hours=6,
                      next_run_time=datetime.now())
    scheduler.add_job(backup.nightly_backup, "cron", hour=3, minute=15,
                      args=[db_path, share_dir])
    scheduler.start()

    app = create_app(
        db_path=db_path,
        config={
            # monthly_budget/default_fuel_type/odometer_entity/fuel_level_entity/
            # location_entity/vehicle_name/price_region/tank_capacity_l/aktywny
            # pojazd żyją teraz w tabeli settings/vehicles (0.7.0/0.8.0) —
            # web.py czyta je świeżo per request zamiast z tego zamrożonego dict-a.
            "drivvo_email": _env("DRIVVO_EMAIL"),
            "drivvo_password": _env("DRIVVO_PASSWORD"),
            "drivvo_vehicle_id": int(_env("DRIVVO_VEHICLE_ID", "0") or 0),
            "odo_budget_entity": _env("ODO_BUDGET_ENTITY"),
            "share_dir": share_dir,
        },
        on_data_change=publish_sensors,
        ha_state=ha_client.get_state,
        ha_services=ha_client.list_services,
    )
    logger.info("Web UI nasłuchuje na :8098 (ingress)")
    app.run(host="0.0.0.0", port=8098, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
