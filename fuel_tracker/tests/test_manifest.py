"""PWA manifest (0.10.0) — Flask test client."""
import json
from pathlib import Path

import pytest

from fuel_tracker import db as dbm
from fuel_tracker.web import create_app


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "web.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.close()
    app = create_app(db_path=db_path, config={})
    app.testing = True
    return app.test_client()


def test_manifest_route_returns_json_with_ingress_aware_start_url(client):
    r = client.get("/manifest.webmanifest",
                   headers={"X-Ingress-Path": "/api/hassio_ingress/abc123"})
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["start_url"] == "/api/hassio_ingress/abc123/fillup-form"
    assert data["scope"] == "/api/hassio_ingress/abc123/"


def test_manifest_content_type_is_manifest_json(client):
    r = client.get("/manifest.webmanifest")
    assert r.mimetype == "application/manifest+json"


def test_manifest_linked_from_base_html_with_version_query(client):
    r = client.get("/")
    assert b'rel="manifest"' in r.data
    assert b"manifest.webmanifest" in r.data


def test_apple_meta_tags_present(client):
    r = client.get("/")
    assert b"apple-mobile-web-app-capable" in r.data
    assert b"apple-touch-icon" in r.data


def test_icon_files_exist_on_disk():
    icons_dir = (Path(__file__).resolve().parents[1] / "fuel_tracker" /
                "static" / "icons")
    for name in ("icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        assert (icons_dir / name).is_file()
