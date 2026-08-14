"""Parser paragonów ze zdjęcia — łańcuch trzech providerów vision (0.15.0,
docs/PLAN-0.15.0-vision.md) + normalizacja wyniku.

Zdjęcie ląduje w <backup_share>/attachments/ (katalog widoczny też
z kontenera HA core jako /share/...). analyze() próbuje po kolei:
1. Gemini bezpośrednio (fuel_tracker.vision.call_gemini) — primary,
2. lokalny router freellmapi (fuel_tracker.vision.call_local) — fallback,
3. llmvision.image_analyzer przez HA (provider wykrywany automatycznie
   przez config_entries) — ostatnie ogniwo, bez własnych opcji add-onu.
Ogniwo, które zawiedzie, oddaje głos następnemu; ReceiptError dopiero gdy
WSZYSTKIE zawiodą, z powodami wszystkich prób sklejonymi w jeden komunikat.

Dwa znane formaty ORLEN (próbki w tests/fixtures/):
- paragon fiskalny: nazwa paliwa, litry × cena/L, sekcja VAT;
- "DOWÓD WYDANIA - KARTA FLOTA ORLEN" (niefiskalny): Kwota, Ilość,
  Stan licznika (przebieg!), bez ceny/L i bez nazwy paliwa.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from . import ha_client, vision

logger = logging.getLogger(__name__)

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# Modele wypróbowywane po kolei na ogniwach gemini/llmvision, gdy pierwszy
# padnie (wyczerpana dzienna quota konkretnego modelu itp.) — paragon FLOTA
# parsują identycznie (zweryfikowane 2026-07-07, ponownie 2026-08-14).
# gemini-2.5-flash-lite USUNIĘTY 0.15.0: HTTP 404 „no longer available to
# new users" na nowych kluczach (zmierzone empirycznie) — jedyny „zapasowy"
# model był od jakiegoś czasu martwy, patrz docs/PLAN-0.15.0-vision.md.
MODELS = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite")

# Schemat wymuszany na modelu (llmvision structure). Bez additionalProperties
# — Gemini structured output nie przyjmuje tego pola w każdym wariancie.
STRUCTURE = {
    "type": "object",
    "properties": {
        "receipt_type": {
            "type": "string",
            "description": "fiscal | fleet_card | other",
        },
        "station_name": {"type": "string",
                         "description": "Sieć + miasto, np. 'ORLEN Warszawa'"},
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "time": {"type": "string", "description": "HH:MM, 24h"},
        "odometer_km": {"type": "integer",
                        "description": "Stan licznika, 0 gdy brak"},
        "fuel_name": {"type": "string",
                      "description": "Nazwa paliwa z paragonu, '' gdy brak"},
        "fuel_volume_l": {"type": "number"},
        "fuel_price_per_l": {"type": "number",
                             "description": "0 gdy brak na paragonie"},
        "fuel_total": {"type": "number"},
        "currency": {"type": "string", "description": "Kod ISO, np. PLN"},
        "non_fuel_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "total": {"type": "number"},
                },
                "required": ["description", "total"],
            },
        },
    },
    "required": ["receipt_type", "date", "currency", "fuel_volume_l",
                 "fuel_total", "non_fuel_items"],
}

PROMPT = """Przeanalizuj zdjęcie paragonu ze stacji paliw (najpewniej Polska).
Rozpoznaj format:
1. Paragon fiskalny — pozycja paliwa z nazwą (np. EFECTA 95, VERVA ON),
   litrami i ceną za litr; sekcja VAT.
2. "DOWÓD WYDANIA - KARTA FLOTA ORLEN" (niefiskalny) — pola: Kwota,
   Ilość (litry), Stan licznika (przebieg w km), bez ceny za litr
   i bez nazwy paliwa.
