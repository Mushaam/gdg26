"""In-memory ingest queue and background worker thread."""

import queue
import threading
import time
import traceback

import database as db
from evaluator import evaluate_reading_against_rules

# How many readings to pull from the queue per cycle
BATCH_SIZE = 500

# Shared queue — Flask puts, worker gets
reading_queue = queue.Queue()

_worker_started = False
_stats_lock = threading.Lock()
_stats = {
    "queued_total": 0,
    "processed_total": 0,
    "alerts_created_total": 0,
    "last_batch_size": 0,
    "queue_size": 0,
    "in_flight": 0,
}


def enqueue_readings(readings):
    """Put readings on the queue. Returns how many were queued."""
    for r in readings:
        reading_queue.put(r)
    with _stats_lock:
        _stats["queued_total"] += len(readings)
        _stats["queue_size"] = reading_queue.qsize()
    return len(readings)


def get_stats():
    with _stats_lock:
        snap = dict(_stats)
    snap["queue_size"] = reading_queue.qsize()
    return snap


def _drain_batch(max_items=BATCH_SIZE):
    batch = []
    while len(batch) < max_items:
        try:
            batch.append(reading_queue.get_nowait())
        except queue.Empty:
            break
    return batch


def process_batch(batch):
    """Insert readings, evaluate rules, create alerts."""
    if not batch:
        return 0, 0

    with _stats_lock:
        _stats["in_flight"] = len(batch)

    alerts_made = 0
    try:
        db.insert_readings_batch(batch)
        rules = db.enabled_rules()

        # Avoid creating many alerts for the same pair inside one batch
        seen_pairs = set()

        for reading in batch:
            for alert_data in evaluate_reading_against_rules(reading, rules):
                pair = (alert_data["rule_id"], alert_data["sensor_id"])
                if pair in seen_pairs:
                    continue
                if db.has_active_alert(pair[0], pair[1]):
                    seen_pairs.add(pair)
                    continue
                db.create_alert(alert_data)
                seen_pairs.add(pair)
                alerts_made += 1
    except Exception:
        # Keep the worker alive; print so demos can see failures
        traceback.print_exc()
    finally:
        with _stats_lock:
            _stats["processed_total"] += len(batch)
            _stats["alerts_created_total"] += alerts_made
            _stats["last_batch_size"] = len(batch)
            _stats["queue_size"] = reading_queue.qsize()
            _stats["in_flight"] = 0

    return len(batch), alerts_made


def _worker_loop():
    while True:
        batch = _drain_batch()
        if not batch:
            time.sleep(0.01)
            continue
        process_batch(batch)


def start_worker():
    """Start the background thread once."""
    global _worker_started
    if _worker_started:
        return
    t = threading.Thread(target=_worker_loop, name="reading-worker", daemon=True)
    t.start()
    _worker_started = True


def wait_until_idle(timeout_seconds=60):
    """Block until the queue is empty and no batch is in flight."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        stats = get_stats()
        if stats["queue_size"] == 0 and stats["in_flight"] == 0:
            return True
        time.sleep(0.02)
    return False
