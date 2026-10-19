"""
Mock notification service.

Sometimes succeeds, sometimes returns an error, sometimes hangs
until the client times out. A 200 response means "delivered".
"""

import random
import time

from flask import Flask, jsonify, request

app = Flask(__name__)

# Tunable for demos: raise FAIL_RATE to see more retries.
SUCCESS_RATE = 0.70
ERROR_RATE = 0.20
# Remaining ~0.10 become timeouts.


@app.post("/notify")
def notify():
    payload = request.get_json(silent=True) or {}
    print(f"[mock] received: {payload}", flush=True)

    roll = random.random()

    if roll < SUCCESS_RATE:
        print("[mock] -> 200 OK", flush=True)
        return jsonify({"status": "delivered", "payload": payload}), 200

    if roll < SUCCESS_RATE + ERROR_RATE:
        print("[mock] -> 503 Service Unavailable", flush=True)
        return jsonify({"status": "error", "message": "temporary failure"}), 503

    # Simulate hanging so the client request times out.
    print("[mock] -> sleeping to force client timeout", flush=True)
    time.sleep(8)
    return jsonify({"status": "late"}), 200


@app.get("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    # threaded=True so a sleeping timeout request does not block other calls.
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
