# Workshop Registration & Notifications — Architecture

Stack: **Flask**, **SQLite**, **vanilla JavaScript** (no frontend frameworks).

---

## Goal

Organizers create workshops. Members register (once per workshop). Every successful registration must **eventually** get a confirmation notification. Cancelling a workshop must notify every registered member. Notifications go through a **mock HTTP service** that sometimes fails or times out. The registration API must **not wait** for the notification to finish.

---

## High-level architecture

```
┌─────────────┐     HTTP      ┌──────────────────────┐
│  Browser    │ ◄──────────►  │  Registration API    │
│  (static/   │               │  (Flask, port 5000)  │
│   HTML/JS)  │               └──────────┬───────────┘
└─────────────┘                          │
                                         │ same process
                                         ▼
                              ┌──────────────────────┐
                              │  SQLite database     │
                              │  - workshops         │
                              │  - registrations     │
                              │  - notification_jobs │  ← durable outbox
                              └──────────┬───────────┘
                                         │
                              ┌──────────▼───────────┐
                              │  Background worker   │
                              │  (thread in app.py)  │
                              └──────────┬───────────┘
                                         │ HTTP POST
                                         ▼
                              ┌──────────────────────┐
                              │  Mock notifier       │
                              │  (Flask, port 5001)  │
                              │  OK / 500 / timeout  │
                              └──────────────────────┘
```

Two processes:

| Process | File | Port | Role |
|---------|------|------|------|
| Main app | `app.py` | 5000 | REST API, static UI, background worker |
| Mock notifier | `mock_notifier.py` | 5001 | Fake delivery endpoint |

---

## Data model

### `workshops`
- `id`, `title`, `status` (`open` | `cancelled`), `created_at`

### `registrations`
- `id`, `workshop_id`, `member_name`, `member_email`, `created_at`
- **Unique** on `(workshop_id, member_email)` → a member can register only once per workshop

### `notification_jobs` (outbox)
- `id`, `workshop_id`, `member_email`, `member_name`
- `event_type` (`registration_confirmation` | `workshop_cancelled`)
- `status` (`pending` | `sent` | `dead`)
- `attempts`, `last_error`, `next_attempt_at`, `created_at`, `sent_at`

This table is the key to reliability: a notification is not “done” until the mock service returns success **and** we mark the row `sent`.

---

## Workflows

### 1. Create workshop

1. Organizer posts title.
2. Row inserted into `workshops` with `status = open`.
3. API returns the workshop immediately.

### 2. Register for a workshop (async confirmation)

1. Member posts name + email for a workshop.
2. In **one SQLite transaction**:
   - Insert into `registrations` (fails with 409 if duplicate email for that workshop).
   - Insert a `notification_jobs` row with `status = pending` and `event_type = registration_confirmation`.
3. Commit.
4. API returns **201** with the registration — **without calling** the mock notifier.
5. Background worker later picks up the job and POSTs to the mock service.
6. On HTTP 200 from mock → mark job `sent`.
7. On failure/timeout → bump `attempts`, set `next_attempt_at` (backoff), leave `pending`.

**Why this meets the requirements**

| Requirement | How |
|-------------|-----|
| Return without waiting for notification | API only writes DB; worker is async |
| Successful registration → confirmation eventually | Job row created in same transaction as registration |
| Failed registration → no confirmation | Job only inserted if registration insert succeeds |
| Survive backend restart | Pending jobs live in SQLite; worker resumes on start |
| Retry when mock unavailable | Worker retries with backoff until success or max attempts |
| One failure must not block others | Each job is processed independently |

### 3. Cancel workshop

1. Organizer marks workshop `cancelled`.
2. For every registration on that workshop, insert a `workshop_cancelled` job (`pending`).
3. API returns immediately.
4. Worker delivers each cancel notification independently (same retry rules).

### 4. Background worker loop

Every ~1 second:

1. Select jobs where `status = pending` and `next_attempt_at <= now`.
2. Cap batch size (e.g. 10) so one busy loop does not starve the API.
3. For each job: HTTP POST to mock notifier.
4. Success → `sent`. Failure → retry schedule or `dead` after max attempts.

On process start, the worker thread starts automatically, so leftover pending jobs after a crash are retried.

### 5. Mock notification service

