"""Punkt startowy add-onu: migracje, auto-import CSV, MQTT, scheduler, Flask."""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from . import __version__, csv_fuelio, db as dbm, ha_client, queries
from .publisher import MQTTPublisher
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


def backup_db(db_path: str, share_dir: str, keep: int = 7) -> None:
    backups = Path(share_dir) / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    target = backups / f"fuel_tracker-{datetime.now():%Y%m%d}.db"
    conn = dbm.get_conn(db_path)
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
        logger.info("Backup bazy: %s", target)
    except Exception:
        logger.exception("Backup nieudany")
    finally:
        conn.close()
    old = sorted(backups.glob("fuel_tracker-*.db"))[:-keep]
    for f in old:
        f.unlink(missing_ok=True)


def main() -> None:
    _setup_logging()
    logger.info("Fuel Tracker %s startuje", __version__)

    db_path = _env("DB_PATH", "/data/fuel_tracker.db")
    share_dir = _env("BACKUP_SHARE", "/share/fuel_tracker")
    default_fuel = _env("DEFAULT_FUEL_TYPE", "PB95")
    budget = float(_env("MONTHLY_FUEL_BUDGET", "0") or 0)
    vehicle_name = _env("VEHICLE_NAME", "Skoda Superb")

    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    vehicle_id = dbm.ensure_vehicle(
        conn, vehicle_name, float(_env("TANK_CAPACITY_L", "66") or 66),
        default_fuel)
    conn.close()

    auto_import_share(db_path, vehicle_id, share_dir, default_fuel)

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
        device_name="Superb Fuel",
        version=__version__,
    )
    mqtt_pub.connect()

    def publish_sensors() -> None:
        c = dbm.get_conn(db_path)
        try:
            mqtt_pub.publish(queries.sensor_values(c, vehicle_id, budget))
        except Exception:
            logger.exception("Publikacja MQTT nieudana")
        finally:
            c.close()

    scheduler = BackgroundScheduler(timezone=_env("TZ", "Europe/Warsaw"))
    scheduler.add_job(publish_sensors, "interval", minutes=15,
                      next_run_time=datetime.now())
    scheduler.add_job(backup_db, "cron", hour=3, minute=15,
                      args=[db_path, share_dir])
    scheduler.start()

    app = create_app(
        db_path=db_path,
        vehicle_id=vehicle_id,
        config={
            "monthly_budget": budget,
            "default_fuel_type": default_fuel,
            "odometer_entity": _env("ODOMETER_ENTITY"),
            "fuel_level_entity": _env("FUEL_LEVEL_ENTITY"),
            "location_entity": _env("LOCATION_ENTITY"),
            "drivvo_email": _env("DRIVVO_EMAIL"),
            "drivvo_password": _env("DRIVVO_PASSWORD"),
            "drivvo_vehicle_id": int(_env("DRIVVO_VEHICLE_ID", "0") or 0),
            "vehicle_name": vehicle_name,
        },
        on_data_change=publish_sensors,
        ha_state=ha_client.get_state,
    )
    logger.info("Web UI nasłuchuje na :8098 (ingress)")
    app.run(host="0.0.0.0", port=8098, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
