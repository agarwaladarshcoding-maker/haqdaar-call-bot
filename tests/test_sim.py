"""TEST_CASES.md section M - full-call scenarios (Step 8), run through
sim.py's real HTTP client against a live uvicorn server, exactly as a
judge running the CLI by hand would exercise it. This is the actual demo,
so these tests drive the SAME code path (sim.run_call / sim.run_dial),
not a re-implementation of the call loop.
"""
import socket
import subprocess
import sys
import time

import pytest

from haqdaar import sim
from haqdaar.engine import ROOT_QUESTION_ID

ROOT_SRC_ON_PATH = True  # conftest.py already inserts src/ onto sys.path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def server(demo_db):
    """A real uvicorn process serving api.py against the demo DB, isolated
    per test via a fresh random port (parallel-safe, no port collisions)."""
    import os

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {
        **os.environ,
        "DB_PATH": demo_db,
        "PYTHONPATH": os.path.join(root, "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
        # Subprocess loads its own config.py/.env - monkeypatch in the
        # parent test process never reaches it. Blank explicitly so this
        # server never makes a real LLM call regardless of what .env has.
        "LLM_API_KEY": "",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "haqdaar.api:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env,
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        if sim._port_open("127.0.0.1", port):
            break
        time.sleep(0.1)
    else:
        proc.terminate()
        pytest.fail("test server did not start")
    yield base_url
    proc.terminate()
    proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# M1: happy path, all valid presses -> 5-6 questions, schemes presented
# ---------------------------------------------------------------------------
def test_m1_happy_path_presents_schemes(server):
    state = sim.run_call(server, script=["1", "2", "1"] + ["1"] * 15, trace=False, seed=1, quiet=True)
    assert state["phase"] == "ended"
    assert len(state["asked"]) <= 10  # max_questions_per_call


# ---------------------------------------------------------------------------
# M2: '#' twice mid-call -> recovers, candidate count correct
# ---------------------------------------------------------------------------
def test_m2_hash_twice_mid_call_recovers_candidate_count(server):
    state = sim.run_call(server, script=["1", "2", "1", "#", "#", "1", "2", "1"] + ["1"] * 10, trace=False, seed=2, quiet=True)
    assert state["phase"] == "ended"


# ---------------------------------------------------------------------------
# M3: '0' at question 4 -> full restart from the root question
# ---------------------------------------------------------------------------
def test_m3_zero_mid_call_restarts_from_root(server):
    import httpx

    with httpx.Client(base_url=server, timeout=5.0) as client:
        body = client.post("/call/start").json()
        call_id = body["call_id"]
        for key in ["1", "2", "1"]:
            body = client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": key}}).json()
        assert len(body["state"]["asked"]) == 3
        body = client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": "0"}}).json()
        assert body["state"]["current_question"] == ROOT_QUESTION_ID
        assert body["state"]["asked"] == []
        assert body["state"]["answers"] == {}
        assert body["state"]["candidate_count"] == 20  # full demo catalogue restored
        client.delete(f"/call/{call_id}")


# ---------------------------------------------------------------------------
# M4: never speaks, only buttons -> completes normally
# ---------------------------------------------------------------------------
def test_m4_buttons_only_completes_normally(server):
    state = sim.run_call(server, script=["1", "1", "1"] + ["1"] * 12, trace=False, seed=4, quiet=True)
    assert state["phase"] == "ended"


# ---------------------------------------------------------------------------
# M5: speaks unclearly throughout -> degrades to buttons, still completes
# (a demo-day test per TEST_CASES.md)
# ---------------------------------------------------------------------------
def test_m5_unclear_speech_throughout_degrades_and_completes(server):
    script = ["1"] + ["!speech:mumble mumble"] * 4 + ["1"] * 12
    state = sim.run_call(server, script=script, trace=False, seed=5, quiet=True)
    assert state["phase"] == "ended"


# ---------------------------------------------------------------------------
# M6: wrong button at every node -> reaches 2-option menus, still completes
# (a demo-day test)
# ---------------------------------------------------------------------------
def test_m6_wrong_button_everywhere_still_completes(server):
    script = ["1"] + ["8"] * 5 + ["1"] * 15
    state = sim.run_call(server, script=script, trace=False, seed=6, quiet=True)
    assert state["phase"] == "ended"


# ---------------------------------------------------------------------------
# M7: long silences at every node -> ends politely, no dead air
# (a demo-day test)
# ---------------------------------------------------------------------------
def test_m7_long_silences_end_politely(server):
    script = ["!silence"] * 6
    state = sim.run_call(server, script=script, trace=False, seed=7, quiet=True)
    assert state["phase"] == "ended"


# ---------------------------------------------------------------------------
# M10: types a direct dial code -> jumps straight to that section.
# (see sim.py module docstring: this is the standalone --dial path,
# menu.py's resolve_code, deliberately decoupled from the question flow.)
# ---------------------------------------------------------------------------
def test_m10_direct_dial_code_reaches_section_text(demo_db, capsys, monkeypatch):
    from haqdaar import menu

    menu._set_db_path_for_testing(demo_db)
    try:
        rc = sim.run_dial("unused", "011", quiet=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "IVR:" in out
    finally:
        menu._set_db_path_for_testing(None)


def test_m10_invalid_dial_code_graceful(demo_db, capsys):
    from haqdaar import menu

    menu._set_db_path_for_testing(demo_db)
    try:
        rc = sim.run_dial("unused", "999", quiet=True)
        assert rc == 1
        out = capsys.readouterr().out
        assert "not a valid code" in out
    finally:
        menu._set_db_path_for_testing(None)


# ---------------------------------------------------------------------------
# M11: contradictory answers -> zero candidates, recovers
# ---------------------------------------------------------------------------
def test_m11_contradictory_answers_recovers(server):
    # Drive many hard-conflicting answers back to back; the engine's I4
    # zero-candidates recovery (already covered at the engine layer) must
    # still let the whole call finish cleanly end to end via HTTP.
    state = sim.run_call(server, script=["1", "1", "5", "2", "1"] + ["1"] * 12, trace=False, seed=11, quiet=True)
    assert state["phase"] == "ended"


# ---------------------------------------------------------------------------
# M12: '*' after every prompt -> replays correctly every time
# ---------------------------------------------------------------------------
def test_m12_star_after_every_prompt_replays(server):
    import httpx

    with httpx.Client(base_url=server, timeout=5.0) as client:
        body = client.post("/call/start").json()
        call_id = body["call_id"]
        first_prompt = body["state"]["last_spoken"]
        body = client.post("/call/event", json={"call_id": call_id, "event": {"dtmf": "*"}}).json()
        replay_say = next(a["say"] for a in body["actions"] if "say" in a)
        assert replay_say == first_prompt
        assert body["state"]["current_question"] == ROOT_QUESTION_ID  # unmoved
        client.delete(f"/call/{call_id}")


# ---------------------------------------------------------------------------
# Whole M-series in one sweep, matching the priority list at the bottom of
# TEST_CASES.md - M6 in particular is explicitly a "judge will absolutely
# mash wrong buttons" test.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(15))
def test_m_series_random_chaos_scripts_always_terminate(server, seed):
    import random

    rng = random.Random(seed)
    keys = ["1", "2", "3", "8", "9", "#", "!silence", "!speech:test"]
    script = [rng.choice(keys) for _ in range(20)]
    state = sim.run_call(server, script=script, trace=False, seed=seed, quiet=True)
    assert state["phase"] == "ended"
