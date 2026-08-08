# Haqdaar Voice — how the files connect

This is the map to come back to whenever something breaks and it's not
obvious which file is responsible. It reflects the code as it actually
exists right now (Steps 0-8 done), not the original build plan.

For click-through demos of the system actually running, see `demos/` —
`step5_system_flow.html` (the state machine), `step6_menu_demo.html`
(browse + dial codes), and `step78_api_sim_demo.html` (real HTTP calls
against a live server, the M-series scenarios, and the concurrency proof).

## The mental model in one paragraph

A caller's answers accumulate in a plain dict (`answers`). Every turn, two
independent systems look at that dict: `narrow.py` asks the database "given
these answers, which schemes still qualify?" and `select.py` asks the
question bank "given these answers, what's the single best next question?"
Neither ever talks to the phone system directly — that's `engine.py`'s job,
which wraps both of them in a pure `step(state, event) -> (new_state,
actions)` function that `api.py` exposes over HTTP, and that either
`sim.py` (a terminal fake, done) or a real Twilio call (Step 11, later)
can drive identically.

## Diagram

```mermaid
flowchart TB
    subgraph data["Data — built once, read many times"]
        YAML["question_bank.yaml<br/>(47 questions, DAG)"]
        SQLITE[("haqdaar.db<br/>schemes + scheme_rules<br/>+ attributes/attr_values")]
        INGEST["scripts/ingest.py<br/>real 100-scheme loader"]
        SEED["scripts/seed_demo.py<br/>20 synthetic test schemes"]
        INGEST -->|writes| SQLITE
        SEED -->|writes, reuses ingest.SCHEMA| SQLITE
    end

    subgraph core["Core engine — pure Python, no I/O side effects, DONE"]
        BANK["bank.py<br/>load_bank() → Bank<br/>askable(answers)"]
        NARROW["narrow.py<br/>narrow(answers) → ranked schemes<br/>THE safety-critical file"]
        SELECT["select.py<br/>pick_question(answers, bank)<br/>→ next Question"]
        YAML -->|parsed by| BANK
        SQLITE -->|read by| NARROW
        BANK -->|askable list| SELECT
        NARROW -->|worst-case simulation| SELECT
    end

    subgraph heart["engine.py — Step 5, DONE"]
        ENGINE["step(state, event)<br/>→ (new_state, actions)<br/>THE HEART — pure state machine"]
        SELECT -->|calls| ENGINE
        NARROW -->|calls, on every answer + on # undo| ENGINE
    end

    subgraph menu_layer["menu.py — Step 6, DONE"]
        MENU["menu.py<br/>number-tree navigation<br/>direct-dial scheme_no lookup"]
        SQLITE -->|reads scheme text| MENU
    end

    subgraph surface["api.py — Step 7, DONE"]
        API["api.py (FastAPI)<br/>POST /call/start<br/>POST /call/event<br/>GET /call/id/state<br/>GET /health"]
        ENGINE -->|wrapped by| API
    end

    subgraph clients["Two interchangeable clients of the same API"]
        SIM["sim.py — Step 8, DONE<br/>terminal keypress simulator<br/>+ direct-dial via menu.py"]
        TWILIO["Twilio webhook — Step 11, FUTURE<br/>real phone calls"]
        API --> SIM
        SIM -.->|--dial CODE, bypasses questions| MENU
        API -.-> TWILIO
    end

    subgraph optional["Optional external services — OFF by default"]
        LLM["LLM (question tie-break)<br/>only if LLM_API_KEY set"]
        SARVAM["Sarvam (speech-to-text /<br/>text-to-speech, Hindi)<br/>only if SARVAM_API_KEY set"]
        SELECT -.->|700ms budget, falls back silently| LLM
        TWILIO -.->|voice audio only, future| SARVAM
    end

    style core fill:#e7ecde,stroke:#5c7048
    style heart fill:#e7ecde,stroke:#5c7048
    style menu_layer fill:#e7ecde,stroke:#5c7048
    style surface fill:#e7ecde,stroke:#5c7048
    style clients fill:#e7ecde,stroke:#5c7048
    style optional fill:#f3e2dc,stroke:#9b4a3a
```

