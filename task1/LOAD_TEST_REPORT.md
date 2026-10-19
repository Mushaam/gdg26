# Load Test Report — 10,000 readings burst

**Date:** 2026-10-19  
**Machine:** local Linux laptop  
**Stack:** Flask (threaded) + SQLite (WAL) + in-memory queue worker  
**Command:** `python load_test.py` against `http://127.0.0.1:5000`

## Method

1. Start the API (`python app.py`).
2. Run `python load_test.py`, which:
   - Builds a JSON list of **10,000** readings
   - Sends them in **one** `POST /api/readings/batch`
   - Measures **accept latency** (time until HTTP `202`)
   - Waits until the queue is empty, no batch is in flight, and SQLite shows +10,000 rows
   - Measures **drain time** (persist + rule evaluation)

Two scenarios were measured: no alert rules, then with rules enabled.

## Results

### Scenario A — ingest only (no alert rules)

| Metric | Value |
|--------|------:|
| Readings in burst | 10,000 |
| HTTP status | 202 Accepted |
| Accept latency | **33.0 ms** |
| Persist / drain time | **221.5 ms** |
| Rows added to SQLite | 10,000 |
| Result | **PASS** (accepted within 1 second and persisted) |

### Scenario B — ingest + evaluation (2 rules enabled)

Rules active during the second burst:

- threshold: `temperature > 30`
- window: average `temperature` over 10 minutes `> 28`

| Metric | Value |
|--------|------:|
| Readings in burst | 10,000 |
| HTTP status | 202 Accepted |
| Accept latency | **34.0 ms** |
| Persist + evaluate drain time | **3561.2 ms** |
| Rows added to SQLite | 10,000 |
| Alerts created (deduped) | 60 |
| Result | **PASS** (accepted within 1 second and persisted) |

## Interpretation

- **Burst goal met:** accepting 10,000 readings completed in ~33–34 ms, well under one second. The HTTP path only validates and enqueues; it does not wait for SQLite.
- **Persistence is fast when rules are off:** ~0.22 s to batch-insert 10k rows.
- **Rule evaluation dominates drain time:** with a window rule, drain rose to ~3.6 s because averages are queried from SQLite and checked per reading until an active alert exists for each sensor.
- **Alert dedupe kept the system usable:** 60 open alerts instead of thousands of duplicates for the same rule/sensor pairs.

## How to reproduce

```bash
source .venv/bin/activate
python app.py          # terminal 1
python load_test.py    # terminal 2
```

Numbers will vary by disk and CPU; accept latency should remain far below 1 second on typical hardware.

## Notes / limits

- This test uses one batch POST (10k in one HTTP body), which matches “burst of 10,000 readings in one second” from the client’s point of view.
- The in-memory queue is process-local: a crash before flush can lose queued readings (see `ARCHITECTURE.md`).
- Flask’s development server is for local demo; production would use a WSGI server, still with a single owner of the queue unless the queue is externalized.
