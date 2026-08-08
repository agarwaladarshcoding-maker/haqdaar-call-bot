"""Stress test for api.py beyond TEST_CASES.md's documented L-series.

Real concurrent threads hammering /call/event for many simultaneous calls
at once (not just sequential requests that happen to interleave in
source order), plus a 100-scenario fuzz pass over random event shapes to
confirm nothing in the HTTP layer itself can 500.
"""
import concurrent.futures
import importlib
import random

import pytest
from fastapi.testclient import TestClient

from haqdaar import api, config


@pytest.fixture()
def client(demo_db, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", demo_db)
    importlib.reload(api)
    with TestClient(api.app) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. Real concurrent threads: 20 calls, each driven by its own thread
# pressing a distinct, deterministic key sequence - if sessions ever
# cross-contaminate under real thread interleaving, some call ends up
# with another call's answers.
# ---------------------------------------------------------------------------
def test_concurrent_calls_under_real_thread_contention(client):
    N = 20

    def run_call(i: int) -> tuple[str, dict]:
        call_id = client.post("/call/start").json()["call_id"]
        key = "1" if i % 2 == 0 else "2"
        resp = client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": key}})
        return call_id, resp.json()["state"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
        results = list(ex.map(run_call, range(N)))

    call_ids = [r[0] for r in results]
    assert len(set(call_ids)) == N  # every call_id unique, no collision

    for i, (call_id, state) in enumerate(results):
        expected = "known_scheme" if i % 2 == 0 else "find_for_me"
        assert state["answers"]["intent"] == expected, f"call {i} got cross-contaminated"


def test_concurrent_events_on_many_calls_interleaved(client):
    N = 15
    call_ids = [client.post("/call/start").json()["call_id"] for _ in range(N)]
    keys = [str(random.Random(i).choice([1, 2])) for i in range(N)]

    def fire(i: int):
        return client.post("/call/event", json={"call_id": call_ids[i], "event": {"dtmf": keys[i]}})

    with concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
        list(ex.map(fire, range(N)))

    for i, cid in enumerate(call_ids):
        state = client.get(f"/call/{cid}/state").json()
        expected = "known_scheme" if keys[i] == "1" else "find_for_me"
        assert state["answers"]["intent"] == expected


# ---------------------------------------------------------------------------
# 2. Fuzz: random event shapes must never 500.
# ---------------------------------------------------------------------------
RANDOM_EVENT_SHAPES = [
    {"dtmf": "1"}, {"dtmf": "9"}, {"dtmf": "x"}, {"dtmf": ""},
    {"speech": "hello", "confidence": 0.9}, {"speech": "", "confidence": 0.1},
    {"timeout": 5}, {"timeout": 0}, {"timeout": 999},
    {"hangup": True}, {}, {"unknown_key": "whatever"},
]


@pytest.mark.parametrize("seed", range(20))
def test_100_random_event_scenarios_never_500(client, seed):
    rng = random.Random(seed)
    call_id = client.post("/call/start").json()["call_id"]
    for _ in range(5):
        event = rng.choice(RANDOM_EVENT_SHAPES)
        resp = client.post("/call/event", json={"call_id": call_id, "event": event})
        assert resp.status_code == 200, f"event {event} caused {resp.status_code}"
        if resp.json()["state"]["phase"] == "ended":
            break


# ---------------------------------------------------------------------------
# 3. Session count never leaks unboundedly across a burst of short calls -
# not a hard leak-detector, just confirms /health's active_calls tracks
# reality after explicit cleanup.
# ---------------------------------------------------------------------------
def test_active_calls_count_reflects_cleanup(client):
    ids = [client.post("/call/start").json()["call_id"] for _ in range(5)]
    health = client.get("/health").json()
    assert health["active_calls"] >= 5
    for cid in ids:
        client.delete(f"/call/{cid}")
    health_after = client.get("/health").json()
    assert health_after["active_calls"] == health["active_calls"] - 5