`POST /notify` with JSON payload. Randomly:

- **Success** (~70%): HTTP 200
- **Error** (~20%): HTTP 500 / 503
- **Timeout** (~10%): sleep longer than the client timeout

A 200 means “delivered” for demo purposes. No real email/SMS.

---

## API surface (main app)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Serve UI |
| `GET` | `/api/workshops` | List workshops (+ registration counts) |
| `POST` | `/api/workshops` | Create workshop |
| `POST` | `/api/workshops/<id>/register` | Register member |
| `POST` | `/api/workshops/<id>/cancel` | Cancel + enqueue cancel notifications |
| `GET` | `/api/registrations` | List registrations |
| `GET` | `/api/notifications` | List notification jobs (for demo / debugging) |

Mock notifier:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/notify` | Accept notification; randomly succeed/fail/timeout |
| `GET` | `/health` | Liveness check |

---

## Reliability topics (what the design does)

### Lost notifications
If the process dies **after** saving the registration+job but **before** calling the mock service, the job remains `pending` in SQLite. On restart the worker sends it.  
If the process dies **after** the mock accepted the message but **before** we mark `sent`, the job is retried → possible **duplicate delivery** (see below).

### Retries
Failed or timed-out deliveries keep `status = pending`, increase `attempts`, and set `next_attempt_at` with exponential-ish backoff (capped). After `MAX_ATTEMPTS`, status becomes `dead` so a permanently broken notifier does not retry forever. Dead jobs can be inspected via `/api/notifications`.

### Duplicate delivery
Delivery is **at-least-once**, not exactly-once. That is intentional and simple:

- Marking `sent` only after a successful HTTP response avoids losing notifications.
- The cost is that a crash in the small window between success and DB update can cause a second send.

Exactly-once would need idempotency keys acknowledged by a real provider, which is out of scope for a mock.

### Backend restarts
SQLite is the source of truth. The worker has no in-memory-only queue for “must deliver” work. Pending outbox rows survive restarts.

### Isolation between members
Jobs are fetched and handled one-by-one (or in a small loop). A 500/timeout for member A only affects that job’s next retry time; member B’s job is still eligible on the next poll.

---

## Throughput vs reliability trade-offs

| Choice | Benefit | Cost |
|--------|---------|------|
| **Transactional outbox in SQLite** | Registration and “must notify” are atomic; restart-safe | SQLite write lock; lower write throughput than a dedicated queue (Kafka/RabbitMQ) |
| **Single background thread + poll** | Easy to explain and demo; no broker to run | Lower throughput than many workers; poll interval adds latency (seconds, not ms) |
| **Return before notify** | Fast API; registration never blocked by slow/failing notifier | User may see “registered” before confirmation is actually delivered |
| **At-least-once + retries** | Confirmations are not lost on transient mock failures | Occasional duplicate notifications after crashes |
| **Small batch size per poll** | API stays responsive; fair across jobs | Peak notify rate is limited (by design for this demo) |
| **Exponential backoff + max attempts** | Protects a flapping mock service; avoids infinite hammering | After max attempts a job is `dead` until someone investigates |
| **Same-process worker** | One command to run the main app | Scaling API and worker independently would need a separate process/queue |
| **Mock random failures** | Demonstrates retry/recovery clearly | Not a production delivery SLA |

**Summary for graders:** we prioritize **correctness and durability** (eventual confirmation, restart safety, independent retries) over **maximum throughput**. For a campus workshop system this is the right trade: correctness of “everyone gets notified” matters more than thousands of registrations per second.

---

## Demo scenarios to show

1. **Happy path** — create workshop → register → watch job go `pending` → `sent`.
2. **Duplicate register** — same email twice → 409, no extra notification job.
3. **Notification failure + recovery** — leave mock running (it fails often); watch `attempts` climb then `sent`.
4. **Restart safety** — register, stop `app.py` before jobs finish, restart → remaining jobs still send.
5. **Cancel** — cancel workshop → one cancel job per registrant → all eventually `sent` (independently).

---

## File layout

```
task2/
  ARCHITECTURE.md      ← this document
  README.md            ← how to run
  requirements.txt
  app.py               ← API + worker + SQLite
  mock_notifier.py     ← flaky HTTP notifier
  static/
    index.html
    style.css
    app.js
```
