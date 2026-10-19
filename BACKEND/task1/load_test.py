"""
Burst load test: queue 10,000 readings in one second (one batch POST),
then wait for the worker to persist them.

Usage (server already running on :5000):
  python load_test.py
"""

import time
from datetime import datetime, timezone

import requests

BASE = "http://127.0.0.1:5000"
BURST = 10000


def make_batch(n):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    batch = []
    for i in range(n):
        batch.append(
            {
                "sensorId": f"sensor-{(i % 50) + 1}",
                "type": ["temperature", "air_quality", "occupancy"][i % 3],
                "value": 20 + (i % 25),
                "timestamp": ts,
            }
        )
    return batch


def main():
    # Baseline count
    before = requests.get(BASE + "/api/stats", timeout=10).json()
    before_db = before.get("readings_in_db", 0)

    batch = make_batch(BURST)

    t0 = time.perf_counter()
    resp = requests.post(BASE + "/api/readings/batch", json=batch, timeout=60)
    accept_ms = (time.perf_counter() - t0) * 1000

    print("Accept status:", resp.status_code, resp.json())
    print(f"Accept latency for {BURST} readings: {accept_ms:.1f} ms")

    # Wait until queue drains, current batch finishes, and DB catches up
    t1 = time.perf_counter()
    deadline = time.time() + 120
    while time.time() < deadline:
        stats = requests.get(BASE + "/api/stats", timeout=10).json()
        queued_left = stats.get("queue_size", 0)
        in_flight = stats.get("in_flight", 0)
        in_db = stats.get("readings_in_db", 0)
        if queued_left == 0 and in_flight == 0 and in_db >= before_db + BURST:
            break
        time.sleep(0.05)
    drain_ms = (time.perf_counter() - t1) * 1000

    after = requests.get(BASE + "/api/stats", timeout=10).json()
    added = after.get("readings_in_db", 0) - before_db

    print(f"Persist + evaluate drain time: {drain_ms:.1f} ms")
    print(f"Readings added to DB: {added}")
    print("Final stats:", after)

    # Simple pass/fail for the burst accept goal
    if accept_ms <= 1000 and resp.status_code == 202 and added >= BURST:
        print("RESULT: PASS (accepted 10k within 1s and persisted)")
    elif resp.status_code == 202 and added >= BURST:
        print(
            f"RESULT: PARTIAL (persisted 10k, but accept took {accept_ms:.1f} ms > 1000 ms)"
        )
    else:
        print("RESULT: FAIL")


if __name__ == "__main__":
    main()
