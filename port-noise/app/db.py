"""SQLite 连接与初始化。"""
import os
import sqlite3

from flask import current_app, g


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        g.db = conn
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(conn, schema_path=None):
    if schema_path is None:
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    # WAL：允许服务进程写库的同时其他连接读库（例如自查脚本）
    conn.execute("PRAGMA journal_mode = WAL")
    conn.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db(get_db())


def is_seed_loaded(conn):
    return conn.execute("SELECT 1 FROM peaks LIMIT 1").fetchone() is not None
