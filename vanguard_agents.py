# ============================================================
# Vanguard OS v2 - Multi-Agent Strategy Engine
# Premium Consulting Edition (Async/Parallel)
# ============================================================

import json
import os
import asyncio
from typing import Dict, Any
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Live search disabled by user request
SEARCH_AVAILABLE = False

# Load API key from .env
load_dotenv()
aclient = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ------------------------------------------------------------
# Helper
# ------------------------------------------------------------
def normalize_markdown(text: str) -> str:
    return text.replace("###", "##").replace("######", "###").strip()


async def _stream_chat(system: str, user: str, *, model: str = "gpt-4o",
                       max_tokens: int = 800, temperature: float = 0.5,
                       timeout: float = 45.0):
    """
    Async generator yielding text deltas from an OpenAI chat completion in streaming mode.
    Callers accumulate deltas themselves; this helper does NOT yield the final full text.
    On error, yields a single "[stream error: ...]" token so downstream UI doesn't hang.
    """
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            ),
            timeout=timeout,
        )
        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta
    except Exception as e:
        print(f"_stream_chat error: {e}")
        yield f"\n\n[stream error: {e}]"

# ------------------------------------------------------------
# AGENT 1: DIAGNOSTICIAN AGENT
# ------------------------------------------------------------
DIAGNOSTICIAN_SYSTEM = "You are the Diagnostician Agent. Identify the CRUX."


def _diagnostician_prompt(situation: str, goal: str, constraints: str, document_context=None) -> str:
    prompt = f"""
You are the Diagnostician Agent for Vanguard OS, applying Rumelt's Good Strategy / Bad Strategy kernel.

Your job is to identify the ONE critical obstacle blocking the goal. Not symptoms. Not wishes. The crux.

Situation: {situation}
Goal: {goal}
Constraints: {constraints}
"""
    if document_context:
        prompt += f"\nCONTEXT:\n{document_context[:5000]}\n"

    prompt += """
Rules:
- Rumelt distinction: a CRUX is the obstacle that, if removed, makes the goal achievable. Not "what is bad."
- Reject vague or multi-headed diagnoses. One crux. One sentence.
- **You MUST name and reject 2 plausible WRONG diagnoses.** This prevents first-plausible-answer bias.
- Root causes must be MECE (mutually exclusive, collectively exhaustive). 3-5 of them.
- End with an explicit confidence score (0-100) based on evidence strength.

Output format (markdown):

## CRUX
<Single sentence. If removed, goal becomes achievable.>

## Why not [Alternative Diagnosis A]
<One-sentence plausible alternative diagnosis, then 1-2 sentences on why it's a symptom, not the crux.>

## Why not [Alternative Diagnosis B]
<Same pattern — a different plausible wrong answer, rejected with reasoning.>

## Root Causes (MECE)
- Cause 1
- Cause 2
- Cause 3
(3-5 total)

## Binding Constraints
<The 2-3 constraints that actually shape strategy — not a laundry list.>

## Confidence
<0-100>  — <one sentence naming the biggest source of uncertainty>
"""
    return prompt


async def diagnostician_agent_stream(situation: str, goal: str, constraints: str, document_context=None):
    """Streaming variant — yields text deltas. Caller concatenates for the full crux."""
    prompt = _diagnostician_prompt(situation, goal, constraints, document_context)
    # temp 0.3 — diagnosis wants accuracy, not creativity
    async for delta in _stream_chat(DIAGNOSTICIAN_SYSTEM, prompt, max_tokens=1200, temperature=0.3, timeout=45.0):
        yield delta


async def diagnostician_agent(situation: str, goal: str, constraints: str, document_context=None):
    prompt = _diagnostician_prompt(situation, goal, constraints, document_context)
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": DIAGNOSTICIAN_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1200,
                temperature=0.3
            ),
            timeout=45.0
        )
        return normalize_markdown(response.choices[0].message.content)
    except Exception as e:
        print(f"ERROR in diagnostician_agent: {e}")
        return f"Error: {str(e)}"

# ------------------------------------------------------------
# AGENT 1.5: FRAMEWORK AGENT (Enright)
# ------------------------------------------------------------
async def framework_agent(situation: str, goal: str, frameworks: list = None, document_context=None):
    """
    Framework Agent — Enright's 5 Levels (always) + 2-3 auto-selected frameworks
    that fit the problem domain. Returns a dict with structured analyses + markdown.
    """
    if frameworks is None:
        frameworks = []

    user_hint = ""
    if frameworks:
        user_hint = f"\nThe user suggested these frameworks (use them if truly relevant, otherwise replace with better fits): {', '.join(frameworks)}\n"

    prompt = f"""
You are the Framework Agent. Apply strategic frameworks with precision.

Situation: {situation}
Goal: {goal}
{user_hint}
"""
    if document_context:
        prompt += f"\n**CONTEXT FROM UPLOADED DOCUMENT:**\n{document_context[:8000]}\n\nGround analysis in real company data.\n"

    prompt += """
Your task:

1) **Enright's 5 Levels of Strategy** — always include. Give 1-2 bullet points per level:
   - Supranational · National · Cluster · Industry · Firm

2) **Select 2-3 additional frameworks** that best fit THIS problem from:
   SWOT · PESTEL · Value Chain · Porter Five Forces · Blue Ocean · 7 Powers · Jobs to be Done · BCG Matrix · Ansoff Matrix

   For each chosen framework, explicitly state WHY you chose it in one sentence before applying it. Do NOT apply all frameworks — fit beats breadth.

3) Return a single JSON object with the following shape. Output ONLY the JSON, no markdown fences:

{
  "enright": {
    "supranational": "<2-3 bullets as a single paragraph>",
    "national": "...",
    "cluster": "...",
    "industry": "...",
    "firm": "..."
  },
  "selected_frameworks": [
    {
      "name": "<e.g., Porter Five Forces>",
      "why_chosen": "<one sentence justifying relevance to this problem>",
      "analysis": "<concise markdown analysis — bullets or short paragraphs>"
    }
    // 2-3 entries total
  ],
  "confidence": <0-100 integer>
}
"""

    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are the Framework Agent. Return ONLY valid JSON. Select frameworks by fit, not breadth."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2500,
                temperature=0.4
            ),
            timeout=60.0
        )
        content = response.choices[0].message.content
        content = content.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(content)
        except json.JSONDecodeError as je:
            print(f"JSON Parse Error in framework_agent: {je}")
            return {
                "error": f"JSON parse failed: {je}",
                "markdown": normalize_markdown(content),
                "enright": {},
                "selected_frameworks": [],
                "confidence": None,
            }

        # Build markdown rendering for downstream agents that still consume text
        md_lines = ["## Enright's 5 Levels"]
        en = data.get("enright") or {}
        for level_key, level_title in [
            ("supranational", "Supranational"),
            ("national", "National"),
            ("cluster", "Cluster"),
            ("industry", "Industry"),
            ("firm", "Firm"),
        ]:
            if en.get(level_key):
                md_lines.append(f"**{level_title}** — {en[level_key]}")

        md_lines.append("")
        md_lines.append("## Selected Frameworks")
        for fw in (data.get("selected_frameworks") or []):
            md_lines.append(f"### {fw.get('name', 'Framework')}")
            md_lines.append(f"_Why chosen:_ {fw.get('why_chosen', '')}")
            md_lines.append(fw.get("analysis", ""))
            md_lines.append("")

        data["markdown"] = normalize_markdown("\n".join(md_lines))
        return data

    except Exception as e:
        print(f"ERROR in framework_agent: {e}")
        return {
            "error": str(e),
            "markdown": f"Error: {e}",
            "enright": {},
            "selected_frameworks": [],
            "confidence": None,
        }

