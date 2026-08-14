"""Klienci vision (Gemini bezpośrednio + lokalny freellmapi) — dwa pierwsze
ogniwa łańcucha providerów skanera paragonów (0.15.0, docs/PLAN-0.15.0-vision.md).

Oba klienty zwracają sparsowany dict albo rzucają VisionError z PRAWDZIWĄ
treścią błędu od providera — to jest naprawa martwego komunikatu z 0.14.0
(„nie odpowiedziała" zamiast realnego powodu).
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from fuel_tracker import vision

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def image_path(tmp_path):
    p = tmp_path / "receipt.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0 nie-prawdziwy-jpeg-wystarczy-do-testu")
    return str(p)


PARSED = {"receipt_type": "fleet_card", "date": "2026-07-03", "currency": "PLN",
         "fuel_volume_l": 52.47, "fuel_total": 357.85, "non_fuel_items": [],
         "fuel_name": "", "fuel_price_per_l": 0, "odometer_km": 31462,
         "station_name": "ORLEN Będzino", "time": "15:56"}


def _resp(status, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body or {}
    r.text = text or json.dumps(json_body or {})
    return r


# ── call_gemini ──────────────────────────────────────────────────────────

def test_call_gemini_success_returns_parsed_dict(monkeypatch, image_path):
    def fake_post(url, **kwargs):
        assert "generativelanguage.googleapis.com" in url
        assert kwargs["headers"]["x-goog-api-key"] == "test-key"
        body = kwargs["json"]
        assert body["generationConfig"]["response_mime_type"] == "application/json"
        parts = body["contents"][0]["parts"]
        assert any("inline_data" in p for p in parts)
        return _resp(200, {
            "candidates": [{"content": {"parts": [
                {"text": json.dumps(PARSED, ensure_ascii=False)}]}}],
            "usageMetadata": {"totalTokenCount": 1499},
        })
    monkeypatch.setattr(vision.requests, "post", fake_post)

    result = vision.call_gemini(image_path, "prompt", {"type": "object"},
                                api_key="test-key", model="gemini-3.1-flash-lite")
    assert result == PARSED


def test_call_gemini_http_error_includes_provider_message(monkeypatch, image_path):
    monkeypatch.setattr(vision.requests, "post",
                        lambda *a, **kw: _resp(429, text="quota exceeded for today"))
    with pytest.raises(vision.VisionError) as exc:
        vision.call_gemini(image_path, "p", {}, api_key="k", model="m")
    assert "429" in str(exc.value)
    assert "quota exceeded for today" in str(exc.value)
    assert "gemini" in str(exc.value)


def test_call_gemini_unparseable_response_raises(monkeypatch, image_path):
    monkeypatch.setattr(vision.requests, "post", lambda *a, **kw: _resp(200, {
        "candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}))
    with pytest.raises(vision.VisionError):
        vision.call_gemini(image_path, "p", {}, api_key="k", model="m")


def test_call_gemini_network_error_raises(monkeypatch, image_path):
    def boom(*a, **kw):
        raise requests.RequestException("dns fail")
    monkeypatch.setattr(vision.requests, "post", boom)
    with pytest.raises(vision.VisionError):
        vision.call_gemini(image_path, "p", {}, api_key="k", model="m")


# ── call_local ───────────────────────────────────────────────────────────

def test_call_local_success_returns_parsed_dict(monkeypatch, image_path):
    def fake_post(url, **kwargs):
        assert url == "http://192.168.0.106:3003/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer local-key"
        body = kwargs["json"]
        assert body["response_format"]["type"] == "json_schema"
        content = body["messages"][0]["content"]
        assert any(part.get("type") == "image_url" for part in content)
        return _resp(200, {
            "choices": [{"message": {"content": json.dumps(PARSED, ensure_ascii=False)}}],
            "usage": {"total_tokens": 1400},
        })
    monkeypatch.setattr(vision.requests, "post", fake_post)

    result = vision.call_local(image_path, "prompt", {"type": "object"},
                               base_url="http://192.168.0.106:3003/v1",
                               api_key="local-key", model="gemini-3.1-flash-lite")
    assert result == PARSED


def test_call_local_raises_max_tokens_to_minimum(monkeypatch, image_path):
    captured = {}
    def fake_post(url, **kwargs):
        captured["max_tokens"] = kwargs["json"]["max_tokens"]
        return _resp(200, {"choices": [{"message": {
            "content": json.dumps(PARSED, ensure_ascii=False)}}]})
    monkeypatch.setattr(vision.requests, "post", fake_post)

    vision.call_local(image_path, "p", {}, base_url="http://x/v1", api_key="k",
                      model="m", max_tokens=300)
    assert captured["max_tokens"] >= 1500


def test_call_local_retries_502_then_succeeds(monkeypatch, image_path):
    monkeypatch.setattr(vision.time, "sleep", lambda *_: None)
    calls = []
    def fake_post(url, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            return _resp(502, text="provider_error")
        return _resp(200, {"choices": [{"message": {
            "content": json.dumps(PARSED, ensure_ascii=False)}}]})
    monkeypatch.setattr(vision.requests, "post", fake_post)

    result = vision.call_local(image_path, "p", {}, base_url="http://x/v1",
                               api_key="k", model="m")
    assert result == PARSED
    assert len(calls) == 3


def test_call_local_gives_up_after_max_attempts_with_provider_message(monkeypatch, image_path):
    monkeypatch.setattr(vision.time, "sleep", lambda *_: None)
    monkeypatch.setattr(vision.requests, "post",
                        lambda *a, **kw: _resp(502, text="provider_error, all keys exhausted"))
    with pytest.raises(vision.VisionError) as exc:
        vision.call_local(image_path, "p", {}, base_url="http://x/v1", api_key="k", model="m")
    assert "502" in str(exc.value)
    assert "provider_error" in str(exc.value)
    assert "freellmapi" in str(exc.value)


def test_call_local_non_retryable_error_fails_fast(monkeypatch, image_path):
    calls = []
    def fake_post(url, **kwargs):
        calls.append(1)
        return _resp(401, text="Invalid API key")
    monkeypatch.setattr(vision.requests, "post", fake_post)

    with pytest.raises(vision.VisionError) as exc:
        vision.call_local(image_path, "p", {}, base_url="http://x/v1", api_key="k", model="m")
    assert len(calls) == 1  # bez retry na 401
    assert "401" in str(exc.value)
    assert "Invalid API key" in str(exc.value)


def test_call_local_network_error_raises(monkeypatch, image_path):
    def boom(*a, **kw):
        raise requests.RequestException("connection refused")
    monkeypatch.setattr(vision.requests, "post", boom)
    with pytest.raises(vision.VisionError):
        vision.call_local(image_path, "p", {}, base_url="http://x/v1", api_key="k", model="m")