Wyciągnij dane wg schematu. Liczby z kropką dziesiętną, bez jednostek.
odometer_km wypełnij TYLKO gdy paragon ma stan licznika/przebieg — inaczej 0.
fuel_price_per_l = 0, gdy ceny za litr nie ma wprost na paragonie.
Pozycje niepaliwowe (AdBlue, płyn do spryskiwaczy, olej, akcesoria,
jedzenie) wypisz w non_fuel_items z kwotami brutto; nie wliczaj ich
do fuel_total. Gdy paragon jest wyłącznie za paliwo, non_fuel_items = []."""


class ReceiptError(Exception):
    """Błąd zrozumiały dla użytkownika (zwracany w API)."""


def save_upload(file_storage, attach_dir: Path) -> str:
    """Zapisuje upload do katalogu załączników; zwraca nazwę pliku."""
    orig = getattr(file_storage, "filename", "") or ""
    ext = Path(orig).suffix.lower()
    if ext not in _ALLOWED_EXT:
        # WebView aparatu HA wysyła image/jpeg bez rozszerzenia.
        mime = (getattr(file_storage, "mimetype", "") or "").lower()
        ext = {"image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
    name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{ext}"
    attach_dir.mkdir(parents=True, exist_ok=True)
    file_storage.save(str(attach_dir / name))
    return name


def _llmvision_attempt(image_path: str, provider: str, model: str) -> dict | None:
    """Jedna próba ogniwa llmvision (jeden model). None = porażka — HTTP
    błąd (ha_client.call_service już zwraca None na >=400, patrz ha_client.py)
    albo brak parsowalnego JSON w odpowiedzi. Naprawa 0.15.0: dawniej HTTP
    błąd przerywał całą analyze() natychmiast (call_service()->None->raise),
    więc pętla po MODELS w praktyce wykonywała się zawsze tylko raz."""
    resp = ha_client.call_service(
        "llmvision", "image_analyzer",
        {
            "provider": provider,
            "model": model,
            "message": PROMPT,
            "image_file": image_path,
            "include_filename": False,
            "target_width": 1280,
            "max_tokens": 1500,
            "response_format": "json",
            "structure": json.dumps(STRUCTURE, ensure_ascii=False),
        },
        return_response=True, timeout=90)
    if resp is None:
        return None
    data = resp.get("service_response") or {}
    parsed = data.get("structured_response")
    if isinstance(parsed, str):
        parsed = extract_json(parsed)
    if not isinstance(parsed, dict):
        parsed = extract_json(data.get("response_text") or "")
    return parsed if isinstance(parsed, dict) else None


def analyze(image_path: str, config: dict) -> dict:
    """Zdjęcie → surowy dict z modelu vision. Łańcuch trzech ogniw (0.15.0):
    gemini -> local (freellmapi) -> llmvision. config to podzbiór opcji
    Supervisora (gemini_api_key/gemini_model/local_llm_base_url/
    local_llm_api_key/local_llm_model) — puste pole = ogniwo pomijane.
    """
    config = config or {}
    reasons: list[str] = []

    gemini_key = (config.get("gemini_api_key") or "").strip()
    if gemini_key:
        configured = (config.get("gemini_model") or "").strip()
        models = ([configured] if configured else []) + [
            m for m in MODELS if m != configured]
        for model in models:
            try:
                return vision.call_gemini(
                    image_path, PROMPT, STRUCTURE,
                    api_key=gemini_key, model=model)
            except vision.VisionError as exc:
                logger.warning("receipts: gemini (%s) nieudane — %s", model, exc)
                reasons.append(str(exc))

    local_base = (config.get("local_llm_base_url") or "").strip()
    local_key = (config.get("local_llm_api_key") or "").strip()
    if local_base and local_key:
        model = (config.get("local_llm_model") or "gemini-3.1-flash-lite").strip()
        try:
            return vision.call_local(
                image_path, PROMPT, STRUCTURE,
                base_url=local_base, api_key=local_key, model=model)
        except vision.VisionError as exc:
            logger.warning("receipts: local (%s) nieudane — %s", model, exc)
            reasons.append(str(exc))

    provider = ha_client.find_config_entry("llmvision")
    if provider:
        for model in MODELS:
            parsed = _llmvision_attempt(image_path, provider, model)
            if parsed is not None:
                return parsed
            logger.warning("llmvision (%s): brak JSON/HTTP błąd w odpowiedzi", model)
        reasons.append("llmvision: żaden model nie zwrócił danych paragonu "
                       "(sprawdź logi HA — providera lub quotę)")
    else:
        reasons.append("llmvision: integracja nieskonfigurowana w HA")

    raise ReceiptError("; ".join(reasons))


def extract_json(text: str) -> dict | None:
    """Pierwszy obiekt JSON z tekstu (modele lubią płoty ```json)."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    start = text.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text[start:])
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


def _num(v) -> float | None:
    """Liczba > 0 albo None (model daje 0/None dla braków, czasem stringi)."""
    try:
        f = float(str(v).replace(",", "."))
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _map_fuel(name: str) -> str | None:
    n = name.upper()
    if not n:
        return None
    if "LPG" in n:
        return "LPG"
    if "ON" in n.split() or "DIESEL" in n or "VERVA ON" in n:
        return "ON"
    if "98" in n or "100" in n:
        return "PB98"
    if "95" in n:
        return "PB95"
    return name.strip() or None


def normalize(parsed: dict, default_fuel_type: str = "PB95") -> dict:
    """Surowy wynik modelu → pola formularza tankowania + wydatek Płyny."""
    volume = _num(parsed.get("fuel_volume_l"))
    price = _num(parsed.get("fuel_price_per_l"))
    total = _num(parsed.get("fuel_total"))
    # Dowód wydania FLOTA nie ma ceny/L; paragon bywa też bez sumy paliwa.
    if total and volume and not price:
        price = round(total / volume, 3)
    elif volume and price and not total:
        total = round(volume * price, 2)
    date = (str(parsed.get("date") or ""))[:10]
    time = (str(parsed.get("time") or ""))[:5]
    dt = ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        dt = f"{date}T{time if re.fullmatch(r'[0-2]\d:[0-5]\d', time) else '12:00'}"
    items = []
    for i in parsed.get("non_fuel_items") or []:
        cost = _num(i.get("total"))
        desc = (i.get("description") or "").strip()
        if cost and desc:
            items.append({"description": desc, "total": cost})
    odo = parsed.get("odometer_km")
    fuel = _map_fuel(str(parsed.get("fuel_name") or ""))
    return {
        "receipt_type": parsed.get("receipt_type") or "other",
        "date": dt,
        "odometer": int(odo) if isinstance(odo, (int, float)) and odo > 0 else None,
        "volume_l": volume,
        "price_per_l": price,
        "total_cost": total,
        "fuel_type": fuel or default_fuel_type,
        "station": (parsed.get("station_name") or "").strip() or None,
        "currency": ((parsed.get("currency") or "PLN").strip().upper()[:3]
                     or "PLN"),
        "non_fuel_items": items,
        "non_fuel_total": round(sum(i["total"] for i in items), 2),
    }
