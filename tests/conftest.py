"""Shared fixtures.

``db_session`` gives a Session inside a transaction that is rolled back
after the test, so tests can commit freely without leaving data behind.
It skips the test if PostgreSQL or the schema is not available.
"""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session


def synthetic_flows(n: int, label: int, seed: int = 0) -> pd.DataFrame:
    """UNSW-NB15-shaped rows; attacks have much larger load, packets and loss."""
    rng = np.random.default_rng(seed + label)
    scale = 1 if label == 0 else 50
    return pd.DataFrame(
        {
            "id": np.arange(n),
            "proto": rng.choice(["tcp", "udp"], n),
            "service": rng.choice(["http", "dns", "-"], n),
            "state": rng.choice(["FIN", "CON", "INT"], n),
            "dur": rng.exponential(0.5, n),
            "spkts": rng.integers(1, 20, n) * scale,
            "dpkts": rng.integers(0, 20, n) * scale,
            "sload": rng.exponential(1e5, n) * scale,
            "dload": rng.exponential(1e4, n) * scale,
            "sloss": rng.integers(0, 2, n) * scale,
            "dloss": rng.integers(0, 2, n) * scale,
            "sjit": rng.exponential(5, n),
            "djit": rng.exponential(5, n),
            "tcprtt": rng.uniform(0, 0.2, n),
            "attack_cat": "Normal" if label == 0 else "DoS",
            "label": label,
        }
    )


@pytest.fixture
def db_session():
    from backend.db.session import engine

    try:
        conn = engine.connect()
    except Exception as exc:  # no server or bad credentials
        pytest.skip(f"PostgreSQL not available: {exc.__class__.__name__}")
    # Open the outer transaction first: any query (even the probe below)
    # would otherwise autobegin one and make conn.begin() fail.
    trans = conn.begin()
    try:
        conn.execute(text("SELECT 1 FROM devices LIMIT 1"))
    except Exception as exc:  # schema not applied
        trans.rollback()
        conn.close()
        pytest.skip(f"Schema not applied: {exc.__class__.__name__}")
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()
