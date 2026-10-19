"""SQLite helpers: schema, connections, and simple queries."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "campus_sensors.db"


def get_connection():
    """Open a connection with WAL mode and row access by column name."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    """Create tables if they do not exist."""
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id TEXT NOT NULL,
                type TEXT NOT NULL,
                value REAL NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_readings_sensor_type_ts
                ON readings (sensor_id, type, timestamp);

            CREATE TABLE IF NOT EXISTS alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sensor_id TEXT,
                metric_type TEXT NOT NULL,
                rule_kind TEXT NOT NULL,
                operator TEXT NOT NULL,
                threshold REAL NOT NULL,
                window_minutes INTEGER,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                sensor_id TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                trigger_value REAL NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (rule_id) REFERENCES alert_rules(id)
            );

            CREATE INDEX IF NOT EXISTS idx_alerts_status
                ON alerts (status);
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_readings_batch(readings):
    """Insert many readings in one transaction. Each item is a dict."""
    if not readings:
        return 0
    conn = get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO readings (sensor_id, type, value, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            [
                (r["sensor_id"], r["type"], r["value"], r["timestamp"])
                for r in readings
            ],
        )
        conn.commit()
        return len(readings)
    finally:
        conn.close()


def count_readings():
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM readings").fetchone()
        return row["c"]
    finally:
        conn.close()


def recent_readings(limit=50):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, sensor_id, type, value, timestamp
            FROM readings
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_rules():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM alert_rules ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_rule(rule_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM alert_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_rule(data):
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO alert_rules (
                name, sensor_id, metric_type, rule_kind, operator,
                threshold, window_minutes, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"],
                data.get("sensor_id"),
                data["metric_type"],
                data["rule_kind"],
                data["operator"],
                data["threshold"],
                data.get("window_minutes"),
                1 if data.get("enabled", True) else 0,
                data["created_at"],
                data["updated_at"],
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_rule(rule_id, data):
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE alert_rules
            SET name = ?, sensor_id = ?, metric_type = ?, rule_kind = ?,
                operator = ?, threshold = ?, window_minutes = ?,
                enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                data["name"],
                data.get("sensor_id"),
                data["metric_type"],
                data["rule_kind"],
                data["operator"],
                data["threshold"],
                data.get("window_minutes"),
                1 if data.get("enabled", True) else 0,
                data["updated_at"],
                rule_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def set_rule_enabled(rule_id, enabled, updated_at):
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE alert_rules
            SET enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (1 if enabled else 0, updated_at, rule_id),
        )
        conn.commit()
    finally:
        conn.close()


def enabled_rules():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM alert_rules WHERE enabled = 1"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def average_in_window(sensor_id, metric_type, start_timestamp, end_timestamp):
    """Average of readings for sensor+type between two ISO UTC timestamps."""
    conn = get_connection()
    try:
        # ISO-8601 UTC strings (…Z) compare correctly as text.
        row = conn.execute(
            """
            SELECT AVG(value) AS avg_value, COUNT(*) AS n
            FROM readings
            WHERE sensor_id = ?
              AND type = ?
              AND timestamp >= ?
              AND timestamp <= ?
            """,
            (sensor_id, metric_type, start_timestamp, end_timestamp),
        ).fetchone()
        if not row or row["n"] == 0:
            return None
        return row["avg_value"]
    finally:
        conn.close()


def has_active_alert(rule_id, sensor_id):
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT id FROM alerts
            WHERE rule_id = ? AND sensor_id = ?
              AND status IN ('open', 'acknowledged')
            LIMIT 1
            """,
            (rule_id, sensor_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def create_alert(data):
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO alerts (
                rule_id, sensor_id, metric_type, trigger_value,
                message, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                data["rule_id"],
                data["sensor_id"],
                data["metric_type"],
                data["trigger_value"],
                data["message"],
                data["created_at"],
                data["updated_at"],
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_alerts(status=None, limit=100):
    conn = get_connection()
    try:
        if status:
            rows = conn.execute(
                """
                SELECT * FROM alerts
                WHERE status = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM alerts
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_alert_status(alert_id, status, updated_at):
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            UPDATE alerts
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, updated_at, alert_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