# ------------------------------------------------------------
# AGENT 1.8: STRUCTURE AGENT (Decision Tree)
# ------------------------------------------------------------
async def structure_agent(situation: str, goal: str, constraints: str):
    prompt = f"""Create a weighted decision tree as JSON for Cytoscape.js.

Situation: {situation}
Goal: {goal}
Constraints: {constraints}

Return JSON with "nodes" and "edges" arrays.

Node format:
  {{"data": {{"id": "n1", "label": "Short Label", "type": "root|branch|leaf",
            "roi": <0-10 expected ROI if action, null for root/branch>,
            "confidence": <0-100 how confident this branch matters>}}}}

Edge format:
  {{"data": {{"source": "n1", "target": "n2", "weight": <1-10 impact>}}}}

Structure:
  Goal (root) -> 3-4 Drivers (branch) -> 2-3 Sub-drivers each -> Actions (leaf)

Rules:
- Edge weight = how strongly the source influences the target (1 low, 10 high).
- Leaf node ROI = expected business impact per $ invested (0-10 scale).
- Confidence = how strongly-supported by the situation/constraints (0-100).
- Keep labels under 6 words.

Output ONLY the JSON object, no markdown."""
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a strategy agent. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2500,
                temperature=0.2
            ),
            timeout=45.0
        )
        content = response.choices[0].message.content
        content = content.replace("```json", "").replace("```", "").strip()
        
        # Validate JSON before returning
        try:
            parsed = json.loads(content)
            return json.dumps(parsed)  # Return clean JSON
        except json.JSONDecodeError as je:
            print(f"JSON Parse Error in structure_agent: {je}")
            print(f"Raw content: {content[:500]}")
            # Return fallback
            return json.dumps({
                "nodes": [{"data": {"id": "error", "label": "JSON Error - Check Logs", "type": "root"}}],
                "edges": []
            })
    except Exception as e:
        print(f"ERROR in structure_agent: {e}")
        return json.dumps({
            "nodes": [{"data": {"id": "error", "label": "Error Generating Tree", "type": "root"}}],
            "edges": []
        })

# ------------------------------------------------------------
# AGENT: MARKET DATA AGENT (Real-Time Stock Data)
# ------------------------------------------------------------
# Simple in-memory cache for market data — 5 minute TTL, keyed by ticker
_MARKET_DATA_CACHE: Dict[str, tuple] = {}
_MARKET_DATA_TTL_SEC = 300


def _fetch_ticker_snapshot(ticker_symbol: str):
    """Fetch + cache a fundamentals snapshot for a ticker. Returns dict or None."""
    import time
    now = time.time()
    cached = _MARKET_DATA_CACHE.get(ticker_symbol)
    if cached and (now - cached[0]) < _MARKET_DATA_TTL_SEC:
        return cached[1]

    try:
        import yfinance as yf
        info = yf.Ticker(ticker_symbol).info or {}
    except Exception as e:
        return {"ticker": ticker_symbol, "error": str(e)}

    snap = {
        "ticker": ticker_symbol,
        "name": info.get("longName", ticker_symbol),
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "prev_close": info.get("previousClose"),
        "market_cap": info.get("marketCap") or 0,
        "pe_ratio": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "revenue_growth": info.get("revenueGrowth"),       # decimal
        "gross_margin": info.get("grossMargins"),          # decimal
        "profit_margin": info.get("profitMargins"),        # decimal
        "free_cashflow": info.get("freeCashflow"),
        "total_revenue": info.get("totalRevenue"),
        "debt_to_equity": info.get("debtToEquity"),
        "beta": info.get("beta"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        "analyst_target": info.get("targetMeanPrice"),
    }
    _MARKET_DATA_CACHE[ticker_symbol] = (now, snap)
    return snap


async def market_data_agent(situation: str, goal: str):
    """Extract tickers; fetch price + fundamentals; flag outliers."""
    try:
        text = f"{situation} {goal}".upper()

        known_tickers = {
            'TESLA': 'TSLA', 'TSLA': 'TSLA',
            'APPLE': 'AAPL', 'AAPL': 'AAPL',
            'MICROSOFT': 'MSFT', 'MSFT': 'MSFT',
            'AMAZON': 'AMZN', 'AMZN': 'AMZN',
            'GOOGLE': 'GOOGL', 'GOOGL': 'GOOGL', 'ALPHABET': 'GOOGL',
            'META': 'META', 'FACEBOOK': 'META',
            'NVIDIA': 'NVDA', 'NVDA': 'NVDA',
            'NETFLIX': 'NFLX', 'NFLX': 'NFLX',
            'UBER': 'UBER',
            'AIRBNB': 'ABNB',
            'SPOTIFY': 'SPOT',
            'ZOOM': 'ZM',
        }
        found_tickers = {t for name, t in known_tickers.items() if name in text}
        if not found_tickers:
            return "No publicly traded companies detected."

        lines = ["## 📊 MARKET CONTEXT", ""]
        snapshots = []
        for t in found_tickers:
            snap = _fetch_ticker_snapshot(t)
            if not snap:
                continue
            snapshots.append(snap)
            if snap.get("error"):
                lines.append(f"### {t}\n- Error fetching: {snap['error']}\n")
                continue

            price = snap.get("price")
            prev = snap.get("prev_close")
            change_str = ""
            if isinstance(price, (int, float)) and isinstance(prev, (int, float)) and prev:
                pct = (price - prev) / prev * 100
                change_str = f" ({'+' if pct >= 0 else ''}{pct:.2f}%)"

            lines.append(f"### {snap['name']} ({snap['ticker']})")
            if price is not None:
                lines.append(f"- **Price**: ${price:.2f}{change_str}")
            if snap.get("market_cap"):
                lines.append(f"- **Market Cap**: ${snap['market_cap']/1e9:.1f}B")
            if isinstance(snap.get("pe_ratio"), (int, float)):
                lines.append(f"- **P/E**: {snap['pe_ratio']:.1f}  ·  **Forward P/E**: {snap.get('forward_pe') or 'n/a'}")
            if isinstance(snap.get("revenue_growth"), (int, float)):
                lines.append(f"- **Revenue growth (YoY)**: {snap['revenue_growth']*100:.1f}%")
            if isinstance(snap.get("gross_margin"), (int, float)):
                lines.append(f"- **Gross margin**: {snap['gross_margin']*100:.1f}%  ·  **Profit margin**: "
                             f"{(snap['profit_margin']*100):.1f}%" if isinstance(snap.get('profit_margin'), (int,float)) else "")
            if isinstance(snap.get("debt_to_equity"), (int, float)):
                lines.append(f"- **Debt/Equity**: {snap['debt_to_equity']:.2f}  ·  **Beta**: {snap.get('beta') or 'n/a'}")
            if snap.get("52w_low") and snap.get("52w_high"):
                lines.append(f"- **52w range**: ${snap['52w_low']:.2f} – ${snap['52w_high']:.2f}")
            if snap.get("analyst_target") and isinstance(price, (int, float)):
                upside = (snap['analyst_target'] - price) / price * 100
                lines.append(f"- **Analyst target**: ${snap['analyst_target']:.2f} ({'+' if upside>=0 else ''}{upside:.1f}%)")
            lines.append("")

        # Outlier flags — comparison across the fetched basket
        pes = [s['pe_ratio'] for s in snapshots if isinstance(s.get('pe_ratio'), (int, float))]
        if len(pes) >= 2:
            median_pe = sorted(pes)[len(pes)//2]
            outliers = [s for s in snapshots if isinstance(s.get('pe_ratio'), (int, float)) and abs(s['pe_ratio'] - median_pe) > 0.5 * median_pe]
            if outliers:
                lines.append("**⚠ Valuation outliers vs peer median:** " +
                             ", ".join(f"{s['ticker']} (P/E {s['pe_ratio']:.1f})" for s in outliers))

        return "\n".join(lines)
    except Exception as e:
        print(f"ERROR in market_data_agent: {e}")
        return f"Error fetching market data: {str(e)}"

# ------------------------------------------------------------
# AGENT 2: DRIVERS / MARKET FORCES AGENT
# ------------------------------------------------------------
async def market_forces_agent(crux_output: str, document_context=None):
    """Porter-style forces with structured 1-5 scores and explicit ranking.
    Returns a dict with 'forces', 'most_determinative', 'markdown', 'confidence'.
    """
    prompt = f"""
You are the Market Forces & Drivers Agent for Vanguard OS.
Pressure-test the crux using Porter's Five Forces extended with Complementors.

Crux Analysis:
{crux_output}
"""
    if document_context:
        prompt += f"\n**DOCUMENT CONTEXT:**\n{document_context[:7000]}\n\nGround your analysis in real data.\n"

    prompt += """
For each force, give:
- `intensity` on a 1-5 scale (1 = favorable to incumbent, 5 = severe threat)
- `direction`: "worsening" | "stable" | "improving" (next 24 months)
- `note`: one sentence with the dominant mechanism
- `impact_on_crux`: "amplifies" | "mitigates" | "neutral"

The six forces to score:
  1. Threat of New Entrants
  2. Bargaining Power of Suppliers
  3. Bargaining Power of Buyers
  4. Threat of Substitutes
  5. Competitive Rivalry
  6. Complementors

Then name the SINGLE most determinative force with a short rationale.

Return ONLY this JSON (no markdown fences):

{
  "forces": [
    {"name": "New Entrants", "intensity": 1-5, "direction": "...", "note": "...", "impact_on_crux": "..."},
    {"name": "Supplier Power", ...},
    {"name": "Buyer Power", ...},
    {"name": "Substitutes", ...},
    {"name": "Competitive Rivalry", ...},
    {"name": "Complementors", ...}
  ],
  "most_determinative": {"force": "...", "why": "..."},
  "implications": "<1-2 sentences on what this means for guiding policy>",
  "confidence": <0-100 integer>
}
"""

    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are the Market Forces Agent. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1400,
                temperature=0.4
            ),
            timeout=45.0
        )
        content = response.choices[0].message.content.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(content)
        except json.JSONDecodeError as je:
            print(f"JSON Parse Error in market_forces_agent: {je}")
            return {
                "error": f"JSON parse failed: {je}",
                "markdown": normalize_markdown(content),
                "forces": [],
                "most_determinative": None,
                "confidence": None,
            }

        # Build markdown for downstream agents
        md = ["## Market Forces", ""]
        for f in (data.get("forces") or []):
            arrow = {"worsening": "↑", "improving": "↓", "stable": "→"}.get(f.get("direction", ""), "·")
            md.append(f"- **{f.get('name', '?')}** ({f.get('intensity', '?')}/5 {arrow}) — {f.get('note', '')}")
        md.append("")
        mdet = data.get("most_determinative") or {}
        if mdet.get("force"):
            md.append(f"**Most determinative force:** {mdet['force']} — {mdet.get('why','')}")
        if data.get("implications"):
            md.append("")
            md.append(f"**Implications:** {data['implications']}")

        data["markdown"] = normalize_markdown("\n".join(md))
        return data

    except Exception as e:
        print(f"ERROR in market_forces_agent: {e}")
        return {
            "error": str(e),
            "markdown": f"Error: {e}",
            "forces": [],
            "most_determinative": None,
            "confidence": None,
        }