## File-by-file, in the order data flows

| File | Status | What it actually does |
|---|---|---|
| `question_bank.yaml` | done | The 47 questions a caller can be asked, and the rules for which question can follow which answer. Pure data, no code. |
| `attribute_seed.sql` | done | Dictionary of every attribute a question can write (`persona`, `age_band`, ...) and, for banded attributes, their sort order (`ord`) so "age 18-40 or older" comparisons work correctly. |
| `scripts/ingest.py` | done | Loads the real 100-scheme catalogue into `haqdaar.db`. Also defines the live database schema (the actual source of truth — the standalone `.sql` files in the repo root are superseded references, not what's loaded). |
| `scripts/seed_demo.py` | done | Builds a 20-scheme *synthetic* database with realistic eligibility rules, used only by the test suite and the demo artifact — the real catalogue's `scheme_rules` table is still empty until Step 9 (human eligibility-text authoring). |
| `src/haqdaar/db.py` | done | One function: open a SQLite connection with sane defaults. Nothing else. |
| `src/haqdaar/bank.py` | done | Loads and validates `question_bank.yaml`. Answers one question: "given what's been answered so far, which questions are still askable?" |
| `src/haqdaar/narrow.py` | done | **The most safety-critical file in the project.** Given a dict of answers, returns every scheme that hasn't been *disproven* — an unanswered attribute never removes a scheme. Getting this backwards would silently hide schemes from callers who haven't been asked enough questions yet. |
| `src/haqdaar/select.py` | done | Given the askable questions and the current candidates, picks the single question that will narrow the list the most no matter which button the caller presses. Can optionally ask an LLM to break ties among the top 5 — but the LLM only ever sees question IDs and a one-line reason, never scheme names, and any bad/slow/missing response falls back to the deterministic top pick automatically. |
| `src/haqdaar/engine.py` | done | Owns the actual call state (what's been asked, what's been answered, how many candidates remain) and decides what happens on every keypress, including the global controls (`0`=restart, `*`=repeat, `#`=undo one answer, always re-running the narrowing so the candidate count is never stale) and the fallback ladders for silence, wrong buttons, and unclear speech. This is the file everything else routes through. |
| `src/haqdaar/menu.py` | done | Handles the "I already know the scheme's number" path — direct dial by 3-digit code (2-digit scheme number + 1-digit section), and the browse-by-category menu tree. Deliberately decoupled from `engine.py`'s question flow: a dial code jumps straight to a scheme's section text without going through any questions, so reshuffling the menu can never move a code someone wrote down (H11). |
| `src/haqdaar/api.py` | done | Exposes `engine.py` over plain HTTP endpoints (`/call/start`, `/call/event`, `/call/{id}/state`, `/health`), so any client (a terminal, a real phone system) can drive a call the same way. In-memory sessions only — caller answers are never written to disk. |
| `src/haqdaar/sim.py` | done | A terminal program that plays the role of a phone: prints what the system would say, accepts keypresses, and shows the call happening, talking to `api.py` over real HTTP exactly as a Twilio adapter eventually will. `--trace` shows the candidate count dropping turn by turn; `--dial CODE` demonstrates the direct-dial path. This is the actual runnable demo — see `demos/step78_api_sim_demo.html` for a recorded walkthrough, or run it live per the command in that file's "Health" tab. |
| `src/haqdaar/voice.py` | Later, not scoped yet | Will eventually turn the system's text prompts into speech and callers' speech into text, via Sarvam. |

## Answering "when do the external services turn on?"

Nothing external is required for the system to work right now, and a full call completes with `.env` absent entirely (Step 8's L1 test). See the one-liner roadmap below for exactly where each optional piece plugs in.
