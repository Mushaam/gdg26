"""Campus Sensor Monitoring API + static organizer UI."""

from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory

import database as db
import worker

app = Flask(__name__, static_folder="static", static_url_path="")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_reading(raw):
    """Accept camelCase (spec example) or snake_case. Return normalized dict or None."""
    sensor_id = raw.get("sensorId") or raw.get("sensor_id")
    metric_type = raw.get("type")
    value = raw.get("value")
    timestamp = raw.get("timestamp")

    if not sensor_id or not metric_type or value is None or not timestamp:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    return {
        "sensor_id": str(sensor_id),
        "type": str(metric_type),
        "value": value,
        "timestamp": str(timestamp),
    }


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/readings")
def post_reading():
    data = request.get_json(silent=True) or {}
    reading = normalize_reading(data)
    if not reading:
        return jsonify({"error": "Invalid reading. Need sensorId, type, value, timestamp."}), 400
    worker.enqueue_readings([reading])
    return jsonify({"status": "queued", "count": 1}), 202


@app.post("/api/readings/batch")
def post_readings_batch():
    data = request.get_json(silent=True)
    if not isinstance(data, list):
        return jsonify({"error": "Body must be a JSON list of readings."}), 400

    batch = []
    for item in data:
        reading = normalize_reading(item or {})
        if reading:
            batch.append(reading)

    if not batch:
        return jsonify({"error": "No valid readings in batch."}), 400

    worker.enqueue_readings(batch)
    return jsonify({"status": "queued", "count": len(batch)}), 202


@app.get("/api/readings")
def get_readings():
    limit = request.args.get("limit", 50, type=int)
    return jsonify(db.recent_readings(limit=min(limit, 500)))


@app.get("/api/stats")
def get_stats():
    stats = worker.get_stats()
    stats["readings_in_db"] = db.count_readings()
    return jsonify(stats)


@app.get("/api/rules")
def get_rules():
    return jsonify(db.list_rules())


@app.post("/api/rules")
def post_rule():
    data = request.get_json(silent=True) or {}
    required = ["name", "metric_type", "rule_kind", "operator", "threshold"]
    for key in required:
        if key not in data:
            return jsonify({"error": f"Missing field: {key}"}), 400

    if data["rule_kind"] not in ("threshold", "window"):
        return jsonify({"error": "rule_kind must be 'threshold' or 'window'."}), 400
    if data["operator"] not in ("gt", "gte", "lt", "lte", "eq"):
        return jsonify({"error": "Invalid operator."}), 400
    if data["rule_kind"] == "window" and not data.get("window_minutes"):
        return jsonify({"error": "window rules need window_minutes."}), 400

    now = utc_now()
    rule_id = db.create_rule(
        {
            "name": data["name"],
            "sensor_id": data.get("sensor_id") or None,
            "metric_type": data["metric_type"],
            "rule_kind": data["rule_kind"],
            "operator": data["operator"],
            "threshold": float(data["threshold"]),
            "window_minutes": int(data["window_minutes"]) if data.get("window_minutes") else None,
            "enabled": data.get("enabled", True),
            "created_at": now,
            "updated_at": now,
        }
    )
    return jsonify(db.get_rule(rule_id)), 201


@app.put("/api/rules/<int:rule_id>")
def put_rule(rule_id):
    existing = db.get_rule(rule_id)
    if not existing:
        return jsonify({"error": "Rule not found."}), 404

    data = request.get_json(silent=True) or {}
    now = utc_now()
    db.update_rule(
        rule_id,
        {
            "name": data.get("name", existing["name"]),
            "sensor_id": data.get("sensor_id", existing["sensor_id"]),
            "metric_type": data.get("metric_type", existing["metric_type"]),
            "rule_kind": data.get("rule_kind", existing["rule_kind"]),
            "operator": data.get("operator", existing["operator"]),
            "threshold": float(data.get("threshold", existing["threshold"])),
            "window_minutes": (
                int(data["window_minutes"])
                if data.get("window_minutes") is not None
                else existing["window_minutes"]
            ),
            "enabled": data.get("enabled", bool(existing["enabled"])),
            "updated_at": now,
        },
    )
    return jsonify(db.get_rule(rule_id))


@app.post("/api/rules/<int:rule_id>/enable")
def enable_rule(rule_id):
    if not db.get_rule(rule_id):
        return jsonify({"error": "Rule not found."}), 404
    db.set_rule_enabled(rule_id, True, utc_now())
    return jsonify(db.get_rule(rule_id))


@app.post("/api/rules/<int:rule_id>/disable")
def disable_rule(rule_id):
    if not db.get_rule(rule_id):
        return jsonify({"error": "Rule not found."}), 404
    db.set_rule_enabled(rule_id, False, utc_now())
    return jsonify(db.get_rule(rule_id))


@app.get("/api/alerts")
def get_alerts():
    status = request.args.get("status")
    return jsonify(db.list_alerts(status=status))


@app.post("/api/alerts/<int:alert_id>/acknowledge")
def acknowledge_alert(alert_id):
    ok = db.update_alert_status(alert_id, "acknowledged", utc_now())
    if not ok:
        return jsonify({"error": "Alert not found."}), 404
    return jsonify({"id": alert_id, "status": "acknowledged"})


@app.post("/api/alerts/<int:alert_id>/resolve")
def resolve_alert(alert_id):
    ok = db.update_alert_status(alert_id, "resolved", utc_now())
    if not ok:
        return jsonify({"error": "Alert not found."}), 404
    return jsonify({"id": alert_id, "status": "resolved"})


def create_app():
    db.init_db()
    worker.start_worker()
    return app


if __name__ == "__main__":
    create_app()
    # threaded=True so HTTP stays responsive while the worker runs
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