# ------------------------------------------------------------
# AGENT 3: FINANCIAL AGENT
# ------------------------------------------------------------
async def financial_agent(crux_output: str, drivers_output: str, document_context=None, user_numbers: str = ""):
    prompt = f"""You are the Financial Agent. Quantify feasibility and impact across three scenarios.

Crux: {crux_output}
Drivers: {drivers_output}
"""

    if user_numbers and user_numbers.strip():
        prompt += (
            "\n**USER-PROVIDED NUMBERS (authoritative — use these, do not estimate over them):**\n"
            f"{user_numbers.strip()}\n"
            "When these overlap with your projections (revenue, CAC, LTV, churn, margin, customer count),"
            " use the user's figures directly. Extrapolate only the missing pieces.\n\n"
        )

    if document_context:
        prompt += f"\n**FINANCIAL DATA FROM DOCUMENT:**\n{document_context[:8000]}\n\nUse actual financials from this document to ground your projections.\n\n"

    prompt += """Task: Output a JSON object with financial projections across Base, Bull, and Bear scenarios,
plus unit economics for the business.

JSON Format (all dollar values in millions unless noted):
{{
  "scenarios": {{
    "base": {{
      "initial_investment": <number>,
      "cash_flows": [<Y1>, <Y2>, <Y3>, <Y4>, <Y5>],
      "discount_rate": <decimal, e.g., 0.10>,
      "revenue_y5": <number>,
      "ebitda_y5": <number>
    }},
    "bull": {{ ...same shape, more optimistic... }},
    "bear": {{ ...same shape, pessimistic... }}
  }},
  "unit_economics": {{
    "cac": <customer acquisition cost, $ per customer>,
    "ltv": <lifetime value, $ per customer>,
    "gross_margin": <decimal, e.g., 0.70 for 70%>,
    "churn_rate": <annual decimal, e.g., 0.08 for 8%>,
    "arpu_monthly": <monthly ARPU in dollars, optional>
  }},
  "assumptions": "<1-2 sentences naming the key assumptions>",
  "narrative": "<2-3 paragraph financial analysis in markdown>"
}}

Rules:
- Anchor to real industry benchmarks when possible
- Bull = upside case (faster growth, lower churn); Bear = downside (slower, higher churn)
- Cash flows are annual NET cash flow (revenue - costs) in millions
- Output ONLY the JSON object, no markdown code fences"""

    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a financial analyst. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1800,
                temperature=0.3
            ),
            timeout=45.0
        )

        content = response.choices[0].message.content
        content = content.replace("```json", "").replace("```", "").strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError as je:
            print(f"JSON Parse Error in financial_agent: {je}")
            return {
                "error": f"JSON parse failed: {je}",
                "markdown": normalize_markdown(content),
                "scenarios": {},
                "unit_economics": {},
                "narrative": content,
                "assumptions": "",
            }

        # Compute metrics for each scenario in Python (more reliable than LLM math)
        scenarios_out = {}
        for name in ("base", "bull", "bear"):
            sc = data.get("scenarios", {}).get(name) or {}
            metrics = calculate_financial_metrics(
                initial_investment=sc.get("initial_investment", 0) or 0,
                cash_flows=sc.get("cash_flows", []) or [],
                discount_rate=sc.get("discount_rate", 0.10) or 0.10,
            )
            scenarios_out[name] = {**sc, "metrics": metrics}

        # Derive unit-economics ratios
        ue = dict(data.get("unit_economics") or {})
        cac = float(ue.get("cac") or 0)
        ltv = float(ue.get("ltv") or 0)
        gross_margin = float(ue.get("gross_margin") or 0)
        arpu_monthly = float(ue.get("arpu_monthly") or 0)
        ue["ltv_cac_ratio"] = round(ltv / cac, 2) if cac > 0 else None
        # Payback months: CAC / (monthly ARPU * gross margin). Skip if we can't compute.
        if arpu_monthly > 0 and gross_margin > 0 and cac > 0:
            ue["payback_months"] = round(cac / (arpu_monthly * gross_margin), 1)
        else:
            ue["payback_months"] = None

        # Rule of 40 from base scenario: growth Y1→Y5 CAGR + EBITDA margin
        base = scenarios_out.get("base", {})
        cfs = base.get("cash_flows") or []
        rev5 = float(base.get("revenue_y5") or 0)
        ebitda5 = float(base.get("ebitda_y5") or 0)
        cagr = None
        if len(cfs) >= 5 and cfs[0] and cfs[0] > 0 and cfs[4] and cfs[4] > 0:
            cagr = (cfs[4] / cfs[0]) ** (1 / 4) - 1
        ebitda_margin = (ebitda5 / rev5) if rev5 > 0 else None
        rule_of_40 = None
        if cagr is not None and ebitda_margin is not None:
            rule_of_40 = round((cagr + ebitda_margin) * 100, 1)
        ue["cagr"] = round(cagr * 100, 1) if cagr is not None else None
        ue["ebitda_margin"] = round(ebitda_margin * 100, 1) if ebitda_margin is not None else None
        ue["rule_of_40"] = rule_of_40

        narrative = data.get("narrative", "")
        assumptions = data.get("assumptions", "")

        # Markdown rendering — kept for downstream agents that still consume text
        base_metrics = scenarios_out.get("base", {}).get("metrics", {})
        markdown = f"""## 📊 Financial Analysis

{narrative}

**Base-case metrics:** NPV {base_metrics.get('npv_str', 'N/A')} · IRR {base_metrics.get('irr_str', 'N/A')} · Payback {base_metrics.get('payback_str', 'N/A')} · ROI {base_metrics.get('roi_str', 'N/A')}

**Key assumptions:** {assumptions}
"""

        return {
            "scenarios": scenarios_out,
            "unit_economics": ue,
            "narrative": narrative,
            "assumptions": assumptions,
            "markdown": normalize_markdown(markdown),
        }

    except Exception as e:
        print(f"ERROR in financial_agent: {e}")
        return {
            "error": str(e),
            "markdown": f"Error: {e}",
            "scenarios": {},
            "unit_economics": {},
            "narrative": "",
            "assumptions": "",
        }


