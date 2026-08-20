"""Geokodowanie Nominatim: parsowanie, cache w bazie, best-effort na błędach.

Zero realnych żądań sieciowych — wszystko przez monkeypatch geocode.requests
(wzorzec jak test_stations.py: test_overpass_lookup_survives_network_failure).
"""
import pytest

from fuel_tracker import geocode


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_geocode_address_returns_coords_and_sets_user_agent(conn, monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        return _FakeResp([{"lat": "54.2088119", "lon": "15.9835218",
                           "display_name": "Orlen, 87, Będzino"}])
    monkeypatch.setattr(geocode.requests, "get", fake_get)

    coords = geocode.geocode_address(conn, "Będzino 87", "Będzino", "76-037")
    assert coords == (54.2088119, 15.9835218)
    assert len(calls) == 1
    _, params, headers = calls[0]
    assert params["street"] == "Będzino 87"
    assert params["city"] == "Będzino"
    assert params["postalcode"] == "76-037"
    assert "User-Agent" in headers
    assert "fuel_tracker" in headers["User-Agent"]


def test_geocode_address_caches_hit_without_second_request(conn, monkeypatch):
    calls = []

    def fake_get(*a, **kw):
        calls.append(1)
        return _FakeResp([{"lat": "54.2088119", "lon": "15.9835218"}])
    monkeypatch.setattr(geocode.requests, "get", fake_get)

    first = geocode.geocode_address(conn, "Będzino 87", "Będzino")
    second = geocode.geocode_address(conn, "Będzino 87", "Będzino")
    assert first == second
    assert len(calls) == 1  # drugie wywołanie z cache


def test_geocode_address_no_results_returns_none_and_caches(conn, monkeypatch):
    calls = []

    def fake_get(*a, **kw):
        calls.append(1)
        return _FakeResp([])
    monkeypatch.setattr(geocode.requests, "get", fake_get)

    assert geocode.geocode_address(conn, "Nigdzie 1", "Nikąd") is None
    assert geocode.geocode_address(conn, "Nigdzie 1", "Nikąd") is None
    assert len(calls) == 1  # "brak trafienia" też jest cache'owany


def test_geocode_address_network_failure_returns_none_not_cached(conn, monkeypatch):
    calls = []

    def boom(*a, **kw):
        calls.append(1)
        raise OSError("timeout")
    monkeypatch.setattr(geocode.requests, "get", boom)

    assert geocode.geocode_address(conn, "Coś", "Miasto") is None
    assert geocode.geocode_address(conn, "Coś", "Miasto") is None
    assert len(calls) == 2  # błąd sieci NIE cache'uje się — próbuje ponownie


def test_geocode_address_empty_input_returns_none_without_request(conn, monkeypatch):
    monkeypatch.setattr(
        geocode.requests, "get",
        lambda *a, **kw: pytest.fail("puste dane nie powinny odpytywać sieci"))
    assert geocode.geocode_address(conn, "", "") is None
