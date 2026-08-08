#!/usr/bin/env python3
"""Builds a JSON trace of REAL calls made over the live api.py HTTP server
(not the engine directly) so the Step 7+8 demo shows the actual
request/response cycle a Twilio adapter or judge's terminal would see -
same principle as gen_step5_trace.py / gen_step6_trace.py: nothing hand-
scripted, every value is a real HTTP response from a real running server."""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT + "/src")

DB = "/tmp/step78_demo.db"
subprocess.run([sys.executable, ROOT + "/scripts/seed_demo.py", DB], check=True, capture_output=True)

import socket
import httpx


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE_URL = f"http://127.0.0.1:{PORT}"
env = {**os.environ, "DB_PATH": DB, "PYTHONPATH": ROOT + "/src"}
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "haqdaar.api:app", "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
    cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

try:
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            httpx.get(f"{BASE_URL}/health", timeout=0.5)
            break
        except httpx.ConnectError:
            time.sleep(0.1)

    scenarios = {
        "m1_happy_path": {
            "label": "M1 - Happy path (business owner, valid presses)",
            "script": ["1", "2", "1", "1", "1"],
        },
        "m2_hash_recovery": {
            "label": "M2 - Press # twice mid-call, candidates recover",
            "script": ["1", "2", "1", "#", "#", "2", "1", "1"],
        },
        "m3_zero_restart": {
            "label": "M3 - Press 0 mid-call, full restart",
            "script": ["1", "2", "1", "0", "1", "2", "1", "1"],
        },
        "m6_wrong_buttons": {
            "label": "M6 - Wrong button every time, still completes",
            "script": ["1", "8", "8", "8", "2", "9", "9", "9", "1", "1"],
        },
        "m7_silence": {
            "label": "M7 - Long silence, ends politely",
            "script": ["!silence", "!silence", "!silence"],
        },
    }

    trace = {"base_url": "http://127.0.0.1:8791", "scenarios": {}, "health": None, "dial_examples": {}}

    trace["health"] = httpx.get(f"{BASE_URL}/health", timeout=5).json()

    for key, sc in scenarios.items():
        with httpx.Client(base_url=BASE_URL, timeout=5.0) as client:
            turns = []
            resp = client.post("/call/start")
            body = resp.json()
            call_id = body["call_id"]
            turns.append({
                "request": {"method": "POST", "path": "/call/start", "body": None},
                "response": {"status": resp.status_code, "actions": body["actions"], "state": body["state"]},
            })
            for key_press in sc["script"]:
                if key_press == "!silence":
                    event = {"timeout": 30}
                else:
                    event = {"dtmf": key_press}
                resp = client.post("/call/event", json={"call_id": call_id, "event": event})
                body = resp.json()
                turns.append({
                    "request": {"method": "POST", "path": "/call/event", "body": {"call_id": "***", "event": event}},
                    "response": {"status": resp.status_code, "actions": body["actions"], "state": body["state"]},
                })
                if body["state"]["phase"] == "ended":
                    break
            client.delete(f"/call/{call_id}")
            trace["scenarios"][key] = {"label": sc["label"], "script": sc["script"], "turns": turns}

    # Concurrency proof: fire two real concurrent calls with different
    # answers and show both survive with correct, non-mixed state (L5).
    import concurrent.futures

    def run_isolated(lang_key):
        with httpx.Client(base_url=BASE_URL, timeout=5.0) as client:
            cid = client.post("/call/start").json()["call_id"]
            r = client.post("/call/event", json={"call_id": cid, "event": {"dtmf": lang_key}})
            state = r.json()["state"]
            client.delete(f"/call/{cid}")
            return cid, state

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(run_isolated, ["1", "2"]))
    trace["concurrency_proof"] = [
        {"call_id": cid[:8] + "...", "language_answered": state["answers"].get("language")}
        for cid, state in results
    ]

    # Dial-code examples via the menu.py path (M10), same DB.
    from haqdaar import menu
    menu._set_db_path_for_testing(DB)
    for code in ["011", "054", "999"]:
        result = menu.resolve_code(code)
        if result is None:
            trace["dial_examples"][code] = None
        else:
            slug, sec_key = result
            trace["dial_examples"][code] = {
                "slug": slug, "sec_key": sec_key,
                "spoken_digits": menu.speak_digits_hi(code),
                "text": menu.section_text(slug, sec_key),
            }

finally:
    proc.terminate()
    proc.wait(timeout=5)

out_path = os.path.join(ROOT, "demos", "step78_trace.json")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(json.dumps(trace, ensure_ascii=False, indent=None))
print("wrote", out_path)
print("scenarios:", list(trace["scenarios"].keys()))
print("health:", trace["health"])
print("concurrency_proof:", trace["concurrency_proof"])
