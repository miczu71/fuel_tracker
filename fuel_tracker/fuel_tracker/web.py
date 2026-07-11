"""Flask web UI + REST API (ingress-safe: linki przez X-Ingress-Path,
fetch-e w JS wyłącznie względne, strony bez zagnieżdżonych ścieżek)."""
from __future__ import annotations

import io
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from flask import (Flask, Response, g, jsonify, render_template, request,
                   send_from_directory)

from . import (backup as bkp, csv_fuelio, currency as cur_mod, db as dbm,
               importer_drivvo, prices as pr, queries, receipts,
               stations as stn, stats as st)
from . import __version__
from . import settings as settingsm

logger = logging.getLogger(__name__)

# Sensory Drivvo do bramki weryfikacyjnej migracji.
_DRIVVO_VERIFY = {
    "count": "sensor.skoda_superb_refuelling_total",
    "cost": "sensor.skoda_superb_refuelling_value_total",
    "volume": "sensor.skoda_superb_refuelling_volume_total",
}

def create_app(db_path: str, config: dict,
               on_data_change: Optional[Callable[[], None]] = None,
               ha_state: Optional[Callable[[str], dict | None]] = None,
               ha_services: Optional[Callable[[], list[str]]] = None) -> Flask:
    app = Flask(__name__)
    # 16 MB — zdjęcia paragonów z aparatu telefonu miewają 5–10 MB.
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
    share_dir = config.get("share_dir") or "/share/fuel_tracker"
    attach_dir = Path(share_dir) / "attachments"

    @app.context_processor
    def inject_version():
        # Cache-busting statyk (app.js/app.css) — telefony/WebView potrafią
        # trzymać stary JS z cache po aktualizacji add-onu (patrz CHANGELOG 0.4.2).
        return {"version": __version__}

    @app.after_request
    def cache_headers(resp):
        # WebView Androida (HA Companion) cache'uje na dysku HTML bez nagłówków
        # i serwuje go bez rewalidacji, przez co ?v= nigdy nie dociera do klienta
        # (patrz CHANGELOG 0.4.4). Statyki są bezpieczne z długim cache dzięki
        # stemplowaniu ?v=<wersja> w base.html/map.html.
        if (request.path.startswith("/static/")
                or request.path.startswith("/api/attachments/")):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-store"
        return resp

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

    def live_settings() -> dict:
        return settingsm.get_settings(conn())

    def current_vehicle_id() -> int:
        configured = int(live_settings().get("active_vehicle_id") or 0)
        return dbm.resolve_active_vehicle_id(conn(), configured)

    def live_vehicle() -> dict:
        return dbm.get_vehicle(conn(), current_vehicle_id())

    def _odometer_from_ha(s: dict) -> int | None:
        if not (ha_state and s["odometer_entity"]):
            return None
        data = ha_state(s["odometer_entity"])
        try:
            return int(float(data["state"])) if data else None
        except (KeyError, TypeError, ValueError):
            return None

    def base() -> str:
        return request.headers.get("X-Ingress-Path", "")

    # ── Strony ────────────────────────────────────────────────────────────

    @app.get("/")
    def page_dashboard():
        return render_template("dashboard.html", base=base(),
                               vehicle=live_vehicle()["name"])

    @app.get("/fillups")
    def page_fillups():
        return render_template("fillups.html", base=base(),
                               vehicle=live_vehicle()["name"])

    @app.get("/fillup-form")
    def page_fillup_form():
        return render_template("fillup_form.html", base=base(),
                               vehicle=live_vehicle()["name"],
                               edit_id=request.args.get("id", ""),
                               currencies=cur_mod.CURRENCIES)

    @app.get("/map")
    def page_map():
        return render_template("map.html", base=base(),
                               vehicle=live_vehicle()["name"])

    @app.get("/expenses")
    def page_expenses():
        return render_template("expenses.html", base=base(),
                               vehicle=live_vehicle()["name"])

    @app.get("/statistics")
    def page_statistics():
        return render_template("statistics.html", base=base(),
                               vehicle=live_vehicle()["name"])

    @app.get("/settings")
    def page_settings():
        return render_template("settings.html", base=base(),
                               vehicle=live_vehicle()["name"])

    @app.get("/manifest.webmanifest")
    def page_manifest():
        # PWA (0.10.0) — musi być szablonem Jinja, nie statycznym plikiem:
        # start_url/scope zależą od X-Ingress-Path danego żądania.
        return Response(
            render_template("manifest.webmanifest", base=base(),
                            version=__version__),
            mimetype="application/manifest+json")

    # ── API: ustawienia / pojazd (0.7.0, edycja bez restartu) ──────────────

    @app.get("/api/settings")
    def api_settings_get():
        return jsonify(live_settings())

    @app.get("/api/ha-services")
    def api_ha_services():
        services = ha_services() if ha_services else []
        return jsonify({"services": services or []})

    @app.put("/api/settings")
    def api_settings_put():
        data = request.get_json(force=True)
        updates = {k: v for k, v in data.items()
                  if k in settingsm.SETTINGS_TYPES}
        settingsm.set_settings(conn(), updates)
        changed()
        return jsonify({"ok": True})

    # ── API: pojazdy (0.8.0, cykl życia + leasing per auto) ────────────────

    @app.get("/api/vehicles")
    def api_vehicles_list():
        active_id = current_vehicle_id()
        rows = dbm.list_vehicles(conn(), include_archived=True)
        for r in rows:
            r["active"] = r["id"] == active_id
        return jsonify(rows)

    @app.post("/api/vehicles")
    def api_vehicle_create():
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Wymagana nazwa"}), 400
        limit = data.get("lease_km_limit")
        rate = data.get("monthly_rate")
        new_id = dbm.create_vehicle(
            conn(), name, float(data.get("tank_capacity_l") or 0),
            data.get("fuel_type") or "PB95",
            lease_start=data.get("lease_start") or None,
            lease_end=data.get("lease_end") or None,
            lease_km_limit=int(limit) if limit else None,
            monthly_rate=float(rate) if rate else None)
        changed()
        return jsonify({"id": new_id}), 201

    @app.get("/api/vehicles/<int:vid>")
    def api_vehicle_get(vid: int):
        v = dbm.get_vehicle(conn(), vid)
        if not v:
            return jsonify({"error": "not found"}), 404
        return jsonify(v)

    @app.put("/api/vehicles/<int:vid>")
    def api_vehicle_update(vid: int):
        if not dbm.get_vehicle(conn(), vid):
            return jsonify({"error": "not found"}), 404
        data = request.get_json(force=True)
        if not dbm.update_vehicle(conn(), vid, data):
            return jsonify({"error": "Brak poprawnych pól"}), 400
        changed()
        return jsonify({"ok": True})

    @app.delete("/api/vehicles/<int:vid>")
    def api_vehicle_delete(vid: int):
        ok, reason = dbm.delete_vehicle(conn(), vid)
        if not ok:
            return jsonify({"error": reason}), 409
        changed()
        return jsonify({"ok": True})

    @app.post("/api/vehicles/<int:vid>/activate")
    def api_vehicle_activate(vid: int):
        v = dbm.get_vehicle(conn(), vid)
        if not v:
            return jsonify({"error": "not found"}), 404
        if v["archived"]:
            return jsonify({"error":
                            "Pojazd zarchiwizowany — najpierw przywróć"}), 400
        settingsm.set_settings(conn(), {"active_vehicle_id": vid})
        changed()
        return jsonify({"ok": True})

    @app.post("/api/vehicles/<int:vid>/archive")
    def api_vehicle_archive(vid: int):
        if not dbm.archive_vehicle(conn(), vid):
            return jsonify({"error":
                            "Nie można zarchiwizować jedynego pojazdu"}), 400
        changed()
        return jsonify({"ok": True})

    @app.post("/api/vehicles/<int:vid>/unarchive")
    def api_vehicle_unarchive(vid: int):
        if not dbm.unarchive_vehicle(conn(), vid):
            return jsonify({"error": "not found"}), 404
        changed()
        return jsonify({"ok": True})

    # ── API: podsumowanie / wykresy ───────────────────────────────────────

    @app.get("/api/summary")
    def api_summary():
        return jsonify(queries.summary(conn(), current_vehicle_id(),
                                       live_settings()["monthly_fuel_budget"]))

    # ── API: tankowania ───────────────────────────────────────────────────

    @app.get("/api/fillups")
    def api_fillups():
        rows = queries.fetch_fillups(conn(), current_vehicle_id(), include_drafts=True)
        cons = st.segment_consumption_by_fillup(
            [r for r in rows if not r["draft"]])
        attachments = _attachment_map("fillup_id")
        for r in rows:
            r["consumption"] = cons.get(r["id"])
            r["attachment_id"] = attachments.get(r["id"])
        return jsonify(rows)

    @app.get("/api/fillups/<int:fid>")
    def api_fillup_get(fid: int):
        row = conn().execute(
            "SELECT * FROM fillups WHERE id = ? AND vehicle_id = ?",
            (fid, current_vehicle_id())).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify(dict(row))

    def _fillup_fields(data: dict) -> dict:
        volume = float(data.get("volume_l") or 0)
        price = float(data.get("price_per_l") or 0)
        total = float(data.get("total_cost") or 0)
        # Dowolne 2 z 3 pól wyznaczają trzecie — w walucie wpisu
        # (za granicą użytkownik wpisuje kwoty z dystrybutora/paragonu).
        if volume and price and not total:
            total = round(volume * price, 2)
        elif volume and total and not price:
            price = round(total / volume, 3)
        elif price and total and not volume:
            volume = round(total / price, 3)
        date = (data.get("date") or "").replace("T", " ")[:16]
        currency = (data.get("currency") or "PLN").strip().upper()
        price_orig = total_orig = rate = None
        if currency != "PLN":
            rate = float(data.get("exchange_rate") or 0) or None
            if rate is None:
                fetched = cur_mod.get_rate(conn(), currency, date[:10])
                rate = fetched["rate"] if fetched else None
            price_orig, total_orig = price, total
            if rate:
                price = round(price * rate, 3)
                total = round(total * rate, 2)
        return {
            "date": date,
            "odometer": int(data.get("odometer") or 0),
            "volume_l": volume, "price_per_l": price, "total_cost": total,
            "currency": currency, "exchange_rate": rate,
            "price_per_l_orig": price_orig, "total_cost_orig": total_orig,
            "full_tank": 1 if data.get("full_tank") in (1, "1", True, "true", "on") else 0,
            "missed_previous": 1 if data.get("missed_previous") in (1, "1", True, "true", "on") else 0,
            "fuel_type": data.get("fuel_type") or live_vehicle()["fuel_type"],
            "station": (data.get("station") or "").strip() or None,
            "notes": (data.get("notes") or "").strip() or None,
            "paid_by": "own" if data.get("paid_by") in ("own", 1, "1", True, "true", "on") else "fleet_card",
            "latitude": float(data["latitude"]) if data.get("latitude") not in (None, "") else None,
            "longitude": float(data["longitude"]) if data.get("longitude") not in (None, "") else None,
        }

    def _odometer_error(f: dict, exclude_id: int | None = None) -> str | None:
        """Przebieg musi rosnąć w czasie względem sąsiednich wpisów (po dacie).

        missed_previous wyłącza kontrolę (np. korekta po wymianie licznika).
        """
        if f["missed_previous"]:
            return None
        params = [current_vehicle_id(), exclude_id or -1, f["date"]]
        prev = conn().execute(
            "SELECT odometer FROM fillups WHERE vehicle_id = ? AND draft = 0 "
            "AND id != ? AND date < ? ORDER BY date DESC LIMIT 1",
            params).fetchone()
        if prev and f["odometer"] < prev["odometer"]:
            return (f"Przebieg {f['odometer']} km mniejszy niż poprzedni wpis "
                    f"({prev['odometer']} km)")
        nxt = conn().execute(
            "SELECT odometer FROM fillups WHERE vehicle_id = ? AND draft = 0 "
            "AND id != ? AND date > ? ORDER BY date ASC LIMIT 1",
            params).fetchone()
        if nxt and f["odometer"] > nxt["odometer"]:
            return (f"Przebieg {f['odometer']} km większy niż późniejszy wpis "
                    f"({nxt['odometer']} km)")
        return None

    def _remember_station(f: dict) -> None:
        if f["station"]:
            stn.upsert_station(conn(), f["station"],
                               f["latitude"], f["longitude"])

    def _currency_error(f: dict) -> str | None:
        if f["currency"] != "PLN" and not f["exchange_rate"]:
            return (f"Brak kursu {f['currency']} (NBP niedostępne) — "
                    "podaj kurs ręcznie")
        return None

    def _link_attachment(data: dict, column: str, entry_id: int) -> None:
        """Wiąże załącznik (zdjęcie paragonu) z utworzonym/edytowanym wpisem."""
        aid = data.get("attachment_id")
        if not aid:
            return
        assert column in ("fillup_id", "expense_id")
        conn().execute(
            f"UPDATE attachments SET {column} = ? WHERE id = ?",
            (entry_id, int(aid)))
        conn().commit()

    def _attachment_map(column: str) -> dict[int, int]:
        """Mapa id wpisu → id załącznika (do list tankowań/wydatków)."""
        rows = conn().execute(
            f"SELECT id, {column} AS eid FROM attachments "
            f"WHERE {column} IS NOT NULL").fetchall()
        return {r["eid"]: r["id"] for r in rows}

    @app.post("/api/fillups")
    def api_fillup_add():
        data = request.get_json(force=True)
        f = _fillup_fields(data)
        if not f["date"] or not f["odometer"] or f["volume_l"] <= 0:
            return jsonify({"error": "Wymagane: data, przebieg, litry"}), 400
        err = _currency_error(f) or _odometer_error(f)
        if err:
            return jsonify({"error": err}), 400
        try:
            cur = conn().execute(
                """INSERT INTO fillups (vehicle_id, date, odometer, volume_l,
                   price_per_l, total_cost, full_tank, missed_previous,
                   fuel_type, station, notes, paid_by, latitude, longitude,
                   currency, price_per_l_orig, total_cost_orig, exchange_rate,
                   source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'manual')""",
                (current_vehicle_id(), f["date"], f["odometer"], f["volume_l"],
                 f["price_per_l"], f["total_cost"], f["full_tank"],
                 f["missed_previous"], f["fuel_type"], f["station"], f["notes"],
                 f["paid_by"], f["latitude"], f["longitude"],
                 f["currency"], f["price_per_l_orig"], f["total_cost_orig"],
                 f["exchange_rate"]))
            conn().commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "Wpis o tej dacie i przebiegu już istnieje"}), 409
        _link_attachment(data, "fillup_id", cur.lastrowid)
        _remember_station(f)
        changed()
        return jsonify({"id": cur.lastrowid}), 201

    @app.put("/api/fillups/<int:fid>")
    def api_fillup_update(fid: int):
        data = request.get_json(force=True)
        f = _fillup_fields(data)
        err = _currency_error(f) or _odometer_error(f, exclude_id=fid)
        if err:
            return jsonify({"error": err}), 400
        cur = conn().execute(
            """UPDATE fillups SET date=?, odometer=?, volume_l=?, price_per_l=?,
               total_cost=?, full_tank=?, missed_previous=?, fuel_type=?,
               station=?, notes=?, paid_by=?, latitude=?, longitude=?,
               currency=?, price_per_l_orig=?, total_cost_orig=?,
               exchange_rate=?, draft=0
               WHERE id=? AND vehicle_id=?""",
            (f["date"], f["odometer"], f["volume_l"], f["price_per_l"],
             f["total_cost"], f["full_tank"], f["missed_previous"],
             f["fuel_type"], f["station"], f["notes"], f["paid_by"],
             f["latitude"], f["longitude"], f["currency"],
             f["price_per_l_orig"], f["total_cost_orig"], f["exchange_rate"],
             fid, current_vehicle_id()))
        conn().commit()
        if not cur.rowcount:
            return jsonify({"error": "not found"}), 404
        _link_attachment(data, "fillup_id", fid)
        _remember_station(f)
        changed()
        return jsonify({"ok": True})

    @app.delete("/api/fillups/<int:fid>")
    def api_fillup_delete(fid: int):
        cur = conn().execute(
            "DELETE FROM fillups WHERE id = ? AND vehicle_id = ?",
            (fid, current_vehicle_id()))
        conn().commit()
        if not cur.rowcount:
            return jsonify({"error": "not found"}), 404
        changed()
        return jsonify({"ok": True})

    @app.get("/api/prefill")
    def api_prefill():
        """Prefill formularza: odometr z myskoda, ostatnia stacja i cena."""
        s = live_settings()
        odometer = _odometer_from_ha(s)
        last = conn().execute(
            "SELECT station, price_per_l FROM fillups WHERE vehicle_id = ? "
            "AND draft = 0 ORDER BY odometer DESC LIMIT 1",
            (current_vehicle_id(),)).fetchone()
        # Pozycja telefonu (location_entity) → dopasowanie zapisanej stacji.
        lat = lon = matched = None
        if ha_state and s["location_entity"]:
            data = ha_state(s["location_entity"])
            attrs = (data or {}).get("attributes", {})
            try:
                lat, lon = float(attrs["latitude"]), float(attrs["longitude"])
            except (KeyError, TypeError, ValueError):
                lat = lon = None
            if lat is not None:
                matched = stn.nearest_station(conn(), lat, lon)
        return jsonify({
            "date": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "odometer": odometer,
            "station": matched["name"] if matched else
                       (last["station"] if last else None),
            "station_matched": bool(matched),
            "latitude": lat, "longitude": lon,
            "price_per_l": last["price_per_l"] if last else None,
            "fuel_type": live_vehicle()["fuel_type"],
        })

    @app.get("/api/rate")
    def api_rate():
        """Kurs NBP dla formularza: ?currency=EUR&date=YYYY-MM-DD."""
        code = (request.args.get("currency") or "").strip().upper()
        on_date = (request.args.get("date") or
                   datetime.now().strftime("%Y-%m-%d"))[:10]
        if not code:
            return jsonify({"error": "Wymagany parametr currency"}), 400
        rate = cur_mod.get_rate(conn(), code, on_date)
        if rate is None:
            return jsonify({"error": f"Brak kursu {code} — NBP niedostępne, "
                                     "podaj kurs ręcznie"}), 502
        return jsonify(rate | {"currency": code})

    # ── API: stacje / mapa ────────────────────────────────────────────────

    @app.get("/api/stations")
    def api_stations():
        return jsonify(stn.list_stations(conn()))

    @app.get("/api/stations/nearby")
    def api_stations_nearby():
        """Sugestie OSM Overpass dla pozycji bez dopasowanej stacji."""
        try:
            lat = float(request.args["lat"])
            lon = float(request.args["lon"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Wymagane parametry lat i lon"}), 400
        return jsonify(stn.overpass_lookup(lat, lon))

    @app.get("/api/map-data")
    def api_map_data():
        return jsonify(stn.map_data(conn(), current_vehicle_id()))

    # ── API: wydatki ──────────────────────────────────────────────────────

    @app.get("/api/expenses")
    def api_expenses():
        rows = queries.fetch_expenses(conn(), current_vehicle_id())
        attachments = _attachment_map("expense_id")
        for r in rows:
            r["attachment_id"] = attachments.get(r["id"])
        return jsonify(rows)

    @app.get("/api/categories")
    def api_categories():
        rows = conn().execute(
            "SELECT id, name, hidden FROM expense_categories ORDER BY id"
        ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.put("/api/categories/<int:cid>")
    def api_category_update(cid: int):
        data = request.get_json(force=True)
        cur = conn().execute(
            "UPDATE expense_categories SET hidden = ? WHERE id = ?",
            (1 if data.get("hidden") in (1, "1", True, "true") else 0, cid))
        conn().commit()
        if not cur.rowcount:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})

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
            (current_vehicle_id(), date, int(data.get("odometer") or 0) or None,
             int(data.get("category_id") or 0) or dbm.category_id(conn(), None),
             (data.get("description") or "").strip() or None, cost))
        conn().commit()
        _link_attachment(data, "expense_id", cur.lastrowid)
        changed()
        return jsonify({"id": cur.lastrowid}), 201

    @app.put("/api/expenses/<int:eid>")
    def api_expense_update(eid: int):
        data = request.get_json(force=True)
        date = (data.get("date") or "").replace("T", " ")[:16]
        cost = float(data.get("cost") or 0)
        if not date or cost <= 0:
            return jsonify({"error": "Wymagane: data i kwota"}), 400
        cur = conn().execute(
            """UPDATE expenses SET date=?, odometer=?, category_id=?,
               description=?, cost=? WHERE id=? AND vehicle_id=?""",
            (date, int(data.get("odometer") or 0) or None,
             int(data.get("category_id") or 0) or dbm.category_id(conn(), None),
             (data.get("description") or "").strip() or None, cost,
             eid, current_vehicle_id()))
        conn().commit()
        if not cur.rowcount:
            return jsonify({"error": "not found"}), 404
        changed()
        return jsonify({"ok": True})

    @app.delete("/api/expenses/<int:eid>")
    def api_expense_delete(eid: int):
        cur = conn().execute(
            "DELETE FROM expenses WHERE id = ? AND vehicle_id = ?",
            (eid, current_vehicle_id()))
        conn().commit()
        if not cur.rowcount:
            return jsonify({"error": "not found"}), 404
        changed()
        return jsonify({"ok": True})

    # ── API: paragony (0.5.0) ─────────────────────────────────────────────

    @app.post("/api/receipts/parse")
    def api_receipt_parse():
        """Zdjęcie paragonu → zapis do share + analiza llmvision → prefill.

        Zdjęcie zostaje na dysku nawet gdy analiza się nie powiedzie —
        wpis można uzupełnić ręcznie, a załącznik i tak podpiąć.
        """
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "Brak pliku"}), 400
        filename = receipts.save_upload(file, attach_dir)
        cur = conn().execute(
            "INSERT INTO attachments (filename) VALUES (?)", (filename,))
        conn().commit()
        aid = cur.lastrowid
        try:
            parsed = receipts.analyze(str(attach_dir / filename))
            norm = receipts.normalize(
                parsed, live_vehicle()["fuel_type"])
        except receipts.ReceiptError as exc:
            return jsonify({"error": str(exc), "attachment_id": aid}), 502
        except Exception:
            logger.exception("Analiza paragonu nieudana")
            return jsonify({"error": "Analiza paragonu nieudana — "
                                     "sprawdź logi add-onu",
                            "attachment_id": aid}), 502
        conn().execute(
            "UPDATE attachments SET parsed_json = ? WHERE id = ?",
            (json.dumps(norm, ensure_ascii=False), aid))
        conn().commit()
        return jsonify({"attachment_id": aid, "parsed": norm})

    @app.get("/api/attachments/<int:aid>")
    def api_attachment(aid: int):
        row = conn().execute(
            "SELECT filename FROM attachments WHERE id = ?", (aid,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        return send_from_directory(attach_dir, row["filename"],
                                   max_age=31536000)

    # ── API: kopia zapasowa (0.10.0) ───────────────────────────────────────

    @app.get("/api/backup/list")
    def api_backup_list():
        return jsonify(bkp.list_backups(share_dir))

    def _backup_path(filename: str) -> Path | None:
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            return None
        return Path(share_dir) / "backups" / filename

    @app.post("/api/backup/restore")
    def api_backup_restore():
        data = request.get_json(force=True)
        path = _backup_path((data.get("filename") or "").strip())
        if path is None:
            return jsonify({"error": "Nieprawidłowa nazwa pliku"}), 400
        if not path.is_file():
            return jsonify({"error": "not found"}), 404
        try:
            result = bkp.restore_from_path(str(path), db_path, share_dir)
        except bkp.BackupError as exc:
            return jsonify({"error": str(exc)}), 400
        changed()
        return jsonify(result)

    @app.post("/api/backup/restore/upload")
    def api_backup_restore_upload():
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "Brak pliku"}), 400
        try:
            result = bkp.restore_from_upload(file, db_path, share_dir)
        except bkp.BackupError as exc:
            return jsonify({"error": str(exc)}), 400
        changed()
        return jsonify(result)

    @app.get("/api/backup/export.json")
    def api_backup_export_json():
        payload = bkp.export_json(conn())
        return Response(
            json.dumps(payload, ensure_ascii=False),
            mimetype="application/json",
            headers={"Content-Disposition":
                     f"attachment; filename=fuel_tracker-export-"
                     f"{datetime.now():%Y%m%d}.json"})

    @app.post("/api/backup/import.json")
    def api_backup_import_json():
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "Brak pliku"}), 400
        try:
            payload = json.loads(file.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return jsonify({"error": "Plik nie jest poprawnym JSON"}), 400
        try:
            result = bkp.import_json(conn(), payload)
        except bkp.BackupError as exc:
            return jsonify({"error": str(exc)}), 400
        changed()
        return jsonify(result)

    # ── API: import / eksport / weryfikacja ───────────────────────────────

    @app.post("/api/import/csv")
    def api_import_csv():
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "Brak pliku"}), 400
        text = file.read().decode("utf-8-sig", errors="replace")
        report = csv_fuelio.import_into(
            conn(), current_vehicle_id(), text, live_vehicle()["fuel_type"])
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
                conn(), current_vehicle_id(), email, password,
                int(body.get("vehicle_id") or config.get("drivvo_vehicle_id", 0) or 0),
                live_vehicle()["fuel_type"],
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
        fillups = queries.fetch_fillups(conn(), current_vehicle_id())
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

    # ── API: statystyki / raport ──────────────────────────────────────────

    def _ha_float(entity_key: str) -> float | None:
        if not (ha_state and config.get(entity_key)):
            return None
        data = ha_state(config[entity_key])
        try:
            return float(data["state"]) if data else None
        except (KeyError, TypeError, ValueError):
            return None

    @app.get("/api/statistics")
    def api_statistics():
        fillups = queries.fetch_fillups(conn(), current_vehicle_id())
        expenses = queries.fetch_expenses(conn(), current_vehicle_id())
        vehicle = live_vehicle()
        settings_now = live_settings()
        region = settings_now["price_region"]
        fuel_type = vehicle["fuel_type"]
        s = st.compute_stats(fillups)

        # Leasing per auto (0.8.0): przebieg z odometer_entity, awaryjnie z
        # ostatniego tankowania — ta sama krzywa co sensor.odo_vs_budget
        # (zewnętrzny, zostaje niżej do porównania przed ewentualnym
        # wycofaniem template'a).
        annual_km = st.projected_annual_km(fillups)
        odo_now = _odometer_from_ha(settings_now)
        if odo_now is None:
            odo_now = s.last_fillup["odometer"] if s.last_fillup else None
        now = datetime.now()
        lease_margin = st.lease_km_margin(
            vehicle["lease_km_limit"], vehicle["lease_start"],
            vehicle["lease_end"], odo_now, now)
        depletion = st.lease_depletion_date(
            vehicle["lease_km_limit"], odo_now, annual_km, now)

        return jsonify({
            "records": st.record_entries(fillups),
            "stations": st.station_ranking(fillups),
            "monthly_km": st.monthly_km(fillups),
            "monthly_report": st.monthly_report(fillups, expenses),
            "split": {
                "fuel_card": round(sum(f["total_cost"] for f in fillups
                                       if f.get("paid_by") != "own"), 2),
                "fuel_own": round(sum(f["total_cost"] for f in fillups
                                      if f.get("paid_by") == "own"), 2),
                "fluids": round(sum(e["cost"] for e in expenses
                                    if e.get("category") == st.FLUIDS_CATEGORY), 2),
                "other_expenses": round(sum(
                    e["cost"] for e in expenses
                    if e.get("category") != st.FLUIDS_CATEGORY), 2),
            },
            "region": {
                "name": region, "fuel_type": fuel_type,
                "latest": pr.latest_price(conn(), region, fuel_type),
                "series": pr.price_series(conn(), region, fuel_type),
            },
            "price_series": [
                {"date": f["date"], "value": f["price_per_l"]}
                for f in sorted(fillups, key=lambda x: x["date"])
                if f["price_per_l"]],
            "leasing": {
                "odo_vs_budget": _ha_float("odo_budget_entity"),
                "projected_annual_km": annual_km,
                "current_odometer": odo_now,
                "km_limit": vehicle["lease_km_limit"],
                "lease_km_margin": lease_margin,
                "limit_depletion_date": depletion,
            },
            "estimated_range_km": st.estimated_range_km(
                s.avg_consumption, float(vehicle["tank_capacity_l"] or 0)),
        })

    @app.get("/api/report.csv")
    def api_report_csv():
        fillups = queries.fetch_fillups(conn(), current_vehicle_id())
        expenses = queries.fetch_expenses(conn(), current_vehicle_id())
        year = request.args.get("year")
        rows = st.monthly_report(fillups, expenses)
        if year:
            rows = [r for r in rows if r["month"].startswith(year)]
        buf = io.StringIO()
        buf.write("Miesiac;PaliwoKarta;PaliwoPrywatne;Plyny;InneWydatki;"
                  "Litry;Km\n")
        for r in rows:
            buf.write(f"{r['month']};{r['fuel_card']};{r['fuel_own']};"
                      f"{r['fluids']};{r['other_expenses']};"
                      f"{r['volume_l']};{r['km']}\n")
        return Response(
            buf.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition":
                     "attachment; filename=fuel_report.csv"})

    @app.get("/api/export/fuelio.csv")
    def api_export():
        data = csv_fuelio.export_csv(conn(), current_vehicle_id())
        return Response(
            data, mimetype="text/csv",
            headers={"Content-Disposition":
                     "attachment; filename=fuelio_export.csv"})

    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True})

    return app
