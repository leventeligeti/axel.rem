"""Standalone PostgreSQL connection — context manager, auto-commit/rollback."""
from contextlib import contextmanager

import psycopg2

from axel_rem import config


@contextmanager
def db():
    conn = psycopg2.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASS,
        connect_timeout=10,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