def calculate_financial_metrics(initial_investment, cash_flows, discount_rate=0.10):
    """Compute NPV, IRR, payback, and ROI. Returns numeric fields plus pre-formatted strings."""
    try:
        import numpy_financial as npf
        import numpy as np

        # Pad to 5 cash flows
        cash_flows = list(cash_flows or [])
        if len(cash_flows) < 5:
            cash_flows = cash_flows + [0] * (5 - len(cash_flows))

        full_cash_flows = [-initial_investment] + cash_flows

        npv = float(npf.npv(discount_rate, full_cash_flows))

        try:
            irr_val = npf.irr(full_cash_flows)
            irr = float(irr_val) if not np.isnan(irr_val) else None
        except Exception:
            irr = None

        # Payback period with linear interpolation inside the crossing year
        cumulative = 0.0
        payback_years = None
        for i, cf in enumerate(cash_flows, 1):
            cumulative += cf
            if cumulative >= initial_investment and cf > 0:
                prev_cumulative = cumulative - cf
                fraction = (initial_investment - prev_cumulative) / cf
                payback_years = (i - 1) + fraction
                break

        total_gain = sum(cash_flows) - initial_investment
        roi = (total_gain / initial_investment) * 100 if initial_investment and initial_investment > 0 else 0.0

        return {
            "npv": round(npv, 2),
            "irr": round(irr * 100, 1) if irr is not None else None,
            "payback_years": round(payback_years, 2) if payback_years is not None else None,
            "roi": round(roi, 0),
            "npv_str": f"${npv:.1f}M",
            "irr_str": f"{irr*100:.1f}%" if irr is not None else "N/A",
            "payback_str": f"{payback_years:.1f} yrs" if payback_years is not None else ">5 yrs",
            "roi_str": f"{roi:.0f}%",
        }

    except ImportError:
        return {
            "npv": None, "irr": None, "payback_years": None, "roi": None,
            "npv_str": "numpy-financial missing", "irr_str": "N/A",
            "payback_str": "N/A", "roi_str": "N/A",
        }
    except Exception as e:
        return {
            "npv": None, "irr": None, "payback_years": None, "roi": None,
            "npv_str": f"Error: {e}", "irr_str": "N/A",
            "payback_str": "N/A", "roi_str": "N/A",
        }






# ------------------------------------------------------------
# AGENT 4: OPS / EXECUTION AGENT
# ------------------------------------------------------------
async def ops_agent(crux_output: str, financial_output: str, document_context=None, portfolio_output: str = ""):
    prompt = f"""
You are the Operations & Execution Agent. Design a coherent, sequenced action system.

Crux:
{crux_output}

Financial Constraints:
{financial_output}
"""
    if portfolio_output and portfolio_output.strip():
        prompt += (
            "\n**STRATEGIC OPTION TO EXECUTE:**\n"
            "Below is the Strategy Portfolio. Identify the PRIMARY RECOMMENDATION and tailor your\n"
            "entire plan to THAT specific option. Reference the option name in your pillars.\n\n"
            f"{portfolio_output[:9000]}\n"
        )

    if document_context:
        prompt += f"\n**ORGANIZATIONAL CONTEXT:**\n{document_context[:7000]}\n\n"

    prompt += """
Rumelt principle: actions must be **coherent** (reinforcing) — not a laundry list.

Output these sections in Markdown:

## 1) Action Pillars (3-5, MECE)
For each pillar:
- Name (punchy, <8 words)
- Why this pillar matters in one sentence
- 2-3 concrete actions underneath

## 2) 30 / 60 / 90 Day Plan
A table (Markdown) with columns: `Timeframe | Action | Owner (Role) | Success Signal`
- 30-day actions: quick wins, team assembly, data baselines
- 60-day actions: pilots, partnerships, capability builds
- 90-day actions: at-scale rollouts, measurement, decision gates

## 3) RACI Snapshot
For each Action Pillar, one line: `Pillar — R: <role>, A: <role>, C: <roles>, I: <roles>`

## 4) Dependencies & Critical Path
List 2-3 blocking dependencies. What MUST ship first for the rest to work?

## 5) Risks & Mitigations
3 risks, each with a concrete mitigation. Focus on execution risks, not strategy risks (Red Team covers those).

## 6) Decision Gates
For each 30/60/90 phase, one metric that triggers a pivot vs continue decision.
"""
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are the Operations & Execution Agent. Design a sequenced, coherent plan."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1600,
                temperature=0.4
            ),
            timeout=45.0
        )
        return normalize_markdown(response.choices[0].message.content)
    except Exception as e:
        print(f"ERROR in ops_agent: {e}")
        return f"Error: {str(e)}"

