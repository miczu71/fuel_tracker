"""Flask web UI + REST API (ingress-safe: linki przez X-Ingress-Path,
fetch-e w JS wyłącznie względne, strony bez zagnieżdżonych ścieżek)."""
from __future__ import annotations

import io
import logging
import sqlite3
from datetime import datetime
from typing import Callable, Optional

from flask import Flask, Response, g, jsonify, render_template, request

from . import csv_fuelio, db as dbm, importer_drivvo, queries, stats as st

logger = logging.getLogger(__name__)

# Sensory Drivvo do bramki weryfikacyjnej migracji.
_DRIVVO_VERIFY = {
    "count": "sensor.skoda_superb_refuelling_total",
    "cost": "sensor.skoda_superb_refuelling_value_total",
    "volume": "sensor.skoda_superb_refuelling_volume_total",
}


def create_app(db_path: str, vehicle_id: int, config: dict,
               on_data_change: Optional[Callable[[], None]] = None,
               ha_state: Optional[Callable[[str], dict | None]] = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

    def conn() -> sqlite3.Connection:
        if "db" not in g:
            g.db = dbm.get_conn(db_path)
        return g.db

    @app.teardown_appcontext
    def close_db(_exc) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def changed() -> None:
        if on_data_change:
            try:
                on_data_change()
            except Exception:
                logger.exception("Callback po zmianie danych nieudany")

    def base() -> str:
        return request.headers.get("X-Ingress-Path", "")

    # ── Strony ────────────────────────────────────────────────────────────

    @app.get("/")
    def page_dashboard():
        return render_template("dashboard.html", base=base(),
                               vehicle=config.get("vehicle_name", ""))

    @app.get("/fillups")
    def page_fillups():
        return render_template("fillups.html", base=base(),
                               vehicle=config.get("vehicle_name", ""))

    @app.get("/fillup-form")
    def page_fillup_form():
        return render_template("fillup_form.html", base=base(),
                               vehicle=config.get("vehicle_name", ""),
                               edit_id=request.args.get("id", ""))

    @app.get("/expenses")
    def page_expenses():
        return render_template("expenses.html", base=base(),
                               vehicle=config.get("vehicle_name", ""))

    @app.get("/settings")
    def page_settings():
        return render_template("settings.html", base=base(),
                               vehicle=config.get("vehicle_name", ""))

    # ── API: podsumowanie / wykresy ───────────────────────────────────────

    @app.get("/api/summary")
    def api_summary():
        return jsonify(queries.summary(conn(), vehicle_id,
                                       config.get("monthly_budget", 0.0)))

    # ── API: tankowania ───────────────────────────────────────────────────

    @app.get("/api/fillups")
    def api_fillups():
        rows = queries.fetch_fillups(conn(), vehicle_id, include_drafts=True)
        cons = st.segment_consumption_by_fillup(
            [r for r in rows if not r["draft"]])
        for r in rows:
            r["consumption"] = cons.get(r["id"])
        return jsonify(rows)

    @app.get("/api/fillups/<int:fid>")
    def api_fillup_get(fid: int):
        row = conn().execute(
            "SELECT * FROM fillups WHERE id = ? AND vehicle_id = ?",
            (fid, vehicle_id)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify(dict(row))

    def _fillup_fields(data: dict) -> dict:
        volume = float(data.get("volume_l") or 0)
        price = float(data.get("price_per_l") or 0)
        total = float(data.get("total_cost") or 0)
        # Dowolne 2 z 3 pól wyznaczają trzecie.
        if volume and price and not total:
            total = round(volume * price, 2)
        elif volume and total and not price:
            price = round(total / volume, 3)
        elif price and total and not volume:
            volume = round(total / price, 3)
        return {
            "date": (data.get("date") or "").replace("T", " ")[:16],
            "odometer": int(data.get("odometer") or 0),
            "volume_l": volume, "price_per_l": price, "total_cost": total,
            "full_tank": 1 if data.get("full_tank") in (1, "1", True, "true", "on") else 0,
            "missed_previous": 1 if data.get("missed_previous") in (1, "1", True, "true", "on") else 0,
            "fuel_type": data.get("fuel_type") or config.get("default_fuel_type", "PB95"),
            "station": (data.get("station") or "").strip() or None,
            "notes": (data.get("notes") or "").strip() or None,
        }

    @app.post("/api/fillups")
    def api_fillup_add():
        f = _fillup_fields(request.get_json(force=True))
        if not f["date"] or not f["odometer"] or f["volume_l"] <= 0:
            return jsonify({"error": "Wymagane: data, przebieg, litry"}), 400
        try:
            cur = conn().execute(
                """INSERT INTO fillups (vehicle_id, date, odometer, volume_l,
                   price_per_l, total_cost, full_tank, missed_previous,
                   fuel_type, station, notes, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'manual')""",
                (vehicle_id, f["date"], f["odometer"], f["volume_l"],
                 f["price_per_l"], f["total_cost"], f["full_tank"],
                 f["missed_previous"], f["fuel_type"], f["station"], f["notes"]))
            conn().commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "Wpis o tej dacie i przebiegu już istnieje"}), 409
        changed()
        return jsonify({"id": cur.lastrowid}), 201

    @app.put("/api/fillups/<int:fid>")
    def api_fillup_update(fid: int):
        f = _fillup_fields(request.get_json(force=True))
        cur = conn().execute(
            """UPDATE fillups SET date=?, odometer=?, volume_l=?, price_per_l=?,
               total_cost=?, full_tank=?, missed_previous=?, fuel_type=?,
               station=?, notes=?, draft=0
               WHERE id=? AND vehicle_id=?""",
            (f["date"], f["odometer"], f["volume_l"], f["price_per_l"],
             f["total_cost"], f["full_tank"], f["missed_previous"],
             f["fuel_type"], f["station"], f["notes"], fid, vehicle_id))
        conn().commit()
        if not cur.rowcount:
            return jsonify({"error": "not found"}), 404
        changed()
        return jsonify({"ok": True})

    @app.delete("/api/fillups/<int:fid>")
    def api_fillup_delete(fid: int):
        cur = conn().execute(
            "DELETE FROM fillups WHERE id = ? AND vehicle_id = ?",
            (fid, vehicle_id))
        conn().commit()
        if not cur.rowcount:
            return jsonify({"error": "not found"}), 404
        changed()
        return jsonify({"ok": True})

    @app.get("/api/prefill")
    def api_prefill():
        """Prefill formularza: odometr z myskoda, ostatnia stacja i cena."""
        odometer = None
        if ha_state and config.get("odometer_entity"):
            data = ha_state(config["odometer_entity"])
            try:
                odometer = int(float(data["state"])) if data else None
            except (KeyError, TypeError, ValueError):
                odometer = None
        last = conn().execute(
            "SELECT station, price_per_l FROM fillups WHERE vehicle_id = ? "
            "AND draft = 0 ORDER BY odometer DESC LIMIT 1",
            (vehicle_id,)).fetchone()
        return jsonify({
            "date": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "odometer": odometer,
            "station": last["station"] if last else None,
            "price_per_l": last["price_per_l"] if last else None,
            "fuel_type": config.get("default_fuel_type", "PB95"),
        })

    # ── API: wydatki ──────────────────────────────────────────────────────

    @app.get("/api/expenses")
    def api_expenses():
        return jsonify(queries.fetch_expenses(conn(), vehicle_id))

    @app.get("/api/categories")
    def api_categories():
        rows = conn().execute(
            "SELECT id, name FROM expense_categories ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.post("/api/expenses")
    def api_expense_add():
        data = request.get_json(force=True)
        date = (data.get("date") or "").replace("T", " ")[:16]
        cost = float(data.get("cost") or 0)
        if not date or cost <= 0:
            return jsonify({"error": "Wymagane: data i kwota"}), 400
        cur = conn().execute(
            """INSERT INTO expenses (vehicle_id, date, odometer, category_id,
               description, cost, source) VALUES (?,?,?,?,?,?,'manual')""",
            (vehicle_id, date, int(data.get("odometer") or 0) or None,
             int(data.get("category_id") or 0) or dbm.category_id(conn(), None),
             (data.get("description") or "").strip() or None, cost))
        conn().commit()
        changed()
        return jsonify({"id": cur.lastrowid}), 201

    @app.delete("/api/expenses/<int:eid>")
    def api_expense_delete(eid: int):
        cur = conn().execute(
            "DELETE FROM expenses WHERE id = ? AND vehicle_id = ?",
            (eid, vehicle_id))
        conn().commit()
        if not cur.rowcount:
            return jsonify({"error": "not found"}), 404
        changed()
        return jsonify({"ok": True})

    # ── API: import / eksport / weryfikacja ───────────────────────────────

    @app.post("/api/import/csv")
    def api_import_csv():
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "Brak pliku"}), 400
        text = file.read().decode("utf-8-sig", errors="replace")
        report = csv_fuelio.import_into(
            conn(), vehicle_id, text, config.get("default_fuel_type", "PB95"))
        changed()
        return jsonify(report.as_dict())

    @app.post("/api/import/drivvo")
    def api_import_drivvo():
        body = request.get_json(silent=True) or {}
        # Dane logowania z body maja priorytet nad opcjami add-onu — jednorazowy
        # import nie wymaga zapisywania hasla w konfiguracji Supervisora.
        email = body.get("email") or config.get("drivvo_email")
        password = body.get("password") or config.get("drivvo_password")
        if not email or not password:
            return jsonify({"error": "Podaj email/password w żądaniu albo "
                                     "drivvo_email/drivvo_password w opcjach add-onu"}), 400
        try:
            result = importer_drivvo.run_import(
                conn(), vehicle_id, email, password,
                int(body.get("vehicle_id") or config.get("drivvo_vehicle_id", 0) or 0),
                config.get("default_fuel_type", "PB95"),
                include_refuellings=bool(body.get("include_refuellings")))
        except importer_drivvo.DrivvoError as exc:
            return jsonify({"error": str(exc)}), 502
        except Exception as exc:
            logger.exception("Import z Drivvo nieudany")
            return jsonify({"error": f"Import nieudany: {exc}"}), 502
        changed()
        return jsonify(result)

    @app.get("/api/verify")
    def api_verify():
        """Bramka migracji: sumy w bazie vs żywe sensory Drivvo w HA."""
        fillups = queries.fetch_fillups(conn(), vehicle_id)
        s = st.compute_stats(fillups)
        local = {"count": s.fillup_count, "cost": s.total_cost,
                 "volume": s.total_volume_l}
        remote: dict = {}
        if ha_state:
            for key, entity in _DRIVVO_VERIFY.items():
                data = ha_state(entity)
                try:
                    remote[key] = float(data["state"]) if data else None
                except (KeyError, TypeError, ValueError):
                    remote[key] = None
        checks = {}
        for key in local:
            r = remote.get(key)
            checks[key] = {
                "local": local[key], "drivvo": r,
                "match": r is not None and abs(local[key] - r) < 0.51,
            }
        return jsonify({"checks": checks,
                        "all_match": all(c["match"] for c in checks.values())})

    @app.get("/api/export/fuelio.csv")
    def api_export():
        data = csv_fuelio.export_csv(conn(), vehicle_id)
        return Response(
            data, mimetype="text/csv",
            headers={"Content-Disposition":
                     "attachment; filename=fuelio_export.csv"})

    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True})

    return app
