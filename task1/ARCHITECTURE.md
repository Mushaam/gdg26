# Campus Sensor Monitoring — Architecture & Workflow

This document explains how the system works, why it is shaped this way, and the main throughput / reliability trade-offs.

## Goal

Ingest campus sensor readings (temperature, air quality, occupancy, etc.), evaluate organizer-defined alert rules, and let organizers manage alerts. The design targets short bursts of about **10,000 readings in one second**, while staying simple enough to read and explain.

## Stack

| Layer        | Choice                         | Why                                      |
|--------------|--------------------------------|------------------------------------------|
| API / app    | Flask                          | Small, familiar Python HTTP server       |
| Database     | SQLite (WAL mode)              | Zero ops, file-based, good enough locally|
| Frontend     | Vanilla HTML / CSS / JS        | No framework overhead                    |
| Simulator    | Python script + UI button      | Easy local testing                       |

## High-level architecture

```
                    ┌─────────────────────┐
                    │  Sensor simulator   │
                    │  (script or UI)     │
                    └──────────┬──────────┘
                               │ HTTP POST
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                         Flask app                            │
│  /api/readings        → enqueue (fast path)                  │
│  /api/readings/batch  → enqueue (fast path)                  │
│  /api/rules           → create / edit / enable / disable     │
│  /api/alerts          → list / acknowledge / resolve         │
│  /                    → static organizer UI                  │
└──────────┬───────────────────────────┬───────────────────────┘
           │                           │
           ▼                           ▼
┌─────────────────────�──┬───────────────────────────┬───────────────────────┘
           │                           │
           ▼                           ▼
┌─────────────────────┐     ┌─────────────────────┐
│  In-memory queue    │     │  SQLite database    │
│  (accept bursts)    │     │  readings, rules,   │
│                     │     │  alerts             │
└──────────┬──────────┘     └──────────▲──────────┘
           │                           │
           ▼                           │
┌─────────────────────┐                │
│  Background worker  │────────────────┘
│  batch insert       │
│  evaluate rules     │
│  create alerts      │
└─────────────────────┘
```

## Components

### 1. Ingest API (fast path)

- `POST /api/readings` — one reading
- `POST /api/readings/batch` — list of readings

These endpoints **do not** write every reading to SQLite before responding. They validate lightly, push items onto an in-memory queue, and return quickly (`202 Accepted` with how many were queued).

That is the main trick for surviving a 10k/sec burst: **accept first, persist later**.

### 2. Background worker

A daemon thread continuously:

1. Drains the queue (up to a batch size, e.g. 500).
2. Inserts the batch into `readings` with one SQLite transaction.
3. Evaluates enabled alert rules against those readings.
4. Inserts new rows into `alerts` when rules fire.

If the worker falls behind, the queue grows in memory. That is intentional: the HTTP layer stays responsive while persistence catches up.

### 3. Rule evaluation

Two rule kinds:

| Kind              | Meaning                                              | Example                                      |
|-------------------|------------------------------------------------------|----------------------------------------------|
| `threshold`       | Compare one reading to a number                      | temperature > 30                             |
| `window`          | Compare the **average** over the last N minutes      | avg temperature over 10 minutes > 28         |

Operators supported: `gt`, `gte`, `lt`, `lte`, `eq`.

A rule can target one `sensor_id` or all sensors of a given `metric_type` (when `sensor_id` is empty).

### 4. Alert lifecycle

```
  rule fires  →  alert created (status = open)
                      │
                      ▼
              organizer acknowledges  →  acknowledged
                      │
                      ▼
              organizer resolves      →  resolved
```

**Deduping:** if there is already an `open` or `acknowledged` alert for the same `rule_id` + `sensor_id`, we do **not** create another one. That keeps the alert list usable during noisy bursts. See trade-offs below.

### 5. Organizer UI (vanilla JS)

Single page that talks to the JSON API:

- Recent readings
- Create / edit / enable / disable rules
- View / acknowledge / resolve alerts
- Start a small in-browser simulator (posts random readings)

### 6. Load test

`load_test.py` posts 10,000 readings in one batch (and optionally timed concurrent posts) and reports accept latency, queue drain time, and how many rows landed in SQLite.

## Data model

```
readings
  id, sensor_id, type, value, timestamp

alert_rules
  id, name, sensor_id, metric_type,
  rule_kind ('threshold' | 'window'),
  operator, threshold, window_minutes,
  enabled, created_at, updated_at

alerts
  id, rule_id, sensor_id, metric_type,
  trigger_value, message, status,
  created_at, updated_at
```

## End-to-end workflows

### A. Ingest a reading

1. Client POSTs JSON reading(s).
2. Flask validates required fields (`sensorId`, `type`, `value`, `timestamp`).
3. Items are put on the queue; response returns immediately.
4. Worker batch-inserts into `readings`.
5. Worker loads enabled rules and evaluates:
   - **threshold:** compare this reading’s value.
   - **window:** `AVG(value)` for that sensor + type over `window_minutes`.