# ------------------------------------------------------------
# AGENT 5: AI & TECHNOLOGY AGENT
# ------------------------------------------------------------
async def tech_agent(ops_output: str, document_context=None):
    prompt = f"""
You are the AI & Technology Agent. Your job is DOMAIN-SPECIFIC technology strategy — not generic "we should use AI."

Action Plan to support:
{ops_output}
"""
    if document_context:
        prompt += f"\n**TECH/R&D CONTEXT:**\n{document_context[:6000]}\n\n"

    prompt += """
First, name the specific industry / domain this strategy operates in. Then tailor everything below to that domain.

Output in Markdown:

## 1) Domain Context
One sentence naming the industry + the dominant tech stack incumbents use today.
One sentence on what's changing (LLMs, vertical AI, automation, platform shifts, etc.).

## 2) Tech Leverage Points (3-5)
For each: `<Leverage point>: what it replaces/amplifies, and the concrete capability needed.`
Rank by expected impact. Be specific — "churn-prediction ML scoring nightly batch" beats "use AI for retention."

## 3) Build vs Buy vs Partner
A short table with columns: `Capability | Build | Buy | Partner | Recommendation | Why`
Cover the 3 most strategically important capabilities from section 2.

## 4) Data & Systems Requirements
What must exist for the leverage points to work? (data sources, integrations, MLOps, governance)

## 5) Tech Risks Specific to This Strategy
3 risks — focus on ones tied to THIS domain (regulatory, data scarcity, vendor lock-in, model drift, etc.). Generic risks like "AI might be wrong" are rejected.
"""
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are the AI & Technology Agent. Ground every recommendation in the specific domain."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1200,
                temperature=0.4
            ),
            timeout=45.0
        )
        return normalize_markdown(response.choices[0].message.content)
    except Exception as e:
        print(f"ERROR in tech_agent: {e}")
        return f"Error: {str(e)}"

# ------------------------------------------------------------
# AGENT 6: HUMAN FACTORS AGENT
# ------------------------------------------------------------
async def human_factors_agent(ops_output: str, document_context=None):
    prompt = f"""
You are the Human Factors Agent. Apply Kotter's 8-Step Change Model to THIS strategy.

Action Plan:
{ops_output}
"""
    if document_context:
        prompt += f"\n**ORGANIZATIONAL/CULTURAL CONTEXT:**\n{document_context[:6000]}\n\n"

    prompt += """
Walk the 8 steps in order. For each step, give 2-3 specific actions tied to THIS strategy. Generic advice is rejected.

Output in Markdown:

## Kotter's 8-Step Change Plan

### 1. Create Urgency
Why acting now is non-negotiable — specific to this strategy (competitive window, financial clock, regulatory moment).

### 2. Form a Powerful Coalition
Name the roles (not individuals) that MUST be in the coalition. Why each.

### 3. Create a Vision for Change
One sentence capturing the change in plain language a frontline employee can repeat.

### 4. Communicate the Vision
3 channels + 3 key moments where the vision gets reinforced.

### 5. Remove Obstacles
Name 2-3 structural obstacles (incentives, reporting lines, metrics, tooling). Propose the removal mechanism.

### 6. Create Short-Term Wins
Name 2-3 wins visible within the first 60-90 days that build momentum.

### 7. Build on the Change
How does success in the first wave compound? What gets un-blocked?

### 8. Anchor in Culture
Which rituals, metrics, or recognition practices make this permanent?

## Adoption Blockers (Top 3)
For each: `Blocker → Evidence it's real → Counter-measure`

## Stakeholder Map
Brief list: who wins, who loses, who decides, who executes. Flag the stakeholders whose opposition is most dangerous.
"""
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are the Human Factors Agent. Apply Kotter's 8 Steps with domain specificity."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1400,
                temperature=0.4
            ),
            timeout=45.0
        )
        return normalize_markdown(response.choices[0].message.content)
    except Exception as e:
        print(f"ERROR in human_factors_agent: {e}")
        return f"Error: {str(e)}"

# ------------------------------------------------------------
# AGENT 6.5: STRATEGY PORTFOLIO AGENT
# ------------------------------------------------------------
async def strategy_portfolio_agent(situation: str, goal: str, constraints: str, crux: str, document_context=None):
    print(f"DEBUG: Starting Portfolio Agent. Situation len: {len(situation)}, Crux len: {len(crux)}")
    prompt = f"""
SYSTEM PROMPT — STRATEGY PORTFOLIO AGENT

You are the Strategy Portfolio Agent inside Vanguard OS, an advanced multi-agent strategic intelligence system.
Your mission is to take a structured strategic situation (S–G–C: Situation, Goals, Constraints), and generate, analyze, stress-test, score, and compare multiple distinct strategic options—not just one.

You must think like:
- a McKinsey senior partner,
- a BCG portfolio strategist,
- a Bain transformation architect,
- and a Rumelt-style crux practitioner.

Your output must be sharp, analytical, explicit, and structured.

🔷 PROCESS OVERVIEW (Follow EXACTLY in this order)

1. CRUX RECAP (Short + Surgical)
Restate:
- The situation
- The crux (the real bottleneck / center of gravity)
- The non-negotiable constraints
- Any strategic goals or KPIs
Keep this to 3–5 sentences maximum.

2. GENERATE 3–4 DISTINCT STRATEGY OPTIONS
Each option MUST represent meaningfully different strategic logic—not variations of the same theme.
For each option, provide:
A. Strategy Option Name (Short, punchy label)
B. Core Strategic Idea (2–3 sentences)
C. Guiding Policy (How will this option win?)
D. Coherent Actions (3–7 bullets)
E. Primary Leverage Point (Where the power comes from)

Ensure diversity across options:
- One focused wedge play
- One broad transformation play
- One defensive risk-limiting play
- Optional: one bold upside / moonshot play

3. SCORE EACH OPTION — STRATEGY SCORECARD
For each option, assign scores from 1–10 for:
- Strategic Fit with Diagnosis & Crux
- Impact Potential on Goals
- Feasibility / Execution Difficulty (higher = more feasible)
- Time to Impact (higher = faster)
- Risk Exposure (higher = more risk)
- Differentiation vs Competitors
- Alignment with Constraints
Then provide a 1–2 sentence interpretation of the scores.

4. MINI WAR-GAME / FAILURE STRESS TEST FOR EACH OPTION
For every option, simulate:
A. Key Failure Mode
B. Competitor Response
C. Internal Resistance
D. Mitigation / Countermeasures

5. PORTFOLIO COMPARISON & RECOMMENDATION
Compare all options side-by-side with an EXPLICIT TRADEOFF MATRIX:
For each pair of options (A vs B, A vs C, B vs C), give one sentence:
"Option X beats Y on <dimension>, but loses on <other dimension>."

Then provide:
- Primary Recommendation (Option X) with rationale.
- Secondary / Backup Option (Option Y) with pivot triggers.

6. KILL CRITERIA (per option — this is mandatory)
For EACH option, name 1-2 observable signals that mean "abandon this option within 90 days."
Format: `If <measurable condition>, kill this option.`
Kill criteria must be falsifiable. Vague ones like "if market changes" are rejected.

🔷 INPUT DATA
Situation: {situation}
Goal: {goal}
Constraints: {constraints}
Crux Diagnosis: {crux}

🔷 OUTPUT FORMAT (REQUIRED)
Return your output using the following structure exactly:

1. Crux Recap
(4–6 sentences)

2. Strategy Options
Option A — [Name]
...
Option B — [Name]
...
Option C — [Name]
...

3. Strategy Scorecards
Option A
...
Option B
...

4. Mini War-Games by Option
Option A
...
Option B
...

5. Portfolio Comparison & Final Recommendation
Tradeoff Matrix (A vs B, A vs C, B vs C):
Primary Recommendation:
Backup Option:

6. Kill Criteria
Option A: If <condition>, kill within 90 days.
Option B: ...
Option C: ...

No fluff. No generic corporate filler. Explicit trade-offs.
"""
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are the Strategy Portfolio Agent. Generate distinct strategic options and scorecards."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=3000,
                temperature=0.6
            ),
            timeout=120.0 # Increased timeout for complex portfolio generation
        )
        content = response.choices[0].message.content
        if not content:
            return "## Strategy Portfolio Generation Failed\n\nThe agent returned no content. Please try refining the prompt."
        
        return normalize_markdown(content)
    except Exception as e:
        print(f"ERROR in strategy_portfolio_agent: {e}")
        return f"## Strategy Portfolio Error\n\nSystem encountered an error: {str(e)}"

