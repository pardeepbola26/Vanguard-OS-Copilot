# Vanguard OS

A multi-agent strategic analysis copilot that breaks down ambiguous business
challenges into clear, structured insights. It identifies the core problem,
maps operational and market constraints, evaluates competitive pressures, and
generates a set of coherent strategic options rooted in first-principles
thinking.

Built around frameworks from Rumelt (*Good Strategy / Bad Strategy*), Enright
(5 Levels of Strategy), and Porter (Five Forces), Vanguard OS mirrors the
consulting workflow by moving from **Diagnosis → Guiding Policy → Actionable
Pathways**.

Whether analyzing a real client, a hypothetical scenario, or an internal
decision, Vanguard OS serves as a disciplined thinking partner that cuts
through noise and forces clarity.

---

## Architecture

**Pipeline of 14 specialized agents**, orchestrated through an async FastAPI
streaming endpoint. The frontend is a single-page HTML app that consumes an
NDJSON stream and progressively renders each agent's output.

```
Diagnostician  →  Frameworks + Structure (parallel)
                 →  Portfolio + Market Forces (parallel)
                 →  Financial  →  Ops  →  Tech + Human Factors (parallel)
                 →  Red Team  →  Synthesizer + Strategy Map (parallel)
```

Two agents (Diagnostician, Synthesizer) stream their output token-by-token
for perceived speed. Financial, Market Forces, and Frameworks produce
structured JSON that drives rich UI (scenario cards, intensity bars, Enright
ladder, framework cards).

### Key files

| File | What it does |
|---|---|
| `api_server.py` | FastAPI server. Streaming pipeline endpoint, document/CSV upload, history persistence, PPTX export |
| `vanguard_agents.py` | All 14 agent definitions + `run_vanguard_pipeline_stream` orchestrator |
| `database.py` | SQLite mission history (disabled by default — re-enable in `api_server.py`) |
| `vanguard.html` | Single-page UI shell — tab layout, preset hero, command palette |
| `static/vanguard.js` | Stream handler, rich renderers (Financials, Frameworks, Market Forces, Cytoscape) |
| `static/vanguard.css` | "Editorial Warm" theme — Fraunces serif + Inter + JetBrains Mono |

---

## Setup

**Prerequisites:** Python 3.9+ and an OpenAI API key.

```bash
# Clone
git clone <your-repo-url>
cd vanguard-os

# Create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate       # on Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure your API key
cp .env.example .env
#   then open .env and paste your real OPENAI_API_KEY
```

## Run

```bash
python3 api_server.py
```

Then open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) in a browser.

Stop with `Ctrl+C`.

### First mission

On the Synthesizer tab, click one of the three preset mission cards
(Telecom Turnaround, SaaS Expansion, Cost Takeout), then press **Run
Vanguard**. The pipeline takes ~90-120 seconds and streams each agent's
output into its tab as it completes.

### Keyboard shortcuts

- **⌘K / Ctrl+K** — Command palette (run, refine, jump to any tab, load presets, toggle theme)
- **Hover any heading** — Copy button appears on the right
- **Right-click a bullet in any output** — "Deep Dive" on that specific point

---

## Features

- **Streaming pipeline** with real-time agent progress
- **Structured financial model** — Base / Bull / Bear scenarios + unit economics
- **Auto-selected frameworks** — agent picks 2-3 that fit the problem
- **Porter forces** with 1-5 intensity scores and "most determinative" callout
- **Decision tree** (dagre top-down layout) with edge weights + ROI heat-coloring
- **Strategy map** with feedback-loop detection
- **Red Team** attacks the specific recommended option with severity scoring
- **Memo Mode** — full-page editorial view of the synthesizer output
- **Refine loop** — changes from the previous run are flash-highlighted
- **Mission history** — localStorage + optional SQLite for durability
- **Document upload** (PDF / DOCX) for grounding analyses in real company data
- **CSV upload** for auto-extracting financial metrics

---

## Configuration

Everything lives in `.env`:

| Variable | Required | What |
|---|---|---|
| `OPENAI_API_KEY` | yes | Your OpenAI API key |

Optional persistence: uncomment the `init_db()` call in `api_server.py`
lifespan to enable SQLite-backed mission history (already wired; just
toggle on).

---

## License

MIT or whatever you prefer — add a LICENSE file before sharing publicly.
