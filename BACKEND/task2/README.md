# Workshop Registration and Notifications

Flask + SQLite backend with a flaky mock HTTP notifier and a barebones vanilla JS UI.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for workflows, reliability design, and throughput trade-offs.

## Setup

```bash
cd /home/mushaam/Desktop/task2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run (two terminals)

**Terminal 1 — mock notifier (port 5001):**

```bash
source .venv/bin/activate
python mock_notifier.py
```

**Terminal 2 — registration API + worker (port 5000):**

```bash
source .venv/bin/activate
python app.py
```

Open http://127.0.0.1:5000/

## Demo checklist

1. Create a workshop, register a member → registration returns immediately.
2. Watch **Notification jobs**: `pending` → `sent` (retries if mock fails/times out).
3. Register the same email again → error, no new confirmation job.
4. Stop `app.py` while jobs are still `pending`, start it again → jobs resume and send.
5. Cancel a workshop with registrants → cancel jobs appear and deliver independently.

## Optional

- Reset data: delete `workshop.db` and restart `app.py`.
- Point at another notifier: `NOTIFIER_URL=http://127.0.0.1:5001/notify python app.py`