# ------------------------------------------------------------
# AGENT 7: RED TEAM AGENT (V2)
# ------------------------------------------------------------
async def red_team_agent_v2(crux: str, drivers: str, financial: str, ops: str, tech: str, human: str, document_context=None, portfolio: str = ""):
    prompt = f"""
You are the Red Team Agent.

Strategy Components:
[CRUX]: {crux}
[DRIVERS]: {drivers}
[FINANCIAL]: {financial}
[OPS]: {ops}
[TECH]: {tech}
[HUMAN]: {human}
"""
    if portfolio and portfolio.strip():
        # Critical addition: red team must attack the SPECIFIC recommended option.
        prompt += (
            "\n[STRATEGY PORTFOLIO — contains the distinct options and the PRIMARY RECOMMENDATION]:\n"
            f"{portfolio[:8000]}\n\n"
            "When you critique, name the specific recommended option and attack its logic.\n"
            "Abstract critiques of ops or tech alone are rejected — tie each objection back to the option's strategic bet.\n"
        )

    if document_context:
        prompt += f"\n**COMPANY CONTEXT:**\n{document_context[:6000]}\n\nUse this to make your challenges more specific and grounded.\n\n"

    prompt += """Challenge the strategy from 3 perspectives: **CFO**, **COO**, **CTO**.

For each perspective, produce ONE sharp objection (not three) plus a severity score and a steelmanned counter.
**Reference the primary recommended option by name** — your objection should be specific to THAT option,
not a generic critique of the strategy family.

**{Persona}** — Severity: {HIGH | MEDIUM | LOW}
- **Objection:** one sentence attacking the recommended option specifically.
- **Why it matters:** one sentence on the concrete harm if this option proceeds unchanged.
- **Steelman counter:** one sentence on the strongest rebuttal or mitigation — don't just critique, propose.

Then end with:

## Top Fix
The single most important change the team should make to the recommended option based on the critiques above. One sentence.

Rules:
- Keep it tight. No listicles of 5 objections. Severity-gated: only name issues that rise above LOW.
- If no objection rises to MEDIUM for a persona, say "No substantive objection" for that persona.
- No generic criticism ("This is risky") — must be specific to the recommended option.
"""
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are the Red Team Agent. One sharp objection per persona. Steelman, don't just critique."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=900,
                temperature=0.7
            ),
            timeout=45.0
        )
        return normalize_markdown(response.choices[0].message.content)
    except Exception as e:
        print(f"ERROR in red_team_agent_v2: {e}")
        return f"Error: {str(e)}"

# ------------------------------------------------------------
# AGENT 8: SYNTHESIZER AGENT
# ------------------------------------------------------------
SYNTHESIZER_SYSTEM = "You are the Synthesizer Agent. Create a McKinsey-level strategy page."


def _synthesizer_prompt(crux, drivers, financial, ops, tech, human, red_team, document_context=None) -> str:
    prompt = f"""
You are the Synthesizer Agent.

Inputs:
[CRUX]: {crux}
[DRIVERS]: {drivers}
[FINANCIAL]: {financial}
[OPS]: {ops}
[TECH]: {tech}
[HUMAN]: {human}
[RED TEAM]: {red_team}
"""
    if document_context:
        prompt += f"\n**SOURCE DOCUMENT:**\n{document_context[:8000]}\n\nGround your final strategy in facts from this document.\n\n"

    prompt += """You consolidate everything into a McKinsey-style strategy memo with this EXACT structure:

## The Bet
One single sentence, first thing on the page, in bold. Starts with "We bet that..." and names the wager the strategy makes.

## Executive Summary
6-10 lines. Front-loaded with the "so what" — the reader should know the recommendation after the first two lines.

## Diagnosis (The Crux)
2-3 sentences. Reference the crux directly.

## Guiding Policy (with tradeoffs)
The strategic direction and what we're NOT doing to make it coherent.

## What We Are NOT Doing
3-5 explicit non-actions. This is a McKinsey discipline — naming what you're giving up sharpens focus. Format:
- **Not <action>** — because <reason>.

## Coherent Actions
MECE, sequenced. Reference the Ops output for the 30/60/90 plan.

## KPIs & Leading Indicators
5-7 metrics. Mix lagging (revenue, retention) and leading (activation, cycle time).

## Key Risks & Mitigations
Reference the Red Team's top fix. 3 risks max, each with a concrete mitigation.

## 30/60/90 Plan (Summary)
One line per phase — pulled from the Ops agent.

Rules:
- Bullet-driven, tight, no corporate filler.
- Preserve the tradeoffs — don't smooth them over.
- "What we are NOT doing" is MANDATORY — not optional.
"""
    return prompt


async def synthesizer_agent_stream(crux: str, drivers: str, financial: str, ops: str, tech: str, human: str, red_team: str, document_context=None):
    """Streaming variant — yields text deltas. Caller concatenates for the full synthesis."""
    prompt = _synthesizer_prompt(crux, drivers, financial, ops, tech, human, red_team, document_context)
    async for delta in _stream_chat(SYNTHESIZER_SYSTEM, prompt, max_tokens=1500, temperature=0.5, timeout=60.0):
        yield delta


async def synthesizer_agent(crux: str, drivers: str, financial: str, ops: str, tech: str, human: str, red_team: str, document_context=None):
    prompt = _synthesizer_prompt(crux, drivers, financial, ops, tech, human, red_team, document_context)
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": SYNTHESIZER_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1500,
                temperature=0.5
            ),
            timeout=60.0
        )
        return normalize_markdown(response.choices[0].message.content)
    except Exception as e:
        print(f"ERROR in synthesizer_agent: {e}")
        return f"Error: {str(e)}"

# ------------------------------------------------------------
# AGENT 9: DEEP DIVE AGENT
# ------------------------------------------------------------
async def deep_dive_agent(topic: str, context: str):
    """
    context is now the full mission context — synthesizer, portfolio, financial, red team,
    whatever the frontend can gather. We accept up to ~12K chars so financial + red team
    insights aren't lost (previously truncated at 2K).
    """
    ctx = context or ""
    if len(ctx) > 12000:
        ctx = ctx[:12000] + "\n\n[...truncated]"

    prompt = f"""
You are the Deep Dive Specialist Agent.

Drill into this specific point: "{topic}"

Full mission context (use EVERY signal available — financial, red team, ops, etc.):
---
{ctx}
---

Your Task — produce a surgical deep dive:

## Why this matters
Strategic rationale in 2-3 sentences. Tie directly to the crux.

## Hidden risks & second-order effects
2-3 risks that WOULD NOT be obvious from a quick read.

## First 3 moves (concrete)
Sequenced, with owners and timelines (weeks, not months).

## KPI impact
Which metrics move and by how much (rough order of magnitude).

## Related questions worth asking next
2-3 follow-up drill-downs the user should consider.

Be concise, specific, grounded in the context. No generic corporate filler.
"""
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are the Deep Dive Specialist. Ground every recommendation in the provided context."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1200,
                temperature=0.6
            ),
            timeout=60.0
        )
        return normalize_markdown(response.choices[0].message.content)
    except Exception as e:
        print(f"ERROR in deep_dive_agent: {e}")
        return f"Error: {str(e)}"

