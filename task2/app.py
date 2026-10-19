"""
Workshop registration API + durable notification outbox worker.

Registration is saved and a notification_jobs row is written in the same
transaction. The HTTP response returns immediately. A background thread
delivers pending jobs to the mock notifier, with retries.
"""

import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "workshop.db"
STATIC_DIR = BASE_DIR / "static"

NOTIFIER_URL = os.environ.get("NOTIFIER_URL", "http://127.0.0.1:5001/notify")
WORKER_POLL_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 3.0
MAX_ATTEMPTS = 8
BATCH_SIZE = 10

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS workshops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workshop_id INTEGER NOT NULL,
            member_name TEXT NOT NULL,
            member_email TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (workshop_id, member_email),
            FOREIGN KEY (workshop_id) REFERENCES workshops(id)
        );

        CREATE TABLE IF NOT EXISTS notification_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workshop_id INTEGER NOT NULL,
            member_name TEXT NOT NULL,
            member_email TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_attempt_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            FOREIGN KEY (workshop_id) REFERENCES workshops(id)
        );
        """
    )
    conn.commit()
    conn.close()


def row_to_dict(row):
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/workshops")
def list_workshops():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT w.*,
               COUNT(r.id) AS registration_count
        FROM workshops w
        LEFT JOIN registrations r ON r.workshop_id = w.id
        GROUP BY w.id
        ORDER BY w.id DESC
        """
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/workshops")
def create_workshop():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO workshops (title, status, created_at) VALUES (?, 'open', ?)",
        (title, now_iso()),
    )
    conn.commit()
    workshop = conn.execute(
        "SELECT * FROM workshops WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    conn.close()
    return jsonify(row_to_dict(workshop)), 201


@app.post("/api/workshops/<int:workshop_id>/register")
def register(workshop_id):
    data = request.get_json(silent=True) or {}
    name = (data.get("member_name") or "").strip()
    email = (data.get("member_email") or "").strip().lower()

    if not name or not email:
        return jsonify({"error": "member_name and member_email are required"}), 400

    conn = get_db()
    workshop = conn.execute(
        "SELECT * FROM workshops WHERE id = ?", (workshop_id,)
    ).fetchone()

    if workshop is None:
        conn.close()
        return jsonify({"error": "workshop not found"}), 404

    if workshop["status"] != "open":
        conn.close()
        return jsonify({"error": "workshop is not open for registration"}), 400

    created = now_iso()
    try:
        # Same transaction: registration + pending notification job.
        # If registration fails (duplicate), no confirmation job is created.
        cur = conn.execute(
            """
            INSERT INTO registrations
                (workshop_id, member_name, member_email, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (workshop_id, name, email, created),
        )
        registration_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO notification_jobs
                (workshop_id, member_name, member_email, event_type,
                 status, attempts, next_attempt_at, created_at)
            VALUES (?, ?, ?, 'registration_confirmation',
                    'pending', 0, ?, ?)
            """,
            (workshop_id, name, email, created, created),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({"error": "already registered for this workshop"}), 409

    registration = conn.execute(
        "SELECT * FROM registrations WHERE id = ?", (registration_id,)
    ).fetchone()
    conn.close()

    # Return immediately — do not wait for the mock notifier.
    return jsonify(row_to_dict(registration)), 201


@app.post("/api/workshops/<int:workshop_id>/cancel")
def cancel_workshop(workshop_id):
    conn = get_db()
    workshop = conn.execute(
        "SELECT * FROM workshops WHERE id = ?", (workshop_id,)
    ).fetchone()

    if workshop is None:
        conn.close()
        return jsonify({"error": "workshop not found"}), 404

    if workshop["status"] == "cancelled":
        conn.close()
        return jsonify({"error": "workshop already cancelled"}), 400

    created = now_iso()
    members = conn.execute(
        """
        SELECT member_name, member_email
        FROM registrations
        WHERE workshop_id = ?
        """,
        (workshop_id,),
    ).fetchall()

    conn.execute(
        "UPDATE workshops SET status = 'cancelled' WHERE id = ?",
        (workshop_id,),
    )

    # One independent job per member — failure for one does not block others.
    for m in members:
        conn.execute(
            """
            INSERT INTO notification_jobs
                (workshop_id, member_name, member_email, event_type,
                 status, attempts, next_attempt_at, created_at)
            VALUES (?, ?, ?, 'workshop_cancelled',
                    'pending', 0, ?, ?)
            """,
            (workshop_id, m["member_name"], m["member_email"], created, created),
        )

    conn.commit()
    updated = conn.execute(
        "SELECT * FROM workshops WHERE id = ?", (workshop_id,)
    ).fetchone()
    conn.close()

    return jsonify(
        {
            "workshop": row_to_dict(updated),
            "notifications_enqueued": len(members),
        }
    )


@app.get("/api/registrations")
def list_registrations():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT r.*, w.title AS workshop_title, w.status AS workshop_status
        FROM registrations r
        JOIN workshops w ON w.id = r.workshop_id
        ORDER BY r.id DESC
        """
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.get("/api/notifications")
def list_notifications():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT n.*, w.title AS workshop_title
        FROM notification_jobs n
        JOIN workshops w ON w.id = n.workshop_id
        ORDER BY n.id DESC
        """
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Background worker — durable retries against the mock notifier
# ---------------------------------------------------------------------------

def backoff_seconds(attempts):
    # 2, 4, 8, 16, ... capped at 60 seconds
    return min(2 ** attempts, 60)


def deliver_job(conn, job):
    payload = {
        "job_id": job["id"],
        "event_type": job["event_type"],
        "workshop_id": job["workshop_id"],
        "member_name": job["member_name"],
        "member_email": job["member_email"],
    }

    try:
        response = requests.post(
            NOTIFIER_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            conn.execute(
                """
                UPDATE notification_jobs
                SET status = 'sent',
                    attempts = attempts + 1,
                    last_error = NULL,
                    sent_at = ?
                WHERE id = ?
                """,
                (now_iso(), job["id"]),
            )
            conn.commit()
            print(f"[worker] job {job['id']} sent", flush=True)
            return

        error = f"HTTP {response.status_code}: {response.text[:200]}"
    except requests.RequestException as exc:
        error = str(exc)

    attempts = job["attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        conn.execute(
            """
            UPDATE notification_jobs
            SET status = 'dead',
                attempts = ?,
                last_error = ?
            WHERE id = ?
            """,
            (attempts, error, job["id"]),
        )
        print(f"[worker] job {job['id']} marked dead: {error}", flush=True)
    else:
        delay = backoff_seconds(attempts)
        next_at = datetime.now(timezone.utc).timestamp() + delay
        next_iso = datetime.fromtimestamp(next_at, timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE notification_jobs
            SET attempts = ?,
                last_error = ?,
                next_attempt_at = ?
            WHERE id = ?
            """,
            (attempts, error, next_iso, job["id"]),
        )
        print(
            f"[worker] job {job['id']} retry in {delay}s "
            f"(attempt {attempts}): {error}",
            flush=True,
        )
    conn.commit()


def worker_loop(stop_event):
    print("[worker] started", flush=True)
    while not stop_event.is_set():
        conn = get_db()
        try:
            jobs = conn.execute(
                """
                SELECT * FROM notification_jobs
                WHERE status = 'pending'
                  AND next_attempt_at <= ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (now_iso(), BATCH_SIZE),
            ).fetchall()

            for job in jobs:
                # Process independently so one failure never skips the rest.
                deliver_job(conn, job)
        except Exception as exc:
            print(f"[worker] unexpected error: {exc}", flush=True)
        finally:
            conn.close()

        stop_event.wait(WORKER_POLL_SECONDS)

    print("[worker] stopped", flush=True)


_stop_event = threading.Event()
_worker_thread = None


def start_worker():
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=worker_loop, args=(_stop_event,), daemon=True
    )
    _worker_thread.start()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

init_db()
start_worker()


if __name__ == "__main__":
    # use_reloader=False so we do not start two worker threads.
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
