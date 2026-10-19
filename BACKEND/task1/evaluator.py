"""Rule evaluation: threshold (one reading) and window (average over time)."""

from datetime import datetime, timedelta, timezone

import database as db


def _parse_ts(value):
    """Parse ISO timestamp into aware UTC datetime."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _fmt_ts(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compare(operator, left, right):
    if operator == "gt":
        return left > right
    if operator == "gte":
        return left >= right
    if operator == "lt":
        return left < right
    if operator == "lte":
        return left <= right
    if operator == "eq":
        return left == right
    return False


def rule_applies_to_reading(rule, reading):
    """Does this rule care about this reading's sensor and metric type?"""
    if rule["metric_type"] != reading["type"]:
        return False
    sensor_id = rule.get("sensor_id")
    if sensor_id and sensor_id != reading["sensor_id"]:
        return False
    return True


def evaluate_threshold(rule, reading):
    return compare(rule["operator"], reading["value"], rule["threshold"])


def evaluate_window(rule, reading):
    """Average of recent readings for this sensor+type vs threshold."""
    end = _parse_ts(reading["timestamp"])
    start = end - timedelta(minutes=int(rule["window_minutes"] or 0))
    avg = db.average_in_window(
        reading["sensor_id"],
        reading["type"],
        _fmt_ts(start),
        _fmt_ts(end),
    )
    if avg is None:
        return False, None
    return compare(rule["operator"], avg, rule["threshold"]), avg


def evaluate_reading_against_rules(reading, rules):
    """
    Return list of alert dicts to create (caller still checks dedupe).
    Each item: rule_id, sensor_id, metric_type, trigger_value, message
    """
    created = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for rule in rules:
        if not rule_applies_to_reading(rule, reading):
            continue

        # Skip work if an alert is already open/acknowledged for this pair
        if db.has_active_alert(rule["id"], reading["sensor_id"]):
            continue

        kind = rule["rule_kind"]
        fired = False
        trigger_value = reading["value"]

        if kind == "threshold":
            fired = evaluate_threshold(rule, reading)
        elif kind == "window":
            fired, avg = evaluate_window(rule, reading)
            if avg is not None:
                trigger_value = avg
        else:
            continue

        if not fired:
            continue

        op = rule["operator"]
        msg = (
            f"Rule '{rule['name']}' fired for {reading['sensor_id']} "
            f"({reading['type']}): {trigger_value} {op} {rule['threshold']}"
        )
        created.append(
            {
                "rule_id": rule["id"],
                "sensor_id": reading["sensor_id"],
                "metric_type": reading["type"],
                "trigger_value": trigger_value,
                "message": msg,
                "created_at": now,
                "updated_at": now,
            }
        )
    return created