6. If a rule matches and no open/acknowledged alert exists for that pair, insert an alert.

### B. Manage a rule

1. Organizer creates or edits a rule via UI or API.
2. Rule is stored in SQLite immediately (management path is low volume).
3. On next worker cycle, the new/updated rule is used.

### C. Manage an alert

1. Organizer lists alerts (filter by status optional).
2. Acknowledge or resolve updates `status` and `updated_at`.

## Throughput & reliability trade-offs

These are the important design choices and what you give up for them.

### 1. In-memory queue vs write-on-request

| Approach                         | Throughput              | Reliability                         |
|----------------------------------|-------------------------|-------------------------------------|
| Write every reading in the HTTP handler | Slower under burst     | Durable as soon as `201` returns    |
| **Queue then background write (chosen)** | Fast accept under burst | Readings can be lost if the process dies before the worker flushes |

**Why we chose the queue:** the assignment asks for handling a 10k-in-one-second burst. SQLite (and disk) cannot always commit 10k individual transactions that fast on a laptop. Batching in a worker is the simple way to meet the burst goal without Redis/Kafka.

**Mitigation:** flush in small batches often; optional graceful shutdown that drains the queue (best-effort).

### 2. SQLite vs a client/server DB (Postgres, etc.)

| SQLite                                      | Postgres / similar                          |
|---------------------------------------------|---------------------------------------------|
| One file, no install, easy demo             | Better multi-writer concurrency             |
| WAL helps readers while one writer runs     | Horizontal scale, replication               |
| Single-writer bottleneck under heavy write  | More ops complexity                         |

**Chosen: SQLite + WAL + batched writes.** Good enough for a campus demo and load test on one machine. For many concurrent writers in production, you would move the queue to Redis/Kafka and the DB to Postgres.

### 3. Alert dedupe vs “alert on every match”

Spec wording: create an alert whenever a rule is satisfied.

If we literally insert one alert per matching reading during a 10k burst, the `alerts` table can explode (thousands of near-identical rows) and the UI becomes useless.

**Chosen:** one active (`open` / `acknowledged`) alert per `(rule_id, sensor_id)`. A new one is created only after the previous is `resolved` (or never existed).

**Trade-off:** fewer, clearer alerts; organizers may miss “how many times it fired” unless they look at readings. A production system might keep a `fire_count` or event log instead.

### 4. Window averages from SQLite vs in-memory buffers

| Query AVG from SQLite (chosen) | Keep ring buffers in RAM                    |
|--------------------------------|---------------------------------------------|
| Survives process restart       | Faster, no SQL for windows                  |
| Extra read load during eval    | Lost on crash; more code to keep correct    |

**Chosen: SQL `AVG` over the time window.** Simpler mental model: the database is the source of truth. Cost: each window rule does a query; with few rules this is fine. With hundreds of window rules at 10k/sec, you would add an in-memory rolling average.

### 5. Synchronous rule evaluation in the worker

Rules run in the same worker that inserts readings.

- **Pro:** simple, one pipeline, easy to explain.
- **Con:** a slow rule set delays draining the queue.

Alternative: separate “ingest worker” and “evaluate worker.” We skipped that to keep the code small.

### 6. No authentication

Organizer APIs are open on localhost. Fine for a coding challenge; not fine on a public network. Add auth before deploying.

### 7. Single process

Flask’s built-in server + one worker thread is for local demo and load testing. For real deployment use a production WSGI server (e.g. gunicorn) and still only **one** process that owns the queue/worker unless you externalize the queue.

## Performance expectations (local)

On a typical laptop, with batch insert + queue:

- Accepting 10,000 readings in one POST should return in well under one second.
- Persisting and evaluating those 10,000 should finish within a few seconds depending on disk and how many rules are enabled.
- Exact numbers are recorded in `LOAD_TEST_REPORT.md` after running `load_test.py`.

## File map

| File                 | Role                                      |
|----------------------|-------------------------------------------|
| `app.py`             | Flask routes, starts worker               |
| `database.py`        | Schema, connections, queries              |
| `worker.py`          | Queue + background batch processing       |
| `evaluator.py`       | Threshold / window rule logic             |
| `simulator.py`       | CLI sensor simulator                      |
| `load_test.py`       | 10k burst test                            |
| `static/`            | Barebones organizer UI                    |
| `ARCHITECTURE.md`    | This document                             |
| `README.md`          | Install and run                           |
| `LOAD_TEST_REPORT.md`| Burst test results                        |

## Design principles (kept on purpose)

1. **Few files, plain functions** — no ORM, no JS framework.
2. **Accept fast, write in batches** — meets the burst requirement.
3. **SQLite is the source of truth** — readings and rules live there.
4. **Document trade-offs** — speed and simplicity over perfect durability and scale.
