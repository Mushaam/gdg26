"""
CLI sensor simulator.

Usage (server must already be running):
  python simulator.py
  python simulator.py --count 100 --interval 0.05
"""

import argparse
import random
import time
from datetime import datetime, timezone

import requests

BASE = "http://127.0.0.1:5000"
SENSORS = ["library-floor-2", "cafeteria", "gym", "lab-a", "dorm-b"]
TYPES = ["temperature", "air_quality", "occupancy"]


def make_reading(sensor_id=None, metric_type=None):
    sensor_id = sensor_id or random.choice(SENSORS)
    metric_type = metric_type or random.choice(TYPES)
    if metric_type == "temperature":
        value = round(random.uniform(18, 40), 1)
    elif metric_type == "air_quality":
        value = round(random.uniform(5, 120), 1)
    else:
        value = float(random.randint(0, 100))

    return {
        "sensorId": sensor_id,
        "type": metric_type,
        "value": value,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main():
    parser = argparse.ArgumentParser(description="Campus sensor simulator")
    parser.add_argument("--count", type=int, default=50, help="How many readings to send")
    parser.add_argument("--interval", type=float, default=0.1, help="Seconds between posts")
    parser.add_argument("--batch", action="store_true", help="Send all as one batch")
    args = parser.parse_args()

    readings = [make_reading() for _ in range(args.count)]

    if args.batch:
        r = requests.post(BASE + "/api/readings/batch", json=readings, timeout=30)
        print(r.status_code, r.json())
        return

    for i, reading in enumerate(readings, 1):
        r = requests.post(BASE + "/api/readings", json=reading, timeout=10)
        print(i, r.status_code, r.json())
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
