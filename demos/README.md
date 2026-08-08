# Visual demos

Self-contained HTML pages for seeing the system work without reading code
or running pytest. Each one embeds a pre-computed trace of real engine
output (not hand-written/simulated data) so what you click through is
what the actual code returns.

- **`step5_system_flow.html`** — open directly in a browser (double-click,
  no server needed). Split view: which files are active on each turn
  (left), the live call transcript (middle), phone keypad + global
  controls `0`/`*`/`#` (right). Driven entirely by
  `haqdaar.engine.step()`.
- **`gen_step5_trace.py`** — regenerates `step5_trace.json`, the data
  embedded in `step5_system_flow.html`. Run `.venv/bin/python
  demos/gen_step5_trace.py` after any change to `engine.py`, `bank.py`,
  `narrow.py`, `select.py`, or `question_bank.yaml` that could change
  reachable call states, then paste the new `step5_trace.json` contents
  over the `const TRACE = ...` line in the HTML file.
- **`step5_trace.json`** — the generated data itself, kept alongside the
  script for diffing when the trace changes between commits.
- **`step6_menu_demo.html`** — open directly in a browser. Two tabs: "Browse
  the menu" (walk the number tree: theme → need group where applicable →
  scheme list → section text, with breadcrumb navigation) and "Dial a code"
  (type a 3-digit code or click a sample chip to jump straight to one
  section — try `054`, an unverified scheme, vs `011`, a verified one, to
  see the deadline-sentence stripping live). Driven entirely by
  `haqdaar.menu` functions.
- **`gen_step6_trace.py`** — regenerates `step6_menu_trace.json`. Run
  `.venv/bin/python demos/gen_step6_trace.py` after any change to
  `menu.py` or the seed data, then paste the new JSON over the `const
  DATA = ...` line in `step6_menu_demo.html`.
- **`step6_menu_trace.json`** — the generated data itself, kept alongside
  the script for diffing.
- **`step78_api_sim_demo.html`** — open directly in a browser. Four tabs:
  "Call scenarios" (M1/M2/M3/M6/M7 as real HTTP request/response ladders —
  method, path, status, the exact `say`/`gather` actions, and a live state
  strip with a candidate-count bar), "Concurrency proof" (two real calls
  fired on separate threads at once, showing neither call's answers leaked
  into the other — L5), "Direct dial" (the M10 dial-code path via
  `menu.py`, same as `step6_menu_demo.html`'s dial tab but reached without
  going through any questions), and "Health + run it yourself" (the real
  `GET /health` response plus the exact `sim.py` command to run locally
  with no API keys). Driven entirely by real requests against a live
  `uvicorn` server running `haqdaar.api:app`.
- **`gen_step7_8_trace.py`** — regenerates `step78_trace.json`. Run
  `.venv/bin/python demos/gen_step7_8_trace.py` after any change to
  `api.py`, `engine.py`, or `sim.py` that could change what a call looks
  like over HTTP, then paste the new JSON over the `const DATA = ...` line
  in `step78_api_sim_demo.html`.
- **`step78_trace.json`** — the generated data itself, kept alongside the
  script for diffing.
