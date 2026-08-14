"""Klienci vision — dwa pierwsze ogniwa łańcucha providerów skanera paragonów
(0.15.0, docs/PLAN-0.15.0-vision.md): Gemini bezpośrednio (primary) i lokalny
freellmapi (fallback). Trzecie ogniwo (llmvision przez HA) zostaje w receipts.py
— to jedyne, które wymaga integracji HA, więc nie ma tu klienta dla niego.

Oba klienty mają wspólny kontrakt: zwracają sparsowany dict albo rzucają
VisionError z PRAWDZIWĄ treścią błędu providera (nie generyczny komunikat —
patrz plan, punkt „komunikat gubi powód"). receipts.analyze() łączy oba
ogniwa w łańcuch i sam robi fallback po modelach z MODELS.

Kształty żądań zmierzone empirycznie na realnym paragonie z tests/fixtures/
(patrz docs/PLAN-0.15.0-vision.md, Krok 0 i testy providerów przed wdrożeniem):
Gemini responseSchema akceptuje STRUCTURE bez modyfikacji (structured output
strict, bez potrzeby additionalProperties), a router freellmapi obsługuje
image_url z data: URI identycznie jak zwykły OpenAI-compatible endpoint.
"""
from __future__ import annotations

import base64
import json
import logging
import random
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_MIN_MAX_TOKENS = 1500
_RETRYABLE_STATUSES = (502,)
_MAX_ATTEMPTS = 3
_BASE_DELAY = 1.0

_MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}


class VisionError(Exception):
    """Błąd zrozumiały dla użytkownika — treść providera, nie generyk."""


def _mime_for(image_path: str) -> str:
    return _MIME_BY_EXT.get(Path(image_path).suffix.lower(), "image/jpeg")


def _b64_image(image_path: str) -> str:
    return base64.b64encode(Path(image_path).read_bytes()).decode()


# ── Gemini (primary) ────────────────────────────────────────────────────

def call_gemini(image_path: str, prompt: str, structure: dict, *,
                api_key: str, model: str, max_tokens: int = _MIN_MAX_TOKENS,
                timeout: int = 90) -> dict:
    """Wywołuje Gemini REST bezpośrednio (nie przez llmvision/HA). Rzuca
    VisionError na każdą porażkę — receipts.analyze() decyduje co dalej."""
    if not api_key:
        raise VisionError("gemini: brak klucza API")

    try:
        resp = requests.post(
            f"{_GEMINI_BASE}/models/{model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json={
                "contents": [{"parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": _mime_for(image_path),
                                     "data": _b64_image(image_path)}},
                ]}],
                "generationConfig": {
                    "temperature": 0,
                    "max_output_tokens": max_tokens,
                    "response_mime_type": "application/json",
                    "response_schema": structure,
                },
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise VisionError(f"gemini: brak odpowiedzi (sieć) — {exc}") from exc

    if resp.status_code != 200:
        raise VisionError(f"gemini: HTTP {resp.status_code} — {resp.text[:300]}")

    try:
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except (KeyError, IndexError, ValueError) as exc:
        raise VisionError(f"gemini: nie udało się sparsować odpowiedzi — {exc}") from exc

    logger.info("gemini (%s): OK, %d tokenów", model,
               (data.get("usageMetadata") or {}).get("totalTokenCount", 0))
    return result


# ── freellmapi lokalny (fallback) ───────────────────────────────────────

def call_local(image_path: str, prompt: str, structure: dict, *,
               base_url: str, api_key: str, model: str,
               max_tokens: int = _MIN_MAX_TOKENS, timeout: int = 90) -> dict:
    """Wywołuje lokalny router OpenAI-compatible (freellmapi). Zmierzone
    zachowanie (patrz nokia_tracker/ai/openai_compat.py i Krok 0 planu):
    max_tokens musi być >=1500 (mniej ucina JSON reasoningowym modelom),
    HTTP 502 to przejściowa awaria upstreamu — retryable z backoffem."""
    if max_tokens < _MIN_MAX_TOKENS:
        logger.warning("vision: max_tokens=%d poniżej bezpiecznego minimum %d — "
                       "podnoszę na czas tego wywołania", max_tokens, _MIN_MAX_TOKENS)
        max_tokens = _MIN_MAX_TOKENS

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {
                "url": f"data:{_mime_for(image_path)};base64,{_b64_image(image_path)}"}},
        ]}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "receipt", "strict": True, "schema": structure},
        },
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    url = f"{base_url}/chat/completions"

    resp = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as exc:
            raise VisionError(f"freellmapi: brak odpowiedzi (sieć) — {exc}") from exc
        if resp.status_code not in _RETRYABLE_STATUSES:
            break
        if attempt < _MAX_ATTEMPTS - 1:
            delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, _BASE_DELAY)
            logger.warning("freellmapi: HTTP %d, ponawiam za %.1fs (próba %d/%d)",
                          resp.status_code, delay, attempt + 1, _MAX_ATTEMPTS)
            time.sleep(delay)

    if resp.status_code != 200:
        raise VisionError(f"freellmapi: HTTP {resp.status_code} — {resp.text[:300]}")

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
    except (KeyError, IndexError, ValueError) as exc:
        raise VisionError(f"freellmapi: nie udało się sparsować odpowiedzi — {exc}") from exc

    logger.info("freellmapi (%s): OK, %d tokenów", model,
               (data.get("usage") or {}).get("total_tokens", 0))
    return result