# ------------------------------------------------------------
# AGENT 10: STRATEGY MAP AGENT (Visual Dependency Graph)
# ------------------------------------------------------------
async def map_agent(crux: str, drivers: str, financial: str, ops: str, tech: str, human: str, red_team: str):
    prompt = f"""
You are the Visual Strategy Mapper.
Visualize the strategy as a causal dependency graph, INCLUDING feedback loops where they exist.
Feedback loops (virtuous or vicious cycles) are often the most important strategic dynamics — don't flatten them.

Inputs:
[CRUX]: {crux}
[DRIVERS]: {drivers}
[FINANCIAL]: {financial}
[OPS]: {ops}
[TECH]: {tech}
[HUMAN]: {human}
[RED TEAM]: {red_team}

Task:
Generate a causal dependency graph as a JSON object compatible with Cytoscape.js.
The JSON must have two lists: "nodes" and "edges", plus an optional "loops" list.

Node format:
  {{ "data": {{ "id": "unique_id", "label": "Short Label", "type": "action|outcome|goal" }} }}

Edge format:
  {{ "data": {{ "source": "source_id", "target": "target_id",
            "weight": <1-10 impact strength>,
            "polarity": "reinforcing" | "balancing" }} }}

Loops format (optional but strongly encouraged where they exist):
  {{ "type": "reinforcing" | "balancing", "nodes": ["id1", "id2", "id3"], "description": "one sentence" }}

Rules:
- Actions (Square) -> Outcomes (Circle) -> Goals (Diamond) is the primary direction,
  but include backward edges when there are real feedback effects.
- `polarity: reinforcing` = source INCREASE causes target INCREASE (same direction).
- `polarity: balancing` = source INCREASE causes target DECREASE (counteracting).
- Identify 1-2 major feedback loops if present. Label them clearly in the `loops` array.
- STRICTLY return ONLY the JSON object. No markdown, no text.
"""
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are the Strategy Mapper. Output raw JSON only."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1000,
                temperature=0.3
            ),
            timeout=45.0
        )
        content = response.choices[0].message.content
        content = content.replace("```json", "").replace("```", "").strip()
        
        # Validate JSON before returning
        try:
            parsed = json.loads(content)
            return json.dumps(parsed)  # Return clean JSON
        except json.JSONDecodeError as je:
            print(f"JSON Parse Error in map_agent: {je}")
            print(f"Raw content: {content[:500]}")
            # Return fallback
            return json.dumps({
                "nodes": [{"data": {"id": "error", "label": "JSON Error - Check Logs", "type": "goal"}}],
                "edges": []
            })
    except Exception as e:
        print(f"ERROR in map_agent: {e}")
        return json.dumps({
            "nodes": [{"data": {"id": "error", "label": "Error Generating Map", "type": "goal"}}],
            "edges": []
        })

# ------------------------------------------------------------
# STREAMING ORCHESTRATOR (V2)
# ------------------------------------------------------------
async def run_vanguard_pipeline_stream(inputs):
    """
    Async Generator that yields JSON chunks as agents finish.
    Follows the V2 sequential flow:
    Diagnostician -> Drivers -> Financial -> Ops -> AI/Tech -> Human Factors -> Red Team -> Synthesizer
    """
    
    # Extract inputs
    situation = inputs.get("situation", "")
    goal = inputs.get("goal", "")
    constraints = inputs.get("constraints", "")
    document_context = inputs.get("document_context", None)
    
    # 0. Market Data (Optional, runs early)
    yield json.dumps({"type": "status", "data": "Fetching Market Data..."}) + "\n"
    market_data_out = await market_data_agent(situation, goal)
    if market_data_out != "No publicly traded companies detected.":
        yield json.dumps({"type": "market_data", "data": market_data_out}) + "\n"
    
    # 1. Diagnostician (CRUX)
    yield json.dumps({"type": "status", "data": "Running Diagnostician..."}) + "\n"

    # Check for Deep Dive Request
    if inputs.get("mode") == "deep_dive":
        topic = inputs.get("topic", "")
        context = inputs.get("context", "")
        yield json.dumps({"type": "status", "data": "Deep Diving..."}) + "\n"
        yield json.dumps({"type": "thought", "data": f"Deep Dive: Analyzing '{topic}'..."}) + "\n"
        
        dd_out = await deep_dive_agent(topic, context)
        yield json.dumps({"type": "deep_dive", "data": dd_out}) + "\n"
        yield json.dumps({"type": "done", "data": "done"}) + "\n"
        return

    yield json.dumps({"type": "status", "data": "Diagnosing the Crux..."}) + "\n"
    yield json.dumps({"type": "thought", "data": "Diagnostician: Analyzing situation and constraints..."}) + "\n"

    # 1. Diagnostician (streaming — users watch this one first)
    accumulated = []
    async for delta in diagnostician_agent_stream(situation, goal, constraints, document_context):
        accumulated.append(delta)
        yield json.dumps({"type": "delta", "pane": "diagnosisContent", "data": delta}) + "\n"
    crux_out = normalize_markdown("".join(accumulated))
    yield json.dumps({"type": "thought", "data": "Diagnostician: Crux identified. Isolating root causes."}) + "\n"
    yield json.dumps({"type": "diagnostician", "data": crux_out}) + "\n"
    
    yield json.dumps({"type": "status", "data": "Applying Strategic Frameworks..."}) + "\n"

    # 1.5 & 1.8 Frameworks & Structure (Parallel) — framework now returns a dict
    selected_frameworks = inputs.get("frameworks", [])
    fw_task = asyncio.create_task(framework_agent(situation, goal, selected_frameworks, document_context))
    struct_task = asyncio.create_task(structure_agent(situation, goal, constraints))

    fw_out, struct_out = await asyncio.gather(fw_task, struct_task)
    fw_md = fw_out.get("markdown", "") if isinstance(fw_out, dict) else str(fw_out)

    yield json.dumps({"type": "frameworks", "data": fw_out}) + "\n"
    yield json.dumps({"type": "thought", "data": "Structure: Decision tree logic mapped."}) + "\n"
    yield json.dumps({"type": "structure", "data": struct_out}) + "\n"
    
    yield json.dumps({"type": "status", "data": "Generating Portfolio + Market Forces..."}) + "\n"
    yield json.dumps({"type": "thought", "data": "Portfolio + Market Forces: Running in parallel..."}) + "\n"

    # Portfolio depends on crux only; Market Forces depends on crux only.
    # Previously sequential — now parallel. Shaves ~25-40s.
    effective_crux = crux_out if crux_out and len(crux_out) > 20 else f"Situation: {situation}\nGoal: {goal}"

    portfolio_task = asyncio.create_task(
        strategy_portfolio_agent(situation, goal, constraints, effective_crux, document_context)
    )
    drivers_task = asyncio.create_task(market_forces_agent(crux_out, document_context))

    portfolio_out, drivers_out = await asyncio.gather(portfolio_task, drivers_task)
    # market_forces_agent now returns a dict with structured scores + markdown
    drivers_md = drivers_out.get("markdown", "") if isinstance(drivers_out, dict) else str(drivers_out)

    yield json.dumps({"type": "thought", "data": "Portfolio: Options scored and war-gamed."}) + "\n"
    yield json.dumps({"type": "portfolio", "data": portfolio_out}) + "\n"
    yield json.dumps({"type": "drivers", "data": drivers_out}) + "\n"

    yield json.dumps({"type": "status", "data": "Modeling Financials..."}) + "\n"

    # Financial depends on crux + drivers (markdown form) — start immediately after drivers lands.
    user_numbers = inputs.get("numbers", "")
    financial_out = await financial_agent(crux_out, drivers_md, document_context, user_numbers)
    financial_md = financial_out.get("markdown", "") if isinstance(financial_out, dict) else str(financial_out)
    yield json.dumps({"type": "financial", "data": financial_out}) + "\n"

    yield json.dumps({"type": "status", "data": "Designing Operations..."}) + "\n"

    # 4. Ops Agent
    ops_out = await ops_agent(crux_out, financial_md, document_context, portfolio_output=portfolio_out)
    yield json.dumps({"type": "ops", "data": ops_out}) + "\n"
    
    yield json.dumps({"type": "status", "data": "Evaluating Technology & Human Factors..."}) + "\n"
    
    # 5 & 6. Tech and Human Factors (Can run in parallel)
    tech_task = asyncio.create_task(tech_agent(ops_out, document_context))
    human_task = asyncio.create_task(human_factors_agent(ops_out, document_context))
    
    tech_out, human_out = await asyncio.gather(tech_task, human_task)
    
    yield json.dumps({"type": "tech", "data": tech_out}) + "\n"
    yield json.dumps({"type": "human_factors", "data": human_out}) + "\n"
    
    yield json.dumps({"type": "status", "data": "Red Teaming Strategy..."}) + "\n"
    
    # 7. Red Team
    yield json.dumps({"type": "thought", "data": "Red Team: Challenging assumptions (CFO/COO/CTO perspectives)..."}) + "\n"
    red_team_out = await red_team_agent_v2(crux_out, drivers_md, financial_md, ops_out, tech_out, human_out, document_context, portfolio=portfolio_out)
    yield json.dumps({"type": "red_team", "data": red_team_out}) + "\n"
    
    yield json.dumps({"type": "status", "data": "Synthesizing Final Strategy..."}) + "\n"
    
    # 8. Synthesizer & Strategy Map (Parallel)
    yield json.dumps({"type": "thought", "data": "Synthesizer: Consolidating final strategy..."}) + "\n"
    yield json.dumps({"type": "thought", "data": "Mapper: Visualizing causal logic..."}) + "\n"
    
    # Synthesizer streams tokens directly; strategy map runs in parallel via a side task.
    map_task = asyncio.create_task(map_agent(crux_out, drivers_md, financial_md, ops_out, tech_out, human_out, red_team_out))

    synth_accumulated = []
    async for delta in synthesizer_agent_stream(crux_out, drivers_md, financial_md, ops_out, tech_out, human_out, red_team_out, document_context):
        synth_accumulated.append(delta)
        yield json.dumps({"type": "delta", "pane": "synthesizerContent", "data": delta}) + "\n"
    final_strategy = normalize_markdown("".join(synth_accumulated))

    map_out = await map_task

    yield json.dumps({"type": "synthesizer", "data": final_strategy}) + "\n"
    yield json.dumps({"type": "strategy_map", "data": map_out}) + "\n"
    
    yield json.dumps({"type": "status", "data": "Mission Complete."}) + "\n"
    yield json.dumps({"type": "done", "data": "done"}) + "\n"

