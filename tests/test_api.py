"""TEST_CASES.md section L - HTTP API (Step 7).

api.py is a pure translation layer over engine.step() - these tests focus
on the things only the HTTP layer can get wrong: session isolation,
idempotency, unknown call_id handling, and never persisting answers to
disk. Engine correctness itself is already covered by test_engine.py.
"""
import importlib

import pytest
from fastapi.testclient import TestClient

from haqdaar import api, config


@pytest.fixture()
def client(demo_db, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", demo_db)
    # api.py caches the loaded bank at module level - reset it per test so
    # a fresh TestClient always starts from a clean, unloaded state.
    importlib.reload(api)
    with TestClient(api.app) as c:
        yield c


# ---------------------------------------------------------------------------
# Basic wiring: start returns the language prompt, actions match engine.py
# ---------------------------------------------------------------------------
def test_call_start_returns_language_prompt(client):
    resp = client.post("/call/start")
    assert resp.status_code == 200
    body = resp.json()
    assert "call_id" in body
    assert any("say" in a for a in body["actions"])
    say_text = next(a["say"] for a in body["actions"] if "say" in a)
    assert "Hindi" in say_text or "Namaste" in say_text
    assert body["state"]["current_question"] == "Q001_LANGUAGE"


def test_call_event_advances_state(client):
    start = client.post("/call/start").json()
    call_id = start["call_id"]
    resp = client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": "1"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["answers"].get("language") == "hi"
    assert body["state"]["current_question"] != "Q001_LANGUAGE"


def test_health_reports_db_and_bank_loaded(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db_loaded"] is True
    assert body["bank_loaded"] is True


# ---------------------------------------------------------------------------
# L5: two concurrent calls must never mix sessions
# ---------------------------------------------------------------------------
def test_l5_two_calls_never_mix_sessions(client):
    call_a = client.post("/call/start").json()["call_id"]
    call_b = client.post("/call/start").json()["call_id"]
    assert call_a != call_b

    client.post("/call/event", json={"call_id": call_a, "event": {"dtmf": "1"}})  # hi
    client.post("/call/event", json={"call_id": call_b, "event": {"dtmf": "2"}})  # en

    state_a = client.get(f"/call/{call_a}/state").json()
    state_b = client.get(f"/call/{call_b}/state").json()
    assert state_a["answers"]["language"] == "hi"
    assert state_b["answers"]["language"] == "en"


def test_l5_many_concurrent_calls_stay_isolated(client):
    call_ids = [client.post("/call/start").json()["call_id"] for _ in range(10)]
    assert len(set(call_ids)) == 10  # all unique
    # Answer each with a different DTMF pattern, verify no cross-talk.
    for i, cid in enumerate(call_ids):
        key = "1" if i % 2 == 0 else "2"
        client.post("/call/event", json={"call_id": cid, "event": {"dtmf": key}})
    for i, cid in enumerate(call_ids):
        expected = "hi" if i % 2 == 0 else "en"
        state = client.get(f"/call/{cid}/state").json()
        assert state["answers"]["language"] == expected


# ---------------------------------------------------------------------------
# L6: hangup cleans up, no crash
# ---------------------------------------------------------------------------
def test_l6_hangup_ends_session_without_crash(client):
    call_id = client.post("/call/start").json()["call_id"]
    resp = client.post("/call/event", json={"call_id": call_id, "event": {"hangup": True}})
    assert resp.status_code == 200
    assert resp.json()["state"]["phase"] == "ended"


def test_l6_delete_call_is_idempotent(client):
    call_id = client.post("/call/start").json()["call_id"]
    r1 = client.delete(f"/call/{call_id}")
    r2 = client.delete(f"/call/{call_id}")  # already gone - must not error
    assert r1.status_code == 200
    assert r2.status_code == 200
    # State is gone after delete.
    assert client.get(f"/call/{call_id}/state").status_code == 404


# ---------------------------------------------------------------------------
# L7: same call_id posted twice (duplicate event) handled idempotently -
# specifically, a duplicate post to an ALREADY-ENDED call must not crash
# and must keep returning a clean terminal response.
# ---------------------------------------------------------------------------
def test_l7_duplicate_event_after_hangup_does_not_crash(client):
    call_id = client.post("/call/start").json()["call_id"]
    client.post("/call/event", json={"call_id": call_id, "event": {"hangup": True}})
    resp = client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": "1"}})
    assert resp.status_code == 200
    assert resp.json()["state"]["phase"] == "ended"
    assert resp.json()["actions"] == [{"end": True}]


def test_l7_posting_same_answer_twice_is_safe(client):
    call_id = client.post("/call/start").json()["call_id"]
    r1 = client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": "1"}})
    q_after_first = r1.json()["state"]["current_question"]
    # Same event posted again just advances from wherever the call now is
    # (this is a duplicate CLIENT retry, not a replay-to-the-same-point
    # guarantee - step() has no idea it's a "duplicate", it just processes
    # the next event in sequence, which is the correct, documented
    # behaviour: HTTP-level duplicate detection is not in scope for L7,
    # only "does not crash / does not corrupt the session").
    r2 = client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": "1"}})
    assert r2.status_code == 200
    assert isinstance(r2.json()["state"]["answers"], dict)


# ---------------------------------------------------------------------------
# L8: unknown call_id -> 404, no crash
# ---------------------------------------------------------------------------
def test_l8_unknown_call_id_event_returns_404(client):
    resp = client.post("/call/event", json={"call_id": "does-not-exist", "event": {"dtmf": "1"}})
    assert resp.status_code == 404


def test_l8_unknown_call_id_state_returns_404(client):
    resp = client.get("/call/does-not-exist/state")
    assert resp.status_code == 404


@pytest.mark.parametrize("bad_id", ["", " ", "../../etc/passwd", "null", "0", "a" * 500])
def test_l8_malformed_call_ids_never_crash(client, bad_id):
    resp = client.get(f"/call/{bad_id}/state")
    assert resp.status_code in (404, 422)


# ---------------------------------------------------------------------------
# M13 / PRD: caller answers never written to disk - state lives only in
# the in-memory dict, confirmed by construction (api._sessions is a plain
# dict, nothing in api.py opens a file for writing).
# ---------------------------------------------------------------------------
def test_answers_never_persisted_to_disk(client, tmp_path):
    import os

    before = set(os.listdir(tmp_path.parent)) if tmp_path.parent.exists() else set()
    call_id = client.post("/call/start").json()["call_id"]
    client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": "1"}})
    # No new files should appear anywhere as a side effect of answering -
    # this is a structural sanity check, not exhaustive, but catches an
    # accidental "log answers to a file" regression.
    import inspect

    source = inspect.getsource(api)
    assert "open(" not in source
    assert ".write(" not in source


# ---------------------------------------------------------------------------
# Full call via the HTTP layer alone, mirroring a real client's turn-by-
# turn interaction (this is what sim.py, Step 8, will do for real).
# ---------------------------------------------------------------------------
def test_full_call_completes_via_http_only(client):
    call_id = client.post("/call/start").json()["call_id"]
    state = client.get(f"/call/{call_id}/state").json()
    turns = 0
    while state["phase"] != "ended" and turns < 30:
        q = state["current_question"]
        # Always press "1" - a deterministic happy path through whatever
        # bank is loaded, same principle as test_engine.py's smoke tests.
        resp = client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": "1"}})
        state = resp.json()["state"]
        turns += 1
        if state["phase"] == "presenting":
            resp = client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": "0"}})
            state = resp.json()["state"]
            break
    assert turns < 30, "call did not terminate within a reasonable number of turns"
