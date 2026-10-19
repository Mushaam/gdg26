# Campus Sensor Monitoring and Alerts

Flask + SQLite backend with a barebones vanilla JavaScript organizer UI.

Reads the problem statement in `GDG_Tech_Wildcard_Entry-6.pdf`.  
Architecture, workflows, and trade-offs: **[ARCHITECTURE.md](ARCHITECTURE.md)**.  
Burst test results: **[LOAD_TEST_REPORT.md](LOAD_TEST_REPORT.md)**.

## Requirements

- Python 3.10+ recommended
- `pip` available

## Install

```bash
cd /home/mushaam/Desktop/task1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the server

```bash
source .venv/bin/activate
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) for the organizer UI.

The SQLite file `campus_sensors.db` is created automatically on first run.

## API quick reference

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/readings` | Queue one reading |
| POST | `/api/readings/batch` | Queue many readings |
| GET | `/api/readings` | Recent readings |
| GET/POST | `/api/rules` | List / create rules |
| PUT | `/api/rules/<id>` | Edit a rule |
| POST | `/api/rules/<id>/enable` | Enable rule |
| POST | `/api/rules/<id>/disable` | Disable rule |
| GET | `/api/alerts` | List alerts (`?status=open`) |
| POST | `/api/alerts/<id>/acknowledge` | Acknowledge |
| POST | `/api/alerts/<id>/resolve` | Resolve |
| GET | `/api/stats` | Queue / DB counters |

### Example reading

```bash
curl -X POST http://127.0.0.1:5000/api/readings \
  -H 'Content-Type: application/json' \
  -d '{
    "sensorId": "library-floor-2",
    "type": "temperature",
    "value": 34.5,
    "timestamp": "2026-10-12T10:30:00Z"
  }'
```

### Example threshold rule

```bash
curl -X POST http://127.0.0.1:5000/api/rules \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Hot library",
    "sensor_id": "library-floor-2",
    "metric_type": "temperature",
    "rule_kind": "threshold",
    "operator": "gt",
    "threshold": 30,
    "enabled": true
  }'
```

### Example window rule

```bash
curl -X POST http://127.0.0.1:5000/api/rules \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Warm average",
    "metric_type": "temperature",
    "rule_kind": "window",
    "operator": "gt",
    "threshold": 28,
    "window_minutes": 10,
    "enabled": true
  }'
```

## Sensor simulator

With the server running:

```bash
# 50 individual posts
python simulator.py

# One batch of 100
python simulator.py --count 100 --batch
```

The UI also has a **Send 20 simulated readings** button.

## Load test (10,000 readings)

```bash
python load_test.py
```

This posts 10,000 readings in one batch, measures accept latency, waits for SQLite persistence, and prints a PASS/PARTIAL/FAIL line. Copy numbers into `LOAD_TEST_REPORT.md` if you re-run on another machine.

## Project layout

```
app.py              Flask routes
database.py         SQLite schema and queries
worker.py           In-memory queue + background batch writer
evaluator.py        Threshold and window rule checks
simulator.py        CLI sensor simulator
load_test.py        10k burst test
static/             Vanilla HTML/CSS/JS UI
ARCHITECTURE.md     Design, workflow, trade-offs
LOAD_TEST_REPORT.md Burst performance notes
README.md           This file
```
