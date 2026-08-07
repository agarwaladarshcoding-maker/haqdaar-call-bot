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
