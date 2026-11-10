"""Apply db/schema.sql and seed the device inventory.

    python -m backend.db.init_db

Safe to run repeatedly: every statement in the schema is idempotent and
devices are matched by hostname.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from backend.db import crud
from backend.db.session import SessionLocal, engine

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"

# A small but realistic network: core/edge routing, switching, a firewall
# and the servers behind it. link_capacity_mbps drives interface utilisation.
DEVICES = [
    {"hostname": "core-rtr-01", "device_type": "router", "ip_address": "10.0.0.1",
     "location": "DC1", "link_capacity_mbps": 10000},
    {"hostname": "edge-rtr-01", "device_type": "router", "ip_address": "10.0.0.2",
     "location": "DC1", "link_capacity_mbps": 1000},
    {"hostname": "dist-sw-01", "device_type": "switch", "ip_address": "10.0.1.1",
     "location": "DC1", "link_capacity_mbps": 10000},
    {"hostname": "access-sw-01", "device_type": "switch", "ip_address": "10.0.1.2",
     "location": "Office", "link_capacity_mbps": 1000},
    {"hostname": "fw-01", "device_type": "firewall", "ip_address": "10.0.0.254",
     "location": "DC1", "link_capacity_mbps": 1000},
    {"hostname": "web-srv-01", "device_type": "server", "ip_address": "10.0.2.10",
     "location": "DC1", "link_capacity_mbps": 1000},
    {"hostname": "app-srv-01", "device_type": "server", "ip_address": "10.0.2.20",
     "location": "DC1", "link_capacity_mbps": 1000},
    {"hostname": "db-srv-01", "device_type": "server", "ip_address": "10.0.2.30",
     "location": "DC1", "link_capacity_mbps": 1000},
]


def apply_schema() -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    # The file manages its own BEGIN/COMMIT, so run it on a raw DBAPI connection.
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql)
        raw.commit()
    finally:
        raw.close()


def seed_devices() -> int:
    with SessionLocal.begin() as session:
        return crud.upsert_devices(session, DEVICES)


def main() -> None:
    apply_schema()
    print(f"Schema applied from {SCHEMA_PATH.name}")
    added = seed_devices()
    with SessionLocal() as session:
        total = session.execute(text("SELECT count(*) FROM devices")).scalar_one()
    print(f"Devices: {added} added, {total} total")


if __name__ == "__main__":
    main()