# ------------------------------------------------------------
# Legacy/Helper Functions (Kept for compatibility if needed)
# ------------------------------------------------------------
async def summary_agent(kernel, drivers):
    return "Summary agent deprecated in V2"

async def slides_agent(kernel, drivers, frameworks, options, redteam, execution):
    return "Slides agent deprecated in V2"

def run_vanguard_pipeline(inputs):
    pass

# ------------------------------------------------------------
# AGENT: MARKET MAPPING
# ------------------------------------------------------------
async def market_mapping_agent(industry: str, geo_scope: str, segments: str):
    """
    Analyzes market structure, competitive landscape, customer segments,
    value chain dynamics, and market attractiveness.
    """
    prompt = f"""You are a Tier-1 Strategy Consultant (McKinsey/BCG caliber).
Perform a comprehensive structural market analysis for the {industry} industry.

INDUSTRY: {industry}
GEOGRAPHIC SCOPE: {geo_scope or "Global"}
TARGET SEGMENTS: {segments or "All major segments"}

CRITICAL: Analyze this SPECIFIC industry. DO NOT use generic placeholder values. 
Each score must reflect the actual market dynamics of {industry}.

Return ONLY valid JSON with this exact structure:
{{
    "competitors": [
        {{
            "name": "Actual company name",
            "archetype": "Leader|Challenger|Niche|Disruptor",
            "positioning": "Their unique market stance",
            "strengths": ["Specific strength 1", "Specific strength 2", "Specific strength 3"],
            "weaknesses": ["Specific weakness 1", "Specific weakness 2"],
            "market_share": <realistic integer 0-100 based on actual market position>,
            "differentiation": <integer 0-100 reflecting their uniqueness>
        }}
    ],
    "segments": [
        {{
            "label": "Segment name",
            "needs": "What this segment actually needs",
            "pain_points": "Real frustrations this segment faces",
            "attractiveness": <integer 0-100 based on profit potential>,
            "growth": "High|Medium|Low"
        }}
    ],
    "value_chain": [
        {{
            "stage": "R&D|Manufacturing|Distribution|etc",
            "description": "What actually happens at this stage in {industry}",
            "friction": "Real bottlenecks in {industry}",
            "opportunities": "Actual value creation opportunities"
        }}
    ],
    "threats": [
        {{
            "type": "New Entrants|Substitutes|Tech Shift|Regulation|Supply Chain",
            "description": "Specific threat facing {industry}",
            "severity": "High|Medium|Low"
        }}
    ],
    "scorecard": {{
        "profit_pool_growth": <0-100: How fast is profit growing in {industry}?>,
        "competitive_intensity": <0-100: How fierce is competition in {industry}? (high number = very competitive)>,
        "switching_costs": <0-100: How hard to switch providers in {industry}? (high = sticky)>,
        "barriers_to_entry": <0-100: How hard to enter {industry}? (high = protected)>,
        "supplier_power": <0-100: How much leverage do suppliers have in {industry}?>,
        "buyer_power": <0-100: How much leverage do buyers have in {industry}?>,
        "overall_attractiveness": <0-100: Average of factors, weighted by importance>
    }}
}}

SCORING GUIDANCE:
- HIGH profit_pool_growth (70-100): Rapidly expanding markets (e.g., AI, EVs)
- MEDIUM (40-69): Steady growth (e.g., SaaS, cloud)
- LOW (0-39): Mature/declining (e.g., traditional retail)

- HIGH competitive_intensity (70-100): Cutthroat (e.g., smartphones, retail)
- LOW (0-39): Few players, comfortable margins

- HIGH barriers_to_entry (70-100): Capital intensive, regulated (e.g., aerospace, pharma)
- LOW (0-39): Easy to start (e.g., apps, consulting)

Provide 4-6 real competitors, 3-5 distinct segments, 4-6 value chain stages, and 3-5 actual threats.
All scores must be INDUSTRY-SPECIFIC and REALISTIC."""

    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a strategic market analyst. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2000,
                temperature=0.7
            ),
            timeout=50.0
        )
        
        content = response.choices[0].message.content.strip()
        content = content.replace("```json", "").replace("```", "").strip()
        
        # Validate JSON
        parsed = json.loads(content)
        return content
        
    except Exception as e:
        print(f"ERROR in market_mapping_agent: {e}")
        return json.dumps({
            "error": str(e),
            "competitors": [],
            "segments": [],
            "value_chain": [],
            "threats": [],
            "scorecard": {
                "profit_pool_growth": 0,
                "competitive_intensity": 0,
                "switching_costs": 0,
                "barriers_to_entry": 0,
                "supplier_power": 0,
                "buyer_power": 0,
                "overall_attractiveness": 0
            }
        })
