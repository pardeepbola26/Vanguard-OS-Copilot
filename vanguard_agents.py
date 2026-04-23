# ============================================================
# Vanguard OS v2 - Multi-Agent Strategy Engine
# Premium Consulting Edition (Async/Parallel)
# ============================================================

import json
import os
import asyncio
from typing import Dict, Any, List, Optional, Literal
from enum import Enum
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Live search disabled by user request
SEARCH_AVAILABLE = False

# Load API key from .env
load_dotenv()
aclient = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ============================================================
# v2 SHARED INFRASTRUCTURE — Vanguard OS architectural anchor
# Enright (SPACE) + Porter (DYNAMICS) + Rumelt (EXECUTION)
# ============================================================

class EnrightLevel(str, Enum):
    SUPRANATIONAL = "supranational"
    NATIONAL = "national"
    CLUSTER = "cluster"
    INDUSTRY = "industry"
    FIRM = "firm"


# Typed shapes used inside agent outputs (JSON-compatible via dicts — we don't
# enforce pydantic at runtime since OpenAI returns dicts, but the schemas
# below are the contract prompts reference.)

# Consideration — something worth the operator's reflection that isn't a
# recommendation or a risk. Shape:
#   { "consideration": str, "why_it_matters": str, "what_would_resolve_it": Optional[str] }

# LoadBearingAssumption — must be true for strategy to work. Shape:
#   { "assumption": str, "confidence": "high|medium|low", "if_wrong": str, "how_to_test": str }

# TemporalSignal — when we'll know / when it's irreversible. Shape:
#   { "claim": str, "time_to_signal": str, "time_to_irreversibility": Optional[str],
#     "closing_window": Optional[str], "compounding_dynamic": Optional[str], "source_agent": str }


UNIVERSAL_AGENT_PREAMBLE = """
<role_anchor>
You are an agent within Vanguard OS, a strategic reasoning system built on the synthesis of three intellectual traditions:
- ENRIGHT's five-level framework (Supranational, National, Cluster, Industry, Firm) defines the strategic SPACE — where we're thinking.
- PORTER's frameworks (Five Forces, Generic Strategies, Value Chain) characterize the competitive DYNAMICS — what forces are in play.
- RUMELT's kernel (Diagnosis, Guiding Policy, Coherent Action) structures the EXECUTION — what we do about it.

Every agent has a specific role within this architecture. Know yours.

Your voice is that of a senior McKinsey partner who has done real operating work — sharp, intellectually honest, willing to make calls, able to say "I don't know" when genuinely uncertain, and allergic to consulting clichés. You have read Good Strategy / Bad Strategy three times. You understand that most strategy documents fail because they confuse goals with strategies, or restate constraints as choices.
</role_anchor>

<operating_principles>
1. SHARP BEATS COMPLETE. One non-obvious insight that changes how the operator thinks beats four framework applications that confirm what they already knew.
2. FALSIFIABILITY IS INTELLECTUAL HONESTY. Every claim must be accompanied by what would prove it wrong. Tentative language is allowed when reality is genuinely uncertain — but name WHAT makes it uncertain and WHAT would resolve it. Blanket hedging ("it may be worth exploring") is banned.
3. NAME THE CRUX. Rumelt's test: can you state the single most important obstacle in one sentence? If you can't, you haven't diagnosed yet — you're still describing symptoms.
4. CLIMB THE LEVELS. When analyzing competitive dynamics, consciously locate them at Enright's correct level. "Industry consolidation" at the industry level is different from "cluster erosion" at the cluster level. Specificity of altitude matters.
5. TEMPORAL REASONING IS FIRST-CLASS. Every recommendation requires a clock. When will we know? When is this irreversible? Is there a closing window?
6. IF YOU DISAGREE WITH UPSTREAM AGENTS, SAY SO. Write to the concerns field. Do not silently deviate.
7. EARN YOUR OUTPUT. Before submitting, ask: would a McKinsey director let this out the door? If the answer is "it's fine" — revise. If the answer is "this sharpens the thinking" — submit.
</operating_principles>
"""


# Forbidden phrases — the Synthesizer enforces these strictly, but they're
# banned across the whole system. Keep banned patterns narrow to avoid
# false-positive filtering.
FORBIDDEN_PHRASES = [
    "it may be worth considering",
    "a variety of factors",
    "leverage synergies",
    "optimize operational efficiencies",
    "in today's competitive landscape",
    "going forward",
]


# ------------------------------------------------------------
# Helper
# ------------------------------------------------------------
def normalize_markdown(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return text.replace("###", "##").replace("######", "###").strip()


async def _llm_json(system: str, user: str, *, model: str = "gpt-4o",
                    max_tokens: int = 2400, temperature: float = 0.4,
                    timeout: float = 60.0) -> dict:
    """
    Call OpenAI with response_format=json_object, parse, and return a dict.
    On parse failure, returns {"error": "...", "raw": "..."} so the caller can
    degrade gracefully without hanging.
    """
    try:
        response = await asyncio.wait_for(
            aclient.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system + "\n\nReturn ONLY a JSON object. No prose outside the JSON."},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                response_format={"type": "json_object"},
            ),
            timeout=timeout,
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except asyncio.TimeoutError:
        return {"error": "timeout", "raw": ""}
    except json.JSONDecodeError as je:
        return {"error": f"json parse: {je}", "raw": content if "content" in locals() else ""}
    except Exception as e:
        return {"error": str(e), "raw": ""}


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
DIAGNOSTICIAN_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Diagnostician. You own the Crux — the most important single sentence in the entire memo.

Rumelt's insight: most strategies fail because they skip diagnosis. Executives jump from "here's the problem" (usually a symptom) to "here's the plan" without ever identifying the actual obstacle. Your job is to NOT do that. You separate:
- What the operator thinks the problem is (presenting_problem)
- What the problem actually is (structural_cause)
- What the operator is likely underweighting (non_obvious_contributor)
- Why it's acute now (why_now)
- What the real external threat is (primary_external_threat)
</your_role>
"""


def _diagnostician_prompt_v1(situation: str, goal: str, constraints: str, document_context=None) -> str:
    # Retained for streaming variant backwards compat — returns prose markdown only.
    return _diagnostician_prompt(situation, goal, constraints, document_context)


def _diagnostician_prompt(situation: str, goal: str, constraints: str, document_context=None,
                          key_numbers: str = "", success_metrics: str = "") -> str:
    prompt = f"""<your_task>
Produce a diagnosis. Your output is binding on every downstream agent.

Before you write:
1. Read the situation three times.
2. Ask: what would a senior McKinsey partner say is genuinely non-obvious here?
3. Ask: what does the operator think is the problem, and what evidence in the situation suggests they're wrong?
4. Ask: why is this acute NOW? If the problem has existed for 18 months, what changed in the last 90 days?

The structural_cause field is the hardest to get right. Test: if your structural_cause could apply to any firm in this industry, it's wrong. Rewrite until it names something specific to THIS firm's position.

The why_now field forces temporal reasoning. "CAC has been climbing for 2 years but the board meeting is next month because Series B investors want to see Q1 CAC payback below 6 months" is a why_now. "CAC is high" is not.

The problem_type classification is a forcing function. Be willing to commit.

The crux_sentence is the sentence the operator will repeat in their next board meeting. A strong crux_sentence:
- Names the real obstacle in under 25 words
- Is falsifiable (you could imagine being wrong)
- Is specific to this situation
- Points toward a type of solution without prescribing it
</your_task>

<specificity_requirements>
Every field must name CONCRETE ANCHORS. Bland diagnosis is rejected.

crux_sentence, structural_cause, non_obvious_contributor, why_now, primary_external_threat
→ Each must reference at least ONE of:
  - Specific regulation / policy / event with date
  - Specific company, product, or competitor (named)
  - Specific number ($, %, headcount, market share, growth rate)
  - Specific technology shift (named product launch, protocol change)

Examples:
❌ "The firm needs to improve its competitive position."
✅ "The firm's 2019-2022 growth engine — Meta paid acquisition at $22 CAC — structurally broke when iOS 14.5 (April 2021) ended deterministic attribution. The firm is still optimizing the broken engine instead of building a community-led replacement that Glossier proved works in this category."

❌ "Competition is increasing."
✅ "The 2.1M-follower TikTok Shop dupe of the firm's hero SKU (launched Sept 2024) is doing $800K-1.2M/mo at 40% below the firm's price point, directly cannibalizing net-new acquisition while the firm's paid CAC has doubled from $22 to $47 over the last 12 months."

❌ "The situation is acute now because of market pressures."
✅ "Acute now because Q1 2025 board meeting is 9 weeks out, Series C marker is $200M post-money, and the last 3 months show paid-channel CAC recovery stalled (Meta variance widening, not closing)."
</specificity_requirements>

<voice_guidance>
Avoid:
- "It may be worth considering..." (commit or don't)
- "A variety of factors contribute..." (name them)
- "The company should focus on..." (you're the Diagnostician, not the recommender)

Use:
- "The operator reads this as X, but the evidence suggests Y, because Z [specific evidence/number/event]."
- "This is acute now because [specific recent change with date]."
- "The non-obvious contributor is [specific named factor] — most operators in this position underweight it because [specific reason]."
</voice_guidance>

<input_context>
Situation: {situation}
Goal: {goal}
Key Numbers: {key_numbers}
Constraints: {constraints}
Success Metrics: {success_metrics}
</input_context>
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:5000]}\n</document_context>\n"

    prompt += """
Return JSON matching this schema (all fields required unless marked optional):
{
  "crux_sentence": "string, MAX 25 words. The one sentence the CEO will repeat.",
  "presenting_problem": "string — what the operator currently thinks the problem is",
  "structural_cause": "string — the underlying regime-change that makes this not a cyclical blip. SPECIFIC to this firm, not the industry.",
  "non_obvious_contributor": "string — something the operator is underweighting",
  "why_now": "string — why this is acute at THIS moment. Name the specific recent change.",
  "primary_external_threat": "string — the single most dangerous external force",
  "problem_type": "execution|strategy|positioning|capability|temporal|composite",
  "dominant_if_composite": "string or null",
  "load_bearing_assumptions": [
    {"assumption": "string", "confidence": "high|medium|low", "if_wrong": "string", "how_to_test": "string"}
  ],
  "memo_contribution": "string — the exact 2-3 sentences the Synthesizer will use for the Crux section of the memo. Must open with the crux_sentence, then give structural_cause + why_now in one or two sentences.",
  "considerations": [{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}],
  "concerns": ["string"]
}
"""
    return prompt


async def diagnostician_agent(situation: str, goal: str, constraints: str,
                              document_context=None, key_numbers: str = "",
                              success_metrics: str = "") -> dict:
    """v2 Diagnostician — structured output with crux_sentence, structural_cause,
    non_obvious_contributor, why_now, primary_external_threat, problem_type,
    load_bearing_assumptions, memo_contribution. Returns a dict.
    """
    prompt = _diagnostician_prompt(situation, goal, constraints, document_context,
                                    key_numbers, success_metrics)
    data = await _llm_json(DIAGNOSTICIAN_SYSTEM, prompt, max_tokens=2000,
                           temperature=0.3, timeout=60.0)

    if data.get("error"):
        # Degrade gracefully — surface the error in memo_contribution
        return {
            "error": data.get("error"),
            "crux_sentence": "Diagnosis unavailable — agent error.",
            "memo_contribution": f"Diagnostician error: {data.get('error')}",
            "markdown": f"## Diagnosis Error\n\n{data.get('error')}",
            "problem_type": "composite",
            "load_bearing_assumptions": [],
            "considerations": [],
            "concerns": [f"Diagnostician failed: {data.get('error')}"],
        }

    # Render markdown view for legacy consumers + UI fallback rendering
    md_parts = []
    if data.get("crux_sentence"):
        md_parts.append(f"## Crux\n\n**{data['crux_sentence']}**")
    if data.get("structural_cause"):
        md_parts.append(f"## Structural Cause\n\n{data['structural_cause']}")
    if data.get("presenting_problem"):
        md_parts.append(f"## Presenting Problem\n\n{data['presenting_problem']}")
    if data.get("non_obvious_contributor"):
        md_parts.append(f"## Non-obvious Contributor\n\n{data['non_obvious_contributor']}")
    if data.get("why_now"):
        md_parts.append(f"## Why Now\n\n{data['why_now']}")
    if data.get("primary_external_threat"):
        md_parts.append(f"## Primary External Threat\n\n{data['primary_external_threat']}")
    if data.get("problem_type"):
        pt = data["problem_type"]
        if data.get("dominant_if_composite"):
            pt += f" (dominant: {data['dominant_if_composite']})"
        md_parts.append(f"## Problem Type\n\n`{pt}`")
    if data.get("load_bearing_assumptions"):
        md_parts.append("## Load-Bearing Assumptions")
        for lba in data["load_bearing_assumptions"]:
            conf = lba.get("confidence", "?")
            md_parts.append(
                f"- **{lba.get('assumption', '')}** "
                f"_(confidence: {conf})_  \n"
                f"  If wrong: {lba.get('if_wrong', '')}  \n"
                f"  How to test: {lba.get('how_to_test', '')}"
            )

    data["markdown"] = normalize_markdown("\n\n".join(md_parts))
    return data

# ------------------------------------------------------------
# AGENT 1.4: ENRIGHT AGENT (NEW — owns strategic SPACE / altitude)
# ------------------------------------------------------------
ENRIGHT_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Enright Agent. Your job is to locate the strategic problem at the correct altitude.

Most strategy work fails by analyzing at the wrong level. A firm-level margin problem might actually be a cluster-level commoditization. An industry-level competitive threat might be a supranational-level regulatory shift disguised as competition. You climb the five levels consciously and name where the real action is.

The five levels, from highest to lowest altitude:
SUPRANATIONAL: Forces above the nation-state. Global capital flows, trade blocs, technology diffusion, climate/demographic megatrends, geopolitical realignment.
NATIONAL: Country-specific dynamics. Regulatory regimes, macro policy, domestic competitive landscape, national talent pools, currency, political stability.
CLUSTER: Geographic or sector-adjacent concentrations that share inputs, labor, knowledge spillovers. A cluster has its own gravity independent of the industry.
INDUSTRY: The industry structure in Porter's sense. Five Forces, strategic groups, industry life cycle, entry barriers.
FIRM: Company-specific. Capabilities, positioning, resources, leadership, culture, operational state.
</your_role>
"""


async def enright_agent(situation: str, goal: str, constraints: str = "",
                         key_numbers: str = "", success_metrics: str = "",
                         document_context=None) -> dict:
    """Enright altitude analysis. Runs in parallel with Frameworks + Structure + Market Forces.
    Its dominant_level and memo_contribution feed Portfolio and Synthesizer.
    """
    prompt = f"""<your_task>
Analyze the situation across all five Enright levels with MCKINSEY-LEVEL SPECIFICITY.

For each level:
1. Mark its relevance (primary/secondary/contextual/not_applicable)
2. Name 2-4 SPECIFIC dynamics operating at that level. Each key_dynamic MUST name at least ONE of these concrete anchors:
   - Specific regulation or policy (name the law, effective date, key provision — NOT "regulatory environment")
   - Specific company or competitor (name the firm and the action — NOT "competitors")
   - Specific event with date/year (NOT "recent trends")
   - Specific number (market size, growth rate, %, $ figure, subscriber count)
   - Specific technology shift (name the product launch, protocol change, platform — NOT "digital transformation")
3. State the strategic implication — concrete, tied to this firm, not abstract
4. If there's something non-obvious at this level, name it

Then:
- Identify the DOMINANT LEVEL — where the real strategic action is
- Name 2-3 LEVEL INTERACTIONS — how dynamics cascade between levels. Each interaction must NAME A MECHANISM, not just say "X affects Y."
- Produce ONE ALTITUDE INSIGHT — the sharpest thing climbing the levels revealed. Must name a specific mechanism, firm, regulation, or number.
</your_task>

<examples_of_good_vs_bad>
❌ BAD supranational: "Global trends affecting the industry."
✅ GOOD supranational: "2023-2024 CHIPS Act realigning semiconductor supply from Taiwan to Arizona/Ohio — adds 2-3 year lead time to fab capacity for firms not already in the allocation queue, and locks out firms without US-based engineering teams from Tier-1 foundry access."

❌ BAD national: "Regulatory environment is challenging."
✅ GOOD national: "FTC non-compete ban (blocked Aug 2024, expected to return under 2025 rulemaking) resets talent mobility — specifically unlocks the ~40% of senior ML engineers currently under non-competes at FAANG firms."

❌ BAD cluster: "Regional competition is intensifying."
✅ GOOD cluster: "Austin climate-tech cluster hit critical mass in 2024 — 4 YC climate companies raised Series A within 30 miles, pulling senior engineering talent away from traditional SaaS (+$40K base uplift), making the firm's compensation band 15-20% below market."

❌ BAD industry: "Porter's forces are pressuring margins."
✅ GOOD industry: "OpenAI's $200/mo Pro tier launch (Dec 2024) repriced pro-sumer AI downward ~60% overnight, compressing the firm's $150/mo enterprise tier into direct substitution with a product from a $157B competitor."

❌ BAD firm: "Firm lacks capabilities."
✅ GOOD firm: "Firm's 2023 B2C→B2B pivot left it with an 11-person B2C marketing org and a 2-person enterprise sales team — the inverse of what $50M ARR requires (industry benchmark: 1 AE per $1M new ARR)."

❌ BAD altitude_insight: "Multiple factors converge to create strategic risk."
✅ GOOD altitude_insight: "The firm reads this as a CAC problem (firm level), but the underlying dynamic is cluster-level: premium beauty DTC is losing its LA/NY cluster advantage as TikTok Shop redistributes discovery to a platform-level cluster. Firms that solve CAC without relocating their cluster positioning will keep bleeding in 2-3 years even if Meta economics temporarily recover."
</examples_of_good_vs_bad>

<output_requirements>
- EVERY key_dynamic must name at least one concrete anchor (regulation/company/date/number/tech shift). Abstract dynamics are rejected.
- Mark at LEAST one level "not_applicable" if it genuinely is.
- The dominant_level is usually NOT firm. If you land on firm, audit yourself — did you climb hard enough?
- If the input context is thin, use the MOST LIKELY real-world references for the named industry — and flag the assumption in concerns: "Used industry-standard references for [X]; confirm applicability to this specific firm."
</output_requirements>

<input_context>
Situation: {situation}
Goal: {goal}
Key Numbers: {key_numbers}
Constraints: {constraints}
Success Metrics: {success_metrics}
</input_context>
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:5000]}\n</document_context>\n"

    prompt += """
Return JSON matching this schema:
{
  "levels": [
    {
      "level": "supranational|national|cluster|industry|firm",
      "relevance": "primary|secondary|contextual|not_applicable",
      "key_dynamics": ["string", "string"],
      "strategic_implication": "string",
      "non_obvious_insight": "string or null"
    }
    // ALL 5 levels, in order
  ],
  "dominant_level": "supranational|national|cluster|industry|firm",
  "level_interactions": ["string describing how dynamics cascade between levels", "string", "string"],
  "altitude_insight": "string — the sharpest single claim from climbing the levels. Must be specific.",
  "memo_contribution": "string — 3-4 sentences the Synthesizer will use for the Strategic Framing section.",
  "considerations": [{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}],
  "concerns": ["string"]
}
"""

    data = await _llm_json(ENRIGHT_SYSTEM, prompt, max_tokens=2400,
                           temperature=0.4, timeout=60.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "dominant_level": "industry",
            "altitude_insight": "Enright analysis unavailable.",
            "memo_contribution": f"Enright agent error: {data['error']}",
            "markdown": f"## Enright Error\n\n{data['error']}",
            "levels": [],
            "level_interactions": [],
            "considerations": [],
            "concerns": [f"Enright failed: {data['error']}"],
        }

    # Markdown render for legacy consumers + fallback UI
    md = []
    level_labels = {
        "supranational": "Supranational (global forces)",
        "national": "National (country dynamics)",
        "cluster": "Cluster (regional/sector concentration)",
        "industry": "Industry (Porter structure)",
        "firm": "Firm (capabilities/positioning)",
    }
    relevance_mark = {
        "primary": "🔴 PRIMARY",
        "secondary": "🟠 secondary",
        "contextual": "· contextual",
        "not_applicable": "—",
    }

    if data.get("dominant_level"):
        md.append(f"**Dominant level:** `{data['dominant_level'].upper()}`")
    if data.get("altitude_insight"):
        md.append(f"\n## Altitude Insight\n\n{data['altitude_insight']}")
    if data.get("levels"):
        md.append("\n## Level-by-Level")
        for lvl in data["levels"]:
            label = level_labels.get(lvl.get("level"), lvl.get("level", "?"))
            mark = relevance_mark.get(lvl.get("relevance", ""), lvl.get("relevance", ""))
            md.append(f"\n### {label}  \n_{mark}_")
            for dyn in (lvl.get("key_dynamics") or []):
                md.append(f"- {dyn}")
            if lvl.get("strategic_implication"):
                md.append(f"\n**Implication:** {lvl['strategic_implication']}")
            if lvl.get("non_obvious_insight"):
                md.append(f"\n**Non-obvious:** {lvl['non_obvious_insight']}")
    if data.get("level_interactions"):
        md.append("\n## Cascading Interactions")
        for inter in data["level_interactions"]:
            md.append(f"- {inter}")

    data["markdown"] = normalize_markdown("\n".join(md))
    return data


# ------------------------------------------------------------
# AGENT 1.5: FRAMEWORK AGENT (Meta-level selector per v2 spec)
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

    system = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Frameworks Agent. Your job is to select the 2-3 analytical lenses that genuinely illuminate THIS situation, and explain what each reveals.

Most strategy work fails by applying frameworks as templates rather than tools. A framework applied as a template produces checkbox analysis. A framework applied as a tool produces insight.

Your role is the tool-user, not the template-applier. Enright's 5-level framework is owned by the Enright Agent — reference it but don't duplicate.

Toolkit you can draw from:
- Porter: Five Forces, Generic Strategies, Value Chain, Diamond Model
- Rumelt: Kernel, Bad Strategy patterns
- Christensen: Jobs-to-be-Done, Disruption Theory, Capabilities/Resources/Priorities
- Wardley: Value-chain mapping, evolution axis
- Kim & Mauborgne: Blue Ocean, Strategy Canvas
- McKinsey: 7S, Three Horizons
- BCG: Growth-Share Matrix, Experience Curve
- Hamel & Prahalad: Core Competencies
- Teece: Dynamic Capabilities
- Barney: Resource-Based View / VRIN
- Kahneman/Tversky: Cognitive biases relevant to strategic decisions
- Taleb: Antifragility, barbell strategy, optionality
</your_role>
"""

    prompt = f"""<your_task>
Select EXACTLY 2-3 frameworks. Not 1. Not 5. The discipline is forcing yourself to pick the few that matter most.

For each selected framework:
1. Name the framework and its category
2. Argue WHY this framework for THIS situation (not generically — specifically)
3. Apply the framework to the situation with specificity
4. Extract the SHARPEST INSIGHT — the thing the framework reveals that a thoughtful generalist would miss
5. Acknowledge WHAT IT MISSES — every framework has blind spots; name them here
6. Locate it at an Enright level

Also produce:
- At least 2 frameworks you REJECTED with brief reasons (forces deliberate selection)
- Optionally: a cross_framework_insight — something that emerges only when you combine the selected lenses

Before finalizing, ask: could my sharpest_insight sections appear in a generic consulting deck? If yes, I haven't applied the framework hard enough.
</your_task>

<input_context>
Situation: {situation}
Goal: {goal}
{user_hint}
</input_context>
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:6000]}\n</document_context>\n"

    prompt += """
Return JSON matching this schema:
{
  "selected_frameworks": [
    {
      "name": "string (e.g., 'Christensen Jobs-to-be-Done')",
      "category": "competitive|positioning|operational|cognitive|temporal",
      "why_this_framework": "string — why THIS framework for THIS situation",
      "application": "string — how the framework maps. Specific, not textbook.",
      "sharpest_insight": "string — what this framework reveals that wasn't obvious",
      "what_it_misses": "string — honest acknowledgment of where this framework fails here",
      "enright_level": "supranational|national|cluster|industry|firm"
    }
    // 2-3 entries
  ],
  "frameworks_considered_and_rejected": [
    {"name": "string", "why_rejected": "string under 40 words"}
    // at least 2
  ],
  "cross_framework_insight": "string or null — insight that only emerges from combining the selected frameworks",
  "memo_contribution": "string — 2-3 sentences the Synthesizer can use",
  "considerations": [{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}],
  "concerns": ["string"]
}
"""

    data = await _llm_json(system, prompt, max_tokens=2800, temperature=0.4, timeout=60.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "selected_frameworks": [],
            "frameworks_considered_and_rejected": [],
            "memo_contribution": f"Frameworks agent error: {data['error']}",
            "markdown": f"## Frameworks Error\n\n{data['error']}",
            "considerations": [],
            "concerns": [f"Frameworks failed: {data['error']}"],
        }

    md = []
    for fw in (data.get("selected_frameworks") or []):
        md.append(f"## {fw.get('name', 'Framework')}")
        if fw.get("why_this_framework"):
            md.append(f"_Why this framework:_ {fw['why_this_framework']}")
        if fw.get("application"):
            md.append(fw["application"])
        if fw.get("sharpest_insight"):
            md.append(f"\n**Sharpest insight:** {fw['sharpest_insight']}")
        if fw.get("what_it_misses"):
            md.append(f"\n**What it misses:** {fw['what_it_misses']}")
        md.append("")
    rejected = data.get("frameworks_considered_and_rejected") or []
    if rejected:
        md.append("## Considered and Rejected")
        for r in rejected:
            md.append(f"- **{r.get('name', '?')}** — {r.get('why_rejected', '')}")
    if data.get("cross_framework_insight"):
        md.append(f"\n## Cross-Framework Insight\n\n{data['cross_framework_insight']}")
    data["markdown"] = normalize_markdown("\n".join(md))
    return data

# ------------------------------------------------------------
# AGENT 1.8: STRUCTURE AGENT (v2 — Systems analyst + decision tree)
# ------------------------------------------------------------
STRUCTURE_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Structure Agent. You see the problem as a SYSTEM — dependencies, feedback loops, bottlenecks, leverage points.

Most strategic analysis treats a firm's situation as a list of issues. You treat it as a causal graph. You identify:
1. What KIND of problem this is (most operators misdiagnose)
2. The critical DEPENDENCY CHAIN (what must happen in what order)
3. The FEEDBACK LOOPS currently shaping the firm's trajectory
4. The STRUCTURAL BOTTLENECK — the one constraint that, if removed, changes everything
5. Any HIDDEN LEVERAGE — points in the system where small changes produce large effects

You are channeling Donella Meadows meets Eli Goldratt. You think in stocks and flows, in reinforcing and balancing loops, in constraints and throughput.

Problem type definitions:
EXECUTION: Strategy is right for environment, firm is failing to deliver. Fix: better operations.
STRATEGY: Strategy itself is wrong for environment. Fix: re-strategize. Execution won't save you.
POSITIONING: Firm's market position has structurally shifted. Fix: reposition.
CAPABILITY: Firm lacks a capability required for ANY viable strategy. Fix: build/buy/partner.
TEMPORAL: Moves are right but timing is wrong. Fix: sequencing.
IDENTITY: Firm has lost coherence about what it is. Fix: forced clarity.
COMPOSITE: Multiple of the above. If composite, name the DOMINANT one.
</your_role>
"""


async def structure_agent(situation: str, goal: str, constraints: str,
                           document_context=None, key_numbers: str = "") -> dict:
    prompt = f"""<your_task>
Classify the problem type and justify with specific evidence. Then trace the system.

CRITICAL DEPENDENCY CHAIN: What must happen in what order for ANY strategy to succeed here?
FEEDBACK LOOPS: Identify 2-4 loops currently shaping the firm's trajectory. For each: reinforcing or balancing? accelerating / stable / weakening / broken? what would break it?
STRUCTURAL BOTTLENECK: Goldratt's Theory of Constraints — name the one binding constraint.
HIDDEN LEVERAGE: Meadows' leverage points — places in the system where small changes produce large effects. Often counterintuitive.

Also generate a 4-level DECISION TREE as a Cytoscape.js graph: Goal → 3-4 Drivers → 2-3 Sub-drivers each → Actions (leaves). Used by the frontend Decision Tree tab. Nodes include type (root/branch/leaf), roi (0-10 for leaves), confidence (0-100). Edges include weight (1-10 impact).
</your_task>

<input_context>
Situation: {situation}
Goal: {goal}
Key Numbers: {key_numbers}
Constraints: {constraints}
</input_context>

Return JSON matching this schema:
{{
  "problem_type": "execution_problem|strategy_problem|positioning_problem|capability_problem|temporal_problem|identity_problem|composite",
  "dominant_if_composite": "string or null",
  "problem_type_evidence": "string — specific evidence for the classification",
  "critical_dependency_chain": ["string ordered step", "string ordered step"],
  "feedback_loops": [
    {{
      "loop_description": "string",
      "loop_type": "reinforcing|balancing",
      "current_state": "accelerating|stable|weakening|broken",
      "break_point": "string or null",
      "strategic_implication": "string"
    }}
  ],
  "structural_bottleneck": "string",
  "hidden_leverage": "string or null",
  "tree": {{
    "nodes": [
      {{"data": {{"id": "goal", "label": "short label", "type": "root", "confidence": 80}}}}
    ],
    "edges": [
      {{"data": {{"source": "goal", "target": "d1", "weight": 8}}}}
    ]
  }},
  "memo_contribution": "string — 2-3 sentences the Synthesizer can use",
  "considerations": [{{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}}],
  "concerns": ["string"]
}}
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:5000]}\n</document_context>\n"

    data = await _llm_json(STRUCTURE_SYSTEM, prompt, max_tokens=3200,
                           temperature=0.3, timeout=75.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "problem_type": "composite",
            "tree": {"nodes": [{"data": {"id": "error", "label": "Structure error", "type": "root"}}], "edges": []},
            "memo_contribution": f"Structure agent error: {data['error']}",
            "markdown": f"## Structure Error\n\n{data['error']}",
            "feedback_loops": [],
            "considerations": [],
            "concerns": [f"Structure failed: {data['error']}"],
        }

    # Legacy frontend compatibility: expose tree at top level too, as JSON STRING
    # so renderCytoscapeDiagram (which accepts string or object) works.
    tree = data.get("tree") or {}
    if isinstance(tree, dict):
        data["nodes"] = tree.get("nodes", [])
        data["edges"] = tree.get("edges", [])

    md = []
    if data.get("problem_type"):
        pt = data["problem_type"]
        if data.get("dominant_if_composite"):
            pt += f" (dominant: {data['dominant_if_composite']})"
        md.append(f"**Problem type:** `{pt}`")
    if data.get("problem_type_evidence"):
        md.append(f"\n{data['problem_type_evidence']}")
    if data.get("structural_bottleneck"):
        md.append(f"\n## Structural Bottleneck\n\n{data['structural_bottleneck']}")
    if data.get("hidden_leverage"):
        md.append(f"\n## Hidden Leverage\n\n{data['hidden_leverage']}")
    if data.get("critical_dependency_chain"):
        md.append("\n## Critical Dependency Chain")
        for i, step in enumerate(data["critical_dependency_chain"], 1):
            md.append(f"{i}. {step}")
    if data.get("feedback_loops"):
        md.append("\n## Feedback Loops")
        for loop in data["feedback_loops"]:
            md.append(
                f"- **{loop.get('loop_description', '')}** "
                f"_{loop.get('loop_type', '?')}, {loop.get('current_state', '?')}_  \n"
                f"  Break point: {loop.get('break_point', 'n/a')}  \n"
                f"  Implication: {loop.get('strategic_implication', '')}"
            )
    data["markdown"] = normalize_markdown("\n".join(md))
    return data


# Legacy Cytoscape-only entrypoint preserved in case any caller needs it.
async def structure_agent_legacy(situation: str, goal: str, constraints: str):
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
MARKET_FORCES_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Market Forces Agent. You own Porter's competitive analysis.

Your role in the intellectual architecture: Enright tells us WHAT LEVEL we're thinking at; you characterize the DYNAMICS operating within the industry level (and sometimes cluster level). Porter's frameworks are tools — you use them, not recite them.

You analyze:
1. The Five Forces with intensity (1-5), trajectory (intensifying/stable/weakening), and time horizon
2. Which forces are MOST DETERMINATIVE for strategy (not all forces matter equally in every industry)
3. The firm's current generic-strategy posture (cost leader / differentiator / focuser / stuck in middle)
4. Strategic group structure and where the firm sits within it
5. Temporal dynamics — how will forces shift over the next 18-24 months?
6. Industry profit pool shifts — where is value migrating?
</your_role>
"""


async def market_forces_agent(crux_output: str, document_context=None,
                               enright_output: Optional[dict] = None,
                               situation: str = "") -> dict:
    """v2 Market Forces — Porter dynamics with temporal trajectory + strategic groups.
    Returns a dict with structured forces + memo_contribution + markdown.
    """
    enright_summary = ""
    if enright_output and isinstance(enright_output, dict) and not enright_output.get("error"):
        enright_summary = (
            f"Enright dominant level: {enright_output.get('dominant_level', '?')}. "
            f"Altitude insight: {enright_output.get('altitude_insight', '')}"
        )

    prompt = f"""<your_task>
Analyze all Five Forces with MCKINSEY-LEVEL SPECIFICITY. Every specific_evidence and implication must name CONCRETE ANCHORS — real companies, specific regulations, dates, numbers, named products, technology shifts. Abstract language is rejected.

For each force:
- Rate intensity 1-5 with specific evidence (NAME companies, prices, market shares, dates)
- Rate trajectory (intensifying/stable/weakening) and over what time horizon
- Name the implication for strategy (concrete action, not abstract "must improve positioning")

CRITICAL: Forces are not equal. Name the MOST_DETERMINATIVE and SECONDARY_DETERMINATIVE. Do not treat all five as equally important.

GENERIC STRATEGY: Where is this firm? Be willing to call "stuck_in_middle" when warranted.
STRATEGIC GROUPS: Map the competitive landscape. NAME the firms in each group. Where does the firm sit?
TEMPORAL DYNAMICS: How will forces shift over 18-24 months? Name SPECIFIC events, regulations, tech launches that will drive the shift.
PROFIT POOL SHIFT: Where is value migrating? NAME the winning/losing categories.
</your_task>

<examples_of_good_vs_bad>
❌ BAD evidence: "Competition is strong with many players in the market."
✅ GOOD evidence: "Rivalry is intense because 3 direct competitors (Gong at $7.25B valuation, Chorus.ai inside ZoomInfo, and Clari) offer overlapping conversation intelligence features at 30-40% lower prices for SMB segment, with Gong's Dec 2024 GenAI release further commoditizing transcription accuracy as a differentiator."

❌ BAD evidence: "Buyers have some power."
✅ GOOD evidence: "Buyer power is high (4/5) — top 5 customers account for 38% of ARR, one of them (a Fortune 500 media company) renegotiated 2025 pricing down 22% after evaluating Cresta and two internal AI pilots, and similar conversations are active with the #2 and #3 accounts."

❌ BAD implication: "The firm should adapt its strategy."
✅ GOOD implication: "With 4/5 substitute threat from GenAI-native entrants, the firm cannot defend its $150/user/mo SMB tier and must retreat to the $500+/user/mo mid-market tier where integration depth (Salesforce CPQ, Gainsight) still compounds — a 60% reduction in addressable SMB seats but protects 75% of current gross margin."

❌ BAD temporal: "Forces will continue to evolve."
✅ GOOD temporal: "Over 18 months: threat of substitutes escalates (GPT-5 expected Q2 2025, 10x context window collapses competitive moat around long-call analysis); buyer power rises as Q3 2025 renewals cycle through with new AI-budget rationalization; rivalry PEAKS then drops as 2-3 underfunded competitors (Wingman, Avoma) run out of runway by mid-2026. Most determinative shifts from rivalry → substitutes at month 12."

❌ BAD strategic_group: "A group of similar competitors."
✅ GOOD strategic_group: "group_name: 'Enterprise CI incumbents', members: ['Gong','Chorus/ZoomInfo','Clari'], axes: {price: '$500-1200/user/mo', integration_depth: 'deep SFDC/MSFT native', deployment: 'enterprise sales motion'}, firm_membership: 'caught_between_groups' — the firm is priced like enterprise ($180/user) but sells like SMB (self-serve with lightweight onboarding)."

❌ BAD profit_pool: "Value is moving in the industry."
✅ GOOD profit_pool: "Profit pool is migrating from 'conversation transcription' (commoditized by GenAI APIs at $0.006/minute) to 'revenue intelligence layered on top of CRM workflows' — Gong captured 60% of this via Salesforce partnership Oct 2024. Firms without native CRM integration by end of 2025 are locked out of where margin still exists."
</examples_of_good_vs_bad>

<input_context>
Situation: {situation}
Crux: {crux_output}
{enright_summary}
</input_context>
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:6000]}\n</document_context>\n"

    prompt += """
Return JSON matching this schema:
{
  "forces": [
    {
      "name": "threat_of_new_entrants|bargaining_power_of_suppliers|bargaining_power_of_buyers|threat_of_substitutes|rivalry_among_competitors",
      "intensity": 1,
      "intensity_trajectory": "intensifying|stable|weakening",
      "time_horizon": "string — over what period is this assessment valid",
      "specific_evidence": "string — evidence from THIS situation, not generic",
      "implication": "string"
    }
    // EXACTLY 5 forces
  ],
  "most_determinative": "string — name which force shapes strategy most",
  "secondary_determinative": "string",
  "generic_strategy_assessment": "cost_leadership|differentiation|focus_cost|focus_differentiation|stuck_in_middle|unclear",
  "stuck_in_middle_risk": "string — is the firm drifting toward stuck-in-middle? If yes, why?",
  "strategic_groups": [
    {
      "group_name": "string",
      "members": ["string"],
      "positioning_axes": {"axis_name": "axis_value"},
      "firm_membership": "in_group|adjacent|not_in_group|caught_between_groups"
    }
  ],
  "temporal_dynamics": "string — how forces will shift over 18-24 months",
  "industry_profit_pool_shift": "string or null — where is value migrating",
  "memo_contribution": "string — 2-3 sentences for the Synthesizer",
  "considerations": [{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}],
  "concerns": ["string"]
}
"""

    data = await _llm_json(MARKET_FORCES_SYSTEM, prompt, max_tokens=2800,
                           temperature=0.4, timeout=75.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "forces": [],
            "most_determinative": None,
            "memo_contribution": f"Market Forces error: {data['error']}",
            "markdown": f"## Market Forces Error\n\n{data['error']}",
            "considerations": [],
            "concerns": [f"Market Forces failed: {data['error']}"],
        }

    # Markdown render
    md = []
    arrow = {"intensifying": "↑", "weakening": "↓", "stable": "→"}
    for f in (data.get("forces") or []):
        name = f.get("name", "?").replace("_", " ").title()
        md.append(
            f"- **{name}** ({f.get('intensity', '?')}/5 "
            f"{arrow.get(f.get('intensity_trajectory',''), '·')}) — "
            f"{f.get('specific_evidence', '')}"
        )
    if data.get("most_determinative"):
        md.append(f"\n**Most determinative:** {data['most_determinative']}")
    if data.get("secondary_determinative"):
        md.append(f"**Secondary:** {data['secondary_determinative']}")
    if data.get("generic_strategy_assessment"):
        md.append(f"\n**Generic strategy posture:** `{data['generic_strategy_assessment']}`")
    if data.get("stuck_in_middle_risk"):
        md.append(f"\n{data['stuck_in_middle_risk']}")
    if data.get("temporal_dynamics"):
        md.append(f"\n## Temporal Dynamics (18-24 mo)\n\n{data['temporal_dynamics']}")
    if data.get("industry_profit_pool_shift"):
        md.append(f"\n## Profit Pool Shift\n\n{data['industry_profit_pool_shift']}")
    data["markdown"] = normalize_markdown("\n".join(md))
    return data

# ------------------------------------------------------------
# AGENT 3: FINANCIAL AGENT
# ------------------------------------------------------------
FINANCIAL_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Financial Agent. You translate strategy into numbers and stress-test whether the strategy can actually deliver on the goals the operator stated.

Your core discipline: YOU DO NOT SOFTEN THE OPERATOR'S STATED GOALS. If the base case doesn't meet them, you SAY SO. Loudly.

Most strategy financial work fails by:
1. Generating scenarios that conveniently match the stated goals (fake alignment)
2. Burying the gap between base case and stated goals in footnotes

You do neither.
</your_role>
"""


async def financial_agent(crux_output: str, drivers_output: str, document_context=None,
                          user_numbers: str = "", goal: str = "",
                          success_metrics: str = "",
                          portfolio_output: Optional[dict] = None) -> dict:
    """v2 Financial — scenario modeling with goal_alignment enforcement.
    Scenarios have probabilities summing to 1.0. Each stated goal gets its own
    goal_alignment object. goal_gap_flag surfaces loudly to the Synthesizer.
    """
    portfolio_rec = ""
    portfolio_thesis = ""
    portfolio_assumptions = ""
    if portfolio_output and isinstance(portfolio_output, dict):
        portfolio_rec = portfolio_output.get("primary_recommendation", "")
        for opt in (portfolio_output.get("options") or []):
            if opt.get("name") == portfolio_rec:
                portfolio_thesis = opt.get("core_thesis", "")
                lbas = opt.get("load_bearing_assumptions") or []
                portfolio_assumptions = "; ".join(
                    f"{a.get('assumption','?')} (confidence: {a.get('confidence','?')})" for a in lbas[:5]
                )
                break

    prompt = f"""<your_task>
Build three scenarios: base, bull, bear. Probabilities must sum to 1.0.
BASE: Median expectation if the recommended strategy is executed reasonably well. In turnarounds, base probability is typically 0.5-0.6, NOT 0.8.
BULL: Upside scenario.
BEAR: Downside scenario — must include probability of covenant breach / cash-out / down-round where applicable.

UNIT ECONOMICS: Current state vs required state (what needs to be true for goals to be met). If required is implausible given current, flag.

GOAL ALIGNMENT: For EACH stated goal/metric, compare base-case outcome to the goal. If gap exists, say so LOUDLY. Set goal_gap_flag True. Do not soften.

SENSITIVITY: Which 2-3 variables drive outcomes most?
CAPITAL REQUIREMENT: What capital does the strategy need? When? Source?
FINANCING IMPLICATIONS: Valuation / runway / covenant implications.

Model the RECOMMENDED strategy specifically. Not generic outcomes.
</your_task>

<input_context>
Crux: {crux_output}
Drivers: {drivers_output}
Goal: {goal}
Success Metrics: {success_metrics}
Recommended option (Portfolio): {portfolio_rec}
Option core thesis: {portfolio_thesis}
Portfolio load-bearing assumptions: {portfolio_assumptions}
</input_context>
"""
    if user_numbers and user_numbers.strip():
        prompt += (
            "\n<user_numbers_authoritative>\n"
            f"{user_numbers.strip()}\n"
            "These are given. Use them directly. Extrapolate only the missing pieces.\n"
            "</user_numbers_authoritative>\n"
        )

    if document_context:
        prompt += f"\n<document_context>\n{document_context[:7000]}\n</document_context>\n"

    prompt += """
Return JSON (values in millions unless noted):
{
  "scenarios": {
    "base": {
      "probability": 0.6,
      "initial_investment": 0,
      "cash_flows": [Y1, Y2, Y3, Y4, Y5],
      "discount_rate": 0.10,
      "revenue_y5": 0,
      "ebitda_y5": 0,
      "key_assumptions": ["string"],
      "break_conditions": ["string — what would invalidate this scenario"]
    },
    "bull": { ... },
    "bear": { ... }
  },
  "unit_economics": {
    "cac": 0,
    "ltv": 0,
    "gross_margin": 0.70,
    "churn_rate": 0.08,
    "arpu_monthly": 0,
    "current_state": {"metric": "value"},
    "required_state": {"metric": "value"},
    "gap_analysis": "string"
  },
  "goal_alignment": [
    {
      "stated_goal": "string (exact wording from user)",
      "base_case_outcome": "string",
      "gap_exists": true,
      "gap_description": "string or null",
      "what_would_close_gap": "string or null"
    }
  ],
  "goal_gap_flag": true,
  "goal_gap_explanation": "string or null",
  "capital_requirement": "string — how much, when, for what",
  "financing_implications": "string — covenants, valuation, runway",
  "sensitivity_analysis": {"top_drivers": ["string"], "details": "string"},
  "assumptions": "string — 1-2 sentences naming key assumptions",
  "narrative": "string — 2-3 paragraphs",
  "memo_contribution": "string — 2-3 sentences the Synthesizer will use, INCLUDING any goal gap if flagged",
  "considerations": [{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}],
  "concerns": ["string"]
}
"""

    data = await _llm_json(FINANCIAL_SYSTEM, prompt, max_tokens=3500,
                           temperature=0.3, timeout=90.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "scenarios": {},
            "unit_economics": {},
            "narrative": "",
            "assumptions": "",
            "goal_alignment": [],
            "goal_gap_flag": False,
            "memo_contribution": f"Financial error: {data['error']}",
            "markdown": f"## Financial Error\n\n{data['error']}",
            "considerations": [],
            "concerns": [f"Financial failed: {data['error']}"],
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
    try:
        cac = float(ue.get("cac") or 0)
        ltv = float(ue.get("ltv") or 0)
        gross_margin = float(ue.get("gross_margin") or 0)
        arpu_monthly = float(ue.get("arpu_monthly") or 0)
    except (TypeError, ValueError):
        cac = ltv = gross_margin = arpu_monthly = 0

    ue["ltv_cac_ratio"] = round(ltv / cac, 2) if cac > 0 else None
    if arpu_monthly > 0 and gross_margin > 0 and cac > 0:
        ue["payback_months"] = round(cac / (arpu_monthly * gross_margin), 1)
    else:
        ue["payback_months"] = None

    base = scenarios_out.get("base", {})
    cfs = base.get("cash_flows") or []
    try:
        rev5 = float(base.get("revenue_y5") or 0)
        ebitda5 = float(base.get("ebitda_y5") or 0)
    except (TypeError, ValueError):
        rev5 = ebitda5 = 0
    cagr = None
    if len(cfs) >= 5 and cfs[0] and cfs[0] > 0 and cfs[4] and cfs[4] > 0:
        try:
            cagr = (cfs[4] / cfs[0]) ** (1 / 4) - 1
        except Exception:
            cagr = None
    ebitda_margin = (ebitda5 / rev5) if rev5 > 0 else None
    rule_of_40 = None
    if cagr is not None and ebitda_margin is not None:
        rule_of_40 = round((cagr + ebitda_margin) * 100, 1)
    ue["cagr"] = round(cagr * 100, 1) if cagr is not None else None
    ue["ebitda_margin"] = round(ebitda_margin * 100, 1) if ebitda_margin is not None else None
    ue["rule_of_40"] = rule_of_40

    data["scenarios"] = scenarios_out
    data["unit_economics"] = ue

    # Markdown rendering — used by downstream agents and UI fallback
    narrative = data.get("narrative", "")
    assumptions = data.get("assumptions", "")
    gga = data.get("goal_alignment") or []
    base_metrics = scenarios_out.get("base", {}).get("metrics", {})

    md = [f"## Financial Analysis\n\n{narrative}"]
    md.append(
        f"\n**Base-case metrics:** NPV {base_metrics.get('npv_str', 'N/A')} · "
        f"IRR {base_metrics.get('irr_str', 'N/A')} · "
        f"Payback {base_metrics.get('payback_str', 'N/A')} · "
        f"ROI {base_metrics.get('roi_str', 'N/A')}"
    )
    if data.get("goal_gap_flag"):
        md.append("\n### ⚠ Goal Gap Flagged")
        md.append(data.get("goal_gap_explanation", ""))
        for g in gga:
            if g.get("gap_exists"):
                md.append(
                    f"- **{g.get('stated_goal','?')}**: {g.get('gap_description','')}  \n"
                    f"  _To close_: {g.get('what_would_close_gap','')}"
                )
    if data.get("capital_requirement"):
        md.append(f"\n**Capital requirement:** {data['capital_requirement']}")
    if data.get("financing_implications"):
        md.append(f"\n**Financing implications:** {data['financing_implications']}")
    if assumptions:
        md.append(f"\n**Key assumptions:** {assumptions}")
    data["markdown"] = normalize_markdown("\n".join(md))
    return data


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
OPS_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Ops Agent. You own Rumelt's COHERENT ACTION layer.

The Diagnostician named the Crux. The Portfolio Agent defined the Guiding Policy and selected an option. Your job: translate that into a sequenced plan where every action coherently supports the guiding policy.

You enforce coherence ruthlessly. You reject actions that don't serve the guiding policy, even if they're good ideas in general.
</your_role>
"""


async def ops_agent(crux_output: str, financial_output, document_context=None,
                    portfolio_output=None, tech_output=None, human_output=None,
                    diagnosis_output=None) -> dict:
    """v2 Ops — Rumelt coherent action. Executes the Portfolio's chosen option.
    Takes dict or string for upstream agent outputs for backward compat.
    """
    # Normalize all upstream to strings
    def _to_text(x):
        if x is None: return ""
        if isinstance(x, dict): return x.get("markdown") or x.get("memo_contribution") or ""
        return str(x)

    portfolio_text = _to_text(portfolio_output)
    financial_text = _to_text(financial_output)
    tech_text = _to_text(tech_output)
    human_text = _to_text(human_output)

    # Extract portfolio anchors if available as dict
    portfolio_rec = ""
    guiding_policy = ""
    downstream_instruction = ""
    kill_criteria = []
    if isinstance(portfolio_output, dict):
        portfolio_rec = portfolio_output.get("primary_recommendation", "")
        downstream_instruction = portfolio_output.get("downstream_instruction", "")
        for opt in (portfolio_output.get("options") or []):
            if opt.get("name") == portfolio_rec:
                guiding_policy = opt.get("guiding_policy", "")
                kill_criteria = opt.get("kill_criteria", []) or []
                break

    prompt = f"""<your_task>
The Portfolio Agent has chosen Option [{portfolio_rec or 'primary'}]. Execute ONLY that option. Do NOT include actions from non-selected options. If you disagree, write to concerns — do not silently add parallel tracks.

A 30/60/90 plan is a DEPENDENCY CHAIN, not three lists by date.

For each action:
- description: specific, not generic
- timeframe: 30_day / 60_day / 90_day / later
- prerequisite: which other action must complete first? ("independent" if none)
- owner_role: role, not name
- success_signal: how we know this worked
- reversibility: reversible / costly / one_way
- rumelt_coherence_check: ONE sentence explaining how this action supports the guiding policy

CRITICAL PATH: order actions into the dependency spine.
ONE-WAY DOORS: identify. They should be sequenced AFTER cheap reversible experiments.
DECISION GATES: at day 30, 60, 90 — what condition determines continue vs. pivot?
MONDAY MORNING ACTION: the single most important thing to do THIS WEEK.

Before including any action, ask: "Does this action, executed well, advance the guiding policy?" If unclear, write to concerns.
</your_task>

<input_context>
Crux: {crux_output}
Portfolio chosen option: {portfolio_rec}
Portfolio guiding policy: {guiding_policy}
Portfolio downstream instruction: {downstream_instruction}
Portfolio kill criteria: {json.dumps(kill_criteria)[:2000]}
Portfolio full output (reference): {portfolio_text[:5000]}
Financial capital requirements: {financial_text[:2500]}
Tech capability decisions: {tech_text[:2000]}
Human role requirements: {human_text[:2000]}
</input_context>
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:4000]}\n</document_context>\n"

    prompt += """
Return JSON:
{
  "chosen_option_name": "string — must match Portfolio primary_recommendation",
  "guiding_policy_reference": "string — restate the guiding policy this plan executes",
  "actions": [
    {
      "description": "string — specific",
      "timeframe": "30_day|60_day|90_day|later",
      "prerequisite": "string (description of prior action) or null",
      "owner_role": "string",
      "success_signal": "string",
      "reversibility": "reversible|costly|one_way",
      "rumelt_coherence_check": "string — ONE sentence"
    }
  ],
  "critical_path": ["ordered action description", "..."],
  "one_way_doors": ["action description"],
  "decision_gates": [
    {"day": 30, "condition": "string", "if_pass": "string", "if_fail": "string"},
    {"day": 60, "condition": "string", "if_pass": "string", "if_fail": "string"},
    {"day": 90, "condition": "string", "if_pass": "string", "if_fail": "string"}
  ],
  "pivot_path": "string — reference to backup option with trigger conditions",
  "monday_morning_action": "string — the ONE thing to do this Monday",
  "memo_contribution": "string — 2-3 sentences for Synthesizer's Coherent Actions section",
  "considerations": [{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}],
  "concerns": ["string"]
}
"""

    data = await _llm_json(OPS_SYSTEM, prompt, max_tokens=3000,
                           temperature=0.4, timeout=75.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "actions": [],
            "memo_contribution": f"Ops error: {data['error']}",
            "markdown": f"## Ops Error\n\n{data['error']}",
            "considerations": [],
            "concerns": [f"Ops failed: {data['error']}"],
        }

    md = []
    if data.get("chosen_option_name"):
        md.append(f"## Executing: **{data['chosen_option_name']}**")
    if data.get("guiding_policy_reference"):
        md.append(f"_{data['guiding_policy_reference']}_")
    if data.get("monday_morning_action"):
        md.append(f"\n### 🗓 Monday Morning Action\n\n{data['monday_morning_action']}")

    # 30/60/90 table
    actions = data.get("actions") or []
    if actions:
        md.append("\n## Action Plan")
        md.append("\n| When | Action | Owner | Signal | Reversibility |")
        md.append("|------|--------|-------|--------|---------------|")
        for a in actions:
            md.append(
                f"| {a.get('timeframe','?')} | {a.get('description','?')} | "
                f"{a.get('owner_role','?')} | {a.get('success_signal','?')} | "
                f"{a.get('reversibility','?')} |"
            )

    if data.get("critical_path"):
        md.append("\n## Critical Path")
        for i, step in enumerate(data["critical_path"], 1):
            md.append(f"{i}. {step}")
    if data.get("one_way_doors"):
        md.append("\n## ⚠ One-Way Doors")
        for d in data["one_way_doors"]:
            md.append(f"- {d}")
    if data.get("decision_gates"):
        md.append("\n## Decision Gates")
        for g in data["decision_gates"]:
            md.append(
                f"- **Day {g.get('day','?')}**: {g.get('condition','?')}  \n"
                f"  ✓ if pass: {g.get('if_pass','')}  \n"
                f"  ✗ if fail: {g.get('if_fail','')}"
            )
    if data.get("pivot_path"):
        md.append(f"\n## Pivot Path\n\n{data['pivot_path']}")

    data["markdown"] = normalize_markdown("\n".join(md))
    return data


# ------------------------------------------------------------
# AGENT 5: AI & TECHNOLOGY AGENT (v2 — capability layer, bound to Portfolio)
# ------------------------------------------------------------
TECH_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Tech Agent. You translate the recommended strategy into required technical capabilities and name the risks of getting them wrong.

You think in build/buy/partner tradeoffs, in time-to-capability, and in what the firm will regret in 18 months if it under-invests in measurement infrastructure today.

You are NOT a general IT strategist. You are specifically thinking about what the CHOSEN strategic option requires, technically, to succeed.
</your_role>
"""


async def tech_agent(ops_output=None, document_context=None,
                     portfolio_output=None) -> dict:
    """v2 Tech — bound to Portfolio's chosen option. Build/buy/partner analysis,
    technical risk, measurement instrumentation.
    """
    portfolio_rec = ""
    portfolio_thesis = ""
    portfolio_di = ""
    if isinstance(portfolio_output, dict):
        portfolio_rec = portfolio_output.get("primary_recommendation", "")
        portfolio_di = portfolio_output.get("downstream_instruction", "")
        for opt in (portfolio_output.get("options") or []):
            if opt.get("name") == portfolio_rec:
                portfolio_thesis = opt.get("core_thesis", "")
                break

    prompt = f"""<your_task>
For the recommended option, define the technical layer.

1. REQUIRED CAPABILITIES — specific. "Customer-level attribution across Meta/email/wholesale" beats "better data."
2. CAPABILITY DECISIONS — build / buy / partner / existing. Consider time-to-capability, cost, strategic importance. Foundational capabilities should be built or bought, not partnered.
3. TECHNICAL RISKS — severity × likelihood, specific mitigations.
4. DATA & MEASUREMENT — what MUST be instrumented to know if strategy works. "Install attribution in month 1, before spending reallocation, or make decisions on corrupted signal for 6+ months."
5. TECHNICAL DEBT IMPLICATIONS — will this strategy create debt? When does it come due?
6. PLATFORM DEPENDENCIES — third-party dependencies this strategy introduces or deepens.
</your_task>

<input_context>
Recommended option: {portfolio_rec}
Option thesis: {portfolio_thesis}
Portfolio downstream instruction: {portfolio_di}
</input_context>
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:5000]}\n</document_context>\n"

    prompt += """
Return JSON:
{
  "chosen_option_name": "string",
  "required_capabilities": ["string — specific"],
  "capability_decisions": [
    {
      "capability": "string",
      "recommendation": "build|buy|partner|existing",
      "rationale": "string",
      "time_to_capability": "string",
      "cost_magnitude": "low|medium|high|very_high",
      "strategic_importance": "foundational|supporting|peripheral"
    }
  ],
  "technical_risks": [
    {"risk": "string", "severity": 4, "likelihood": 3, "mitigation": "string"}
  ],
  "data_and_measurement": ["string — specific signals to instrument"],
  "technical_debt_implications": "string",
  "platform_dependencies": ["string"],
  "memo_contribution": "string — 2-3 sentences for Synthesizer",
  "considerations": [{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}],
  "concerns": ["string"]
}
"""

    data = await _llm_json(TECH_SYSTEM, prompt, max_tokens=2500,
                           temperature=0.4, timeout=60.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "capability_decisions": [],
            "technical_risks": [],
            "memo_contribution": f"Tech error: {data['error']}",
            "markdown": f"## Tech Error\n\n{data['error']}",
            "considerations": [],
            "concerns": [f"Tech failed: {data['error']}"],
        }

    md = []
    if data.get("required_capabilities"):
        md.append("## Required Capabilities")
        for c in data["required_capabilities"]:
            md.append(f"- {c}")
    if data.get("capability_decisions"):
        md.append("\n## Build / Buy / Partner")
        md.append("\n| Capability | Decision | Time | Cost | Importance |")
        md.append("|------------|----------|------|------|------------|")
        for cd in data["capability_decisions"]:
            md.append(
                f"| {cd.get('capability','?')} | **{cd.get('recommendation','?')}** | "
                f"{cd.get('time_to_capability','?')} | {cd.get('cost_magnitude','?')} | "
                f"{cd.get('strategic_importance','?')} |"
            )
    if data.get("data_and_measurement"):
        md.append("\n## Data & Measurement")
        for m in data["data_and_measurement"]:
            md.append(f"- {m}")
    if data.get("technical_risks"):
        md.append("\n## Technical Risks")
        for r in data["technical_risks"]:
            md.append(
                f"- **{r.get('risk','?')}** (sev {r.get('severity','?')}/5, lik {r.get('likelihood','?')}/5) — "
                f"{r.get('mitigation','')}"
            )
    if data.get("platform_dependencies"):
        md.append("\n## Platform Dependencies")
        for p in data["platform_dependencies"]:
            md.append(f"- {p}")
    if data.get("technical_debt_implications"):
        md.append(f"\n## Technical Debt\n\n{data['technical_debt_implications']}")

    data["markdown"] = normalize_markdown("\n".join(md))
    return data


# ------------------------------------------------------------
# AGENT 6: HUMAN AGENT (v2 — people layer)
# ------------------------------------------------------------
HUMAN_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Human Agent. You assess the PEOPLE layer of strategy execution — the thing that breaks more strategies than any technical or financial factor.

You know that most strategic failure is not analytical failure. It's the firm's inability to execute because the people can't, won't, or don't have the bandwidth to.

Be kind but direct. "Change management considerations" is softening. Name things.
</your_role>
"""


async def human_factors_agent(ops_output=None, document_context=None,
                              portfolio_output=None) -> dict:
    """v2 Human — bound to Portfolio's chosen option. Talent, culture,
    change management, leadership bandwidth, stakeholder map.
    """
    portfolio_rec = ""
    portfolio_thesis = ""
    if isinstance(portfolio_output, dict):
        portfolio_rec = portfolio_output.get("primary_recommendation", "")
        for opt in (portfolio_output.get("options") or []):
            if opt.get("name") == portfolio_rec:
                portfolio_thesis = opt.get("core_thesis", "")
                break

    prompt = f"""<your_task>
For the recommended option:
1. REQUIRED ROLES — does the firm have them? Senior hires take 4-6 months minimum and often fail.
2. CULTURAL FIT — not stated values, the ACTUAL culture as revealed by how the firm operates.
3. CULTURAL FRICTION POINTS — where will strategy grate against the firm's natural behavior?
4. CHANGE MANAGEMENT DIFFICULTY — low/medium/high/severe. 10% behavior change = easy. Redefining what firm does = severe.
5. INTERNAL RESISTANCE MAP — who resists, with how much influence? Map real power, not titles.
6. LEADERSHIP BANDWIDTH — executive cycles are the scarcest resource. If strategy needs CEO driving 3 initiatives, it fails.
7. LEADERSHIP CAPABILITY GAPS — what don't senior leaders know how to do that this requires?
</your_task>

<input_context>
Recommended option: {portfolio_rec}
Option thesis: {portfolio_thesis}
</input_context>
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:5000]}\n</document_context>\n"

    prompt += """
Return JSON:
{
  "chosen_option_name": "string",
  "required_roles": [
    {
      "role": "string",
      "level": "executive|senior|mid|junior",
      "existing_or_hire": "existing_fits|existing_needs_development|hire_required|contractor_sufficient",
      "timeline": "string",
      "cost_magnitude": "low|medium|high"
    }
  ],
  "cultural_fit_assessment": "string — does this strategy fit the firm's ACTUAL culture?",
  "cultural_friction_points": ["string"],
  "change_management_difficulty": "low|medium|high|severe",
  "change_management_risks": ["string"],
  "internal_resistance_map": [
    {
      "stakeholder": "string (role)",
      "likely_position": "champion|supportive|neutral|skeptical|opposed",
      "influence_level": "high|medium|low",
      "mitigation_or_enrollment": "string"
    }
  ],
  "leadership_bandwidth_assessment": "string — does leadership have cycles?",
  "leadership_capability_gaps": ["string"],
  "memo_contribution": "string — 2-3 sentences for Synthesizer",
  "considerations": [{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}],
  "concerns": ["string"]
}
"""

    data = await _llm_json(HUMAN_SYSTEM, prompt, max_tokens=2500,
                           temperature=0.4, timeout=60.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "required_roles": [],
            "internal_resistance_map": [],
            "memo_contribution": f"Human error: {data['error']}",
            "markdown": f"## Human Error\n\n{data['error']}",
            "considerations": [],
            "concerns": [f"Human failed: {data['error']}"],
        }

    md = []
    if data.get("change_management_difficulty"):
        md.append(f"**Change management difficulty:** `{data['change_management_difficulty']}`")
    if data.get("cultural_fit_assessment"):
        md.append(f"\n## Cultural Fit\n\n{data['cultural_fit_assessment']}")
    if data.get("cultural_friction_points"):
        md.append("\n## Friction Points")
        for f in data["cultural_friction_points"]:
            md.append(f"- {f}")
    if data.get("required_roles"):
        md.append("\n## Required Roles")
        md.append("\n| Role | Level | Status | Timeline | Cost |")
        md.append("|------|-------|--------|----------|------|")
        for r in data["required_roles"]:
            md.append(
                f"| {r.get('role','?')} | {r.get('level','?')} | {r.get('existing_or_hire','?')} | "
                f"{r.get('timeline','?')} | {r.get('cost_magnitude','?')} |"
            )
    if data.get("internal_resistance_map"):
        md.append("\n## Internal Resistance Map")
        for s in data["internal_resistance_map"]:
            md.append(
                f"- **{s.get('stakeholder','?')}** ({s.get('likely_position','?')}, "
                f"influence: {s.get('influence_level','?')}) — {s.get('mitigation_or_enrollment','')}"
            )
    if data.get("leadership_bandwidth_assessment"):
        md.append(f"\n## Leadership Bandwidth\n\n{data['leadership_bandwidth_assessment']}")
    if data.get("leadership_capability_gaps"):
        md.append("\n## Leadership Gaps")
        for g in data["leadership_capability_gaps"]:
            md.append(f"- {g}")

    data["markdown"] = normalize_markdown("\n".join(md))
    return data

# ------------------------------------------------------------
# AGENT 6.5: STRATEGY PORTFOLIO AGENT
# ------------------------------------------------------------
PORTFOLIO_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Portfolio Agent. You generate genuinely distinct strategic options, score them honestly, and make a call.

Your role in the Rumelt architecture: you own the GUIDING POLICY. The Diagnostician named the Crux; you define the approach that addresses it. The Ops agent will execute what you decide.

Your voice is a senior McKinsey partner presenting to a CEO and board. You are not hedging. You have a view.

Three common failures to avoid:
1. "Three flavors of the same strategy" — if all options bet on the same theory, you have one option with variants.
2. "Score everything evenly" — if your recommendation only wins by 0.3 on a 10-point scale, you averaged instead of choosing.
3. "Recommend without tradeoff" — every real strategy gives something up. Name what.
</your_role>
"""


async def strategy_portfolio_agent(situation: str, goal: str, constraints: str,
                                    crux: str, document_context=None,
                                    diagnosis_output: Optional[dict] = None,
                                    enright_output: Optional[dict] = None,
                                    market_forces_output: Optional[dict] = None,
                                    key_numbers: str = "") -> dict:
    """v2 Portfolio — binding downstream_instruction + kill criteria + load-bearing assumptions."""
    # Extract key anchors from upstream
    diag_crux = ""
    diag_threat = ""
    if diagnosis_output and isinstance(diagnosis_output, dict):
        diag_crux = diagnosis_output.get("crux_sentence", "")
        diag_threat = diagnosis_output.get("primary_external_threat", "")

    dominant_level = "industry"
    altitude_insight = ""
    if enright_output and isinstance(enright_output, dict):
        dominant_level = enright_output.get("dominant_level", "industry")
        altitude_insight = enright_output.get("altitude_insight", "")

    mf_summary = ""
    if market_forces_output and isinstance(market_forces_output, dict):
        mf_summary = f"Most determinative force: {market_forces_output.get('most_determinative', '?')}"

    prompt = f"""<your_task>
Generate 3-4 genuinely DISTINCT strategic options, each betting on a DIFFERENT theory of the business. McKinsey-level specificity — real companies named, real numbers, real dates, real mechanisms.

Test for distinctness: if I asked "what does each option believe about where the real value is?" — do I get different answers? If not, collapse them.

For each option:
- one_liner: under 15 words. The whole bet in a sentence.
- core_thesis: falsifiable. "What would make this option wrong?"
- enright_level_addressed: locate the option at the right altitude (often NOT firm level)
- guiding_policy: Rumelt's term — the overall approach, not a laundry list of tactics
- coherent_actions: 3-5 actions with SPECIFIC NAMED MECHANISMS — "hire VP Community from DTC native with 2.1M+ follower experience" not "invest in community"
- threat_response: MANDATORY — how does this option address the primary_external_threat? Name the specific threat actor and the counter-mechanism.
- kill_criteria: NUMERIC AND TIME-BOUND. Each must have: specific metric, numeric threshold, specific timeframe. "If blended CAC doesn't fall below $48 within 90 days" passes. "If CAC doesn't improve" fails.
- load_bearing_assumptions: what MUST be true for the option to work. Each must be falsifiable with a specific test.

Consider generating a MOONSHOT — an ambitious option that would reshape the firm if it worked.

recommendation_rationale: head-to-head argument. "Option A beats B on X because [specific mechanism]; loses on Y because [specific tradeoff]" — not scoring.

what_we_are_giving_up: name the specific strategic non-choice. "Accepting 18 months of flat margin in exchange for a defensible position" beats "there are tradeoffs."

downstream_instruction is a CONTRACT executed by Ops/Financial/Tech/Human. Must be actionable: "Execute Option A. Monthly test: email/SMS share ≥25% of revenue by month 4. Reference backup Option C only if monthly test misses by >5pp."
</your_task>

<examples_of_good_vs_bad>
❌ BAD action: "Invest in customer retention."
✅ GOOD action: "Rebuild the post-purchase flow with 30-day/60-day/90-day SMS touchpoints targeting the $85 LTV gap between cohort 1 and cohort 3, using Attentive (already deployed) + Klaviyo handoff, Q1 2025 launch."

❌ BAD threat_response: "We will monitor competitive threats."
✅ GOOD threat_response: "The dupe competitor (2.1M TikTok followers) attacks on product-quality axis. This option defends by moving positioning to ritual + community (Glossier's 2017-2020 play, which defended 60pp margin against Sephora private label). Dupe can copy a formulation in 6 months; cannot copy community in 24."

❌ BAD kill_criteria: "If growth doesn't improve."
✅ GOOD kill_criteria: {{ "metric": "mid-market ACV mix", "threshold": "<25% of new bookings", "timeframe": "by end of Q2 2025" }} — observable from Salesforce reports, time-bound, operator can act on it.

❌ BAD load_bearing_assumption: "Market conditions remain favorable."
✅ GOOD load_bearing_assumption: {{ "assumption": "Meta attribution recovery doesn't exceed 40% of iOS 14 pre-ATT accuracy by EOY 2025", "confidence": "high", "if_wrong": "Primary option is uneconomical — paid becomes viable again and community investment loses relative ROI", "how_to_test": "Monthly Meta Pixel vs MMM variance; flag if variance falls below 15% for 2 consecutive months." }}

❌ BAD rationale: "Option A scored 8.2 vs Option B's 7.9."
✅ GOOD rationale: "Option A beats Option C because community compounds (24-month half-life) while wholesale growth (Option C) is capped by Sephora's 3-year distribution lock-out clauses. Option A loses to Option C on cash — burns an incremental $3.2M through month 9 before the community flywheel engages. The CEO must accept 18 months of cash pressure to buy a defensible position; if she can't, Option C is the right answer."
</examples_of_good_vs_bad>

<input_context>
Situation: {situation}
Goal: {goal}
Key Numbers: {key_numbers}
Constraints: {constraints}
Crux (Diagnostician): {diag_crux or crux}
Primary external threat: {diag_threat}
Enright dominant level: {dominant_level}
Enright altitude insight: {altitude_insight}
{mf_summary}
</input_context>
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:5000]}\n</document_context>\n"

    prompt += """
Return JSON matching this schema:
{
  "options": [
    {
      "name": "string, <= 4 words",
      "one_liner": "string <= 100 chars, 15 words max",
      "core_thesis": "string — what theory of the business this option bets on",
      "enright_level_addressed": "supranational|national|cluster|industry|firm",
      "guiding_policy": "string — Rumelt's guiding policy for this option",
      "coherent_actions": ["string", "string", "string"],
      "primary_leverage_point": "string",
      "threat_response": "string — how this option handles primary_external_threat",
      "load_bearing_assumptions": [
        {"assumption": "string", "confidence": "high|medium|low", "if_wrong": "string", "how_to_test": "string"}
      ],
      "kill_criteria": [
        {"metric": "string", "threshold": "string (numeric)", "timeframe": "string (time-bound)"}
      ],
      "failure_modes": [
        {"mode": "string", "probability": "high|medium|low", "mitigation": "string"}
      ],
      "scores": {
        "strategic_fit": 7,
        "impact": 8,
        "feasibility": 6,
        "time_to_impact": 5,
        "risk_exposure": 4,
        "differentiation": 9,
        "constraint_alignment": 7
      }
    }
    // 3-4 options
  ],
  "moonshot": null,
  "primary_recommendation": "string — name of chosen option (must match one of options[].name)",
  "recommendation_rationale": "string — head-to-head argument: why primary beats second-place specifically",
  "what_we_are_giving_up": "string — what the operator trades away by choosing primary",
  "backup_recommendation": "string — name of backup option",
  "pivot_triggers": [
    {"signal": "string", "timeframe": "string", "action": "string"}
  ],
  "tradeoff_matrix": "string — A vs B, B vs C honest comparison (multi-line markdown)",
  "downstream_instruction": "string — CONTRACT for Ops/Financial/Tech/Human. 'Execute X. Reference Y only via pivot triggers.'",
  "memo_contribution": "string — 3-4 sentences for Synthesizer's Guiding Policy section",
  "considerations": [{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}],
  "concerns": ["string"]
}
"""

    data = await _llm_json(PORTFOLIO_SYSTEM, prompt, max_tokens=4000,
                           temperature=0.6, timeout=120.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "options": [],
            "primary_recommendation": None,
            "downstream_instruction": "",
            "memo_contribution": f"Portfolio error: {data['error']}",
            "markdown": f"## Portfolio Error\n\n{data['error']}",
            "considerations": [],
            "concerns": [f"Portfolio failed: {data['error']}"],
        }

    # Markdown render for pane display + downstream consumption
    md = []
    if data.get("primary_recommendation"):
        md.append(f"## Primary Recommendation: **{data['primary_recommendation']}**")
    if data.get("recommendation_rationale"):
        md.append(data["recommendation_rationale"])
    if data.get("what_we_are_giving_up"):
        md.append(f"\n### What we're giving up\n\n{data['what_we_are_giving_up']}")
    if data.get("backup_recommendation"):
        md.append(f"\n**Backup:** {data['backup_recommendation']}")
    pivots = data.get("pivot_triggers") or []
    if pivots:
        md.append("\n### Pivot triggers")
        for p in pivots:
            md.append(f"- **{p.get('signal', '?')}** ({p.get('timeframe', '?')}) → {p.get('action', '')}")
    md.append("\n## Options")
    for i, opt in enumerate(data.get("options") or [], 1):
        md.append(f"\n### Option {i}: {opt.get('name', '?')}")
        if opt.get("one_liner"):
            md.append(f"_{opt['one_liner']}_")
        if opt.get("core_thesis"):
            md.append(f"\n**Core thesis:** {opt['core_thesis']}")
        if opt.get("enright_level_addressed"):
            md.append(f"**Operating level:** `{opt['enright_level_addressed']}`")
        if opt.get("guiding_policy"):
            md.append(f"**Guiding policy:** {opt['guiding_policy']}")
        if opt.get("threat_response"):
            md.append(f"**Threat response:** {opt['threat_response']}")
        if opt.get("coherent_actions"):
            md.append("**Actions:**")
            for a in opt["coherent_actions"]:
                md.append(f"- {a}")
        kc = opt.get("kill_criteria") or []
        if kc:
            md.append("**Kill criteria:**")
            for k in kc:
                md.append(f"- If **{k.get('metric','?')}** {k.get('threshold','?')} within {k.get('timeframe','?')} → kill")
    if data.get("tradeoff_matrix"):
        md.append(f"\n## Tradeoff Matrix\n\n{data['tradeoff_matrix']}")
    if data.get("downstream_instruction"):
        md.append(f"\n---\n\n> **Downstream instruction:** {data['downstream_instruction']}")
    data["markdown"] = normalize_markdown("\n".join(md))
    return data

# ------------------------------------------------------------
# AGENT 7: RED TEAM AGENT (V2)
# ------------------------------------------------------------
RED_TEAM_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Red Team. You are NOT the system's critic — you are a steelman for the opposing view.

YOUR PERSONA: a skeptical investment committee member. 40+ strategy presentations. Has watched every turnaround plan fail in every possible way. Intellectually honest — you WANT the firm to succeed, but you've seen too many confident plans collapse. You target the Portfolio Agent's load_bearing_assumptions directly.

Your output serves TWO purposes:
1. Surface the 3 most dangerous attacks with specific leading indicators
2. STEELMAN the opposing view — what would a smart skeptic recommend INSTEAD?
</your_role>
"""


async def red_team_agent_v2(crux, drivers, financial, ops, tech, human,
                             document_context=None, portfolio=None) -> dict:
    """v2 Red Team — persona-driven, EXACTLY 3 attacks + steelman + revision_required flag."""
    def _to_text(x):
        if x is None: return ""
        if isinstance(x, dict): return x.get("markdown") or x.get("memo_contribution") or ""
        return str(x)

    crux_s = _to_text(crux) if not isinstance(crux, str) else crux
    drivers_s = _to_text(drivers) if not isinstance(drivers, str) else drivers
    financial_s = _to_text(financial) if not isinstance(financial, str) else financial
    ops_s = _to_text(ops) if not isinstance(ops, str) else ops
    tech_s = _to_text(tech) if not isinstance(tech, str) else tech
    human_s = _to_text(human) if not isinstance(human, str) else human
    portfolio_s = _to_text(portfolio) if not isinstance(portfolio, str) else portfolio

    portfolio_rec = ""
    load_bearing = []
    if isinstance(portfolio, dict):
        portfolio_rec = portfolio.get("primary_recommendation", "")
        for opt in (portfolio.get("options") or []):
            if opt.get("name") == portfolio_rec:
                load_bearing = opt.get("load_bearing_assumptions", []) or []
                break

    prompt = f"""<your_task>
Identify EXACTLY 3 attacks. Not more. Forcing the discipline of naming the most dangerous three.

For each attack:
- attack_vector: what specifically kills the strategy?
- severity 1-5, likelihood 1-5
- target_assumption: WHICH load-bearing assumption does this kill?
- leading_indicator: how would we know this is materializing by month 2-3?
- rebuttal_difficulty: easy (manageable) / hard (expensive to rebut) / fatal (no recovery)
- recommended_counter (optional)

STRONGEST ATTACK: if one attack could alone kill the strategy, which is it?
STRATEGY SURVIVES: can the strategy survive its strongest attack with reasonable mitigation?
STEELMAN: what would a smart skeptic recommend INSTEAD?
- State the alternative clearly
- Strongest argument for it
- If primary still wins on reflection, explain why. If steelman actually wins, say so and set revision_required = True.
</your_task>

<input_context>
Primary recommendation: {portfolio_rec}
Load-bearing assumptions: {json.dumps(load_bearing)[:2500]}
[CRUX]: {crux_s[:1500]}
[DRIVERS]: {drivers_s[:1500]}
[FINANCIAL]: {financial_s[:2500]}
[OPS]: {ops_s[:2500]}
[TECH]: {tech_s[:1500]}
[HUMAN]: {human_s[:1500]}
[PORTFOLIO]: {portfolio_s[:4000]}
</input_context>
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:4000]}\n</document_context>\n"

    prompt += """
Return JSON:
{
  "persona": "Skeptical Investment Committee",
  "attacks": [
    {
      "attack_vector": "string",
      "severity": 5,
      "likelihood": 3,
      "target_assumption": "string — which load-bearing assumption this kills",
      "leading_indicator": "string — how we'd see it materializing early",
      "rebuttal_difficulty": "easy|hard|fatal",
      "recommended_counter": "string or null"
    }
    // EXACTLY 3
  ],
  "strongest_attack": "string — which attack's attack_vector is the killer",
  "strategy_survives": true,
  "revision_required": false,
  "suggested_revision": "string or null",
  "steelman": {
    "alternative_recommendation": "string — what a smart skeptic would recommend instead",
    "strongest_argument": "string — the sharpest version of that case",
    "why_primary_still_wins": "string or null — if primary still wins on reflection"
  },
  "memo_contribution": "string — 2-3 sentences for Synthesizer's Key Risks section, ending with the top fix",
  "considerations": [{"consideration": "string", "why_it_matters": "string", "what_would_resolve_it": "string or null"}]
}
"""

    data = await _llm_json(RED_TEAM_SYSTEM, prompt, max_tokens=2500,
                           temperature=0.65, timeout=75.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "attacks": [],
            "strategy_survives": True,
            "revision_required": False,
            "memo_contribution": f"Red Team error: {data['error']}",
            "markdown": f"## Red Team Error\n\n{data['error']}",
            "considerations": [],
            "concerns": [f"Red Team failed: {data['error']}"],
        }

    md = []
    attacks = data.get("attacks") or []
    for i, a in enumerate(attacks, 1):
        md.append(f"## Attack {i}: {a.get('attack_vector','?')}")
        md.append(
            f"**Severity** {a.get('severity','?')}/5 · "
            f"**Likelihood** {a.get('likelihood','?')}/5 · "
            f"**Rebuttal** `{a.get('rebuttal_difficulty','?')}`"
        )
        if a.get("target_assumption"):
            md.append(f"\n_Kills assumption:_ {a['target_assumption']}")
        if a.get("leading_indicator"):
            md.append(f"\n_Leading indicator:_ {a['leading_indicator']}")
        if a.get("recommended_counter"):
            md.append(f"\n_Counter:_ {a['recommended_counter']}")
        md.append("")
    if data.get("strongest_attack"):
        md.append(f"## 💀 Strongest Attack\n\n{data['strongest_attack']}")
    if data.get("revision_required"):
        md.append(f"\n### ⚠ Revision Required\n\n{data.get('suggested_revision','')}")
    st = data.get("steelman") or {}
    if st.get("alternative_recommendation"):
        md.append("\n## Steelman — What a Smart Skeptic Would Do Instead")
        md.append(f"\n**Alternative:** {st['alternative_recommendation']}")
        if st.get("strongest_argument"):
            md.append(f"\n**Strongest argument:** {st['strongest_argument']}")
        if st.get("why_primary_still_wins"):
            md.append(f"\n**Why primary still wins:** {st['why_primary_still_wins']}")

    data["markdown"] = normalize_markdown("\n".join(md))
    return data

# ------------------------------------------------------------
# AGENT 8: SYNTHESIZER AGENT
# ------------------------------------------------------------
SYNTHESIZER_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Synthesizer. You do NOT re-analyze. You ASSEMBLE.

Upstream agents have produced pre-computed memo_contribution strings. Your job is compression, stitching, and voice. If you find yourself generating new analytical claims, you are doing it wrong — every claim must trace to an upstream agent.

The only original content you produce is:
1. The Bet (synthesized from Portfolio's core_thesis, load_bearing_assumptions, and kill_criteria)
2. Transitional connective tissue between sections
3. Quality gate flags
4. Operator considerations (curated from upstream considerations fields)

Your voice is a senior McKinsey partner writing the cover memo to a board deck. The memo should feel like it was written by one mind, not assembled by a committee.
</your_role>

<the_bet_specification>
STRUCTURE (required):
"We bet that [CONTRARIAN CLAIM about the world or the firm]. If we are wrong about [SPECIFIC ASSUMPTION], the strategy fails because [SPECIFIC CONSEQUENCE]. We will know within [SPECIFIC TIMEFRAME] by watching [SPECIFIC METRIC]."

Three requirements:
1. CONTRARIAN: A Bet everyone would agree with is a platitude, not a bet.
2. FALSIFIABLE: You must be able to imagine being wrong.
3. TIME-BOUND WITH SIGNAL: Extract timeframe and signal from Portfolio.kill_criteria.

If Portfolio's core_thesis isn't contrarian enough, write to flags_for_operator: "The recommended strategy lacks a contrarian thesis — this may be a defensive play disguised as offense." Do not manufacture a fake bet.
</the_bet_specification>

<what_we_are_not_doing_specification>
BAD (restated constraint): "Not expanding to Amazon because of brand positioning."
GOOD (strategic non-choice): "Not investing in defending the hero SKU at the product-quality axis. We believe that axis commoditizes within 12 months regardless of our investment, and the firm's value should migrate to ritual and community before the category collapses."

The good version makes a CALL the operator could defend or dispute. The bad version is "we can't."

Require at least 3 strategic non-choices. If you can't produce them from upstream outputs, write to flags_for_operator: "The strategy lacks strong non-choices — options may not be as distinct as Portfolio claimed."
</what_we_are_not_doing_specification>

<quality_gates>
1. MONDAY MORNING TEST: If operator reads only The Bet + first sentence of Executive Summary, can they act? If no, revise opening until yes.
2. MATH CONSISTENCY: Use exact numbers from Financial.memo_contribution. If goal_gap_flag is True, acknowledge it.
3. BET FALSIFIABILITY: The Bet has explicit contrarian claim, consequence, timeframe, and signal.
4. RED TEAM SURVIVAL: If revision_required is True, flag this at the TOP of flags_for_operator. Do not bury it.

For any gate that fails, set the boolean False and write the issue to flags_for_operator.
</quality_gates>

<forbidden_phrases>
Ban from the final memo:
- "It may be worth considering..."
- "A variety of factors..."
- "Leverage synergies"
- "Optimize operational efficiencies"
- "In today's competitive landscape..."
- "Going forward"
- Any sentence that could appear in a generic consulting deck
</forbidden_phrases>
"""


async def synthesizer_agent(enright=None, diagnosis=None, frameworks=None,
                             structure=None, market_forces=None, portfolio=None,
                             financial=None, tech=None, human=None, ops=None,
                             red_team=None, document_context=None) -> dict:
    """v2 Synthesizer — assembler pattern. Reads memo_contribution from upstream agents;
    produces The Bet + Executive Summary + quality gate flags + memo markdown.
    All upstream args are dicts (or None). Returns a dict with markdown + structured fields.
    """
    def _mc(x, fallback_field="markdown"):
        if x is None: return ""
        if isinstance(x, dict):
            return x.get("memo_contribution") or x.get(fallback_field) or ""
        return str(x)

    # Pull the memo_contributions + key structured anchors from upstream
    diag_memo = _mc(diagnosis)
    enright_memo = _mc(enright)
    frameworks_memo = _mc(frameworks)
    structure_memo = _mc(structure)
    mf_memo = _mc(market_forces)
    portfolio_memo = _mc(portfolio)
    financial_memo = _mc(financial)
    tech_memo = _mc(tech)
    human_memo = _mc(human)
    ops_memo = _mc(ops)
    red_memo = _mc(red_team)

    # Anchors for The Bet
    crux_sentence = ""
    primary_threat = ""
    if isinstance(diagnosis, dict):
        crux_sentence = diagnosis.get("crux_sentence", "")
        primary_threat = diagnosis.get("primary_external_threat", "")

    portfolio_rec = ""
    portfolio_thesis = ""
    portfolio_kc = []
    portfolio_lba = []
    downstream_instruction = ""
    if isinstance(portfolio, dict):
        portfolio_rec = portfolio.get("primary_recommendation", "")
        downstream_instruction = portfolio.get("downstream_instruction", "")
        for opt in (portfolio.get("options") or []):
            if opt.get("name") == portfolio_rec:
                portfolio_thesis = opt.get("core_thesis", "")
                portfolio_kc = opt.get("kill_criteria", []) or []
                portfolio_lba = opt.get("load_bearing_assumptions", []) or []
                break

    # Red team flags
    red_revision = False
    red_suggested = ""
    if isinstance(red_team, dict):
        red_revision = bool(red_team.get("revision_required"))
        red_suggested = red_team.get("suggested_revision", "") or ""

    # Financial gap
    goal_gap = False
    goal_gap_text = ""
    if isinstance(financial, dict):
        goal_gap = bool(financial.get("goal_gap_flag"))
        goal_gap_text = financial.get("goal_gap_explanation", "") or ""

    # Gather considerations from all upstream for curation
    all_considerations = []
    for agent_out in [diagnosis, enright, frameworks, structure, market_forces,
                       portfolio, financial, tech, human, ops, red_team]:
        if isinstance(agent_out, dict):
            for c in (agent_out.get("considerations") or []):
                all_considerations.append(c)

    prompt = f"""<your_task>
Assemble the memo. Do NOT re-analyze. Every claim must trace to an upstream agent's memo_contribution.

Build The Bet from Portfolio's core_thesis + load_bearing_assumptions + kill_criteria.
Build "What We Are NOT Doing" from Portfolio's what_we_are_giving_up, tradeoff_matrix, and options not selected.
Run the quality gates. Flag failures loudly.
</your_task>

<upstream_memo_contributions>
[DIAGNOSTICIAN]: {diag_memo}
CRUX sentence: {crux_sentence}
Primary external threat: {primary_threat}

[ENRIGHT]: {enright_memo}

[FRAMEWORKS]: {frameworks_memo}

[STRUCTURE]: {structure_memo}

[MARKET FORCES]: {mf_memo}

[PORTFOLIO]: {portfolio_memo}
Primary recommendation: {portfolio_rec}
Core thesis: {portfolio_thesis}
Load-bearing assumptions: {json.dumps(portfolio_lba)[:1800]}
Kill criteria: {json.dumps(portfolio_kc)[:1200]}
Downstream instruction: {downstream_instruction}

[FINANCIAL]: {financial_memo}
Goal gap flag: {goal_gap}. Gap: {goal_gap_text}

[TECH]: {tech_memo}
[HUMAN]: {human_memo}
[OPS]: {ops_memo}

[RED TEAM]: {red_memo}
Revision required: {red_revision}. Suggested revision: {red_suggested}

[CONSIDERATIONS FROM UPSTREAM — curate for considerations_for_the_operator]:
{json.dumps(all_considerations)[:3000]}
</upstream_memo_contributions>
"""
    if document_context:
        prompt += f"\n<document_context>\n{document_context[:3000]}\n</document_context>\n"

    prompt += """
Return JSON. The_move field is NEW AND CRITICAL — the operator's #1 complaint about strategy memos is they bury the recommendation. Do not bury it here.

{
  "the_bet": "string — 'We bet that [contrarian claim]. If we are wrong about [specific assumption], the strategy fails because [specific consequence]. We will know within [specific timeframe] by watching [specific metric].'",
  "the_move": {
    "recommended_option_name": "string — name of the chosen Portfolio option (must match Portfolio.primary_recommendation exactly)",
    "one_sentence_what": "string — ONE sentence under 25 words naming what the firm will do. Must be specific and actionable. 'Shift 60% of paid-acquisition spend to community + owned channels over 9 months.' NOT 'pursue growth strategy.'",
    "why_this_beats_alternatives": "string — head-to-head argument against the backup option. Name both options by name. NAME the specific dimension where primary wins and the dimension where backup wins.",
    "first_week_action": "string — the ONE thing the operator should do this Monday. Must be specific (name the meeting, hire, contract, or metric to pull).",
    "decision_point_date": "string — when the operator will know if this is working. Must be a specific date/month and a specific observable metric."
  },
  "executive_summary": "string — max 100 words. Front-loaded with the_move.one_sentence_what as the opening sentence. Then: crux, strategic framing, non-negotiable tradeoff, timeline.",
  "crux": "string — from Diagnostician's memo_contribution, condensed.",
  "strategic_framing": "string — from Enright's memo_contribution. The altitude at which this is being fought.",
  "guiding_policy": "string — from Portfolio's memo_contribution. Direction + what we're NOT doing.",
  "what_we_are_not_doing": ["string — strategic non-choice, min 3 items", "string", "string"],
  "coherent_actions": ["string — from Ops memo_contribution, condensed"],
  "kpis": {"leading": ["string"], "lagging": ["string"]},
  "key_risks": ["string — from Red Team memo_contribution, specific not generic"],
  "thirty_sixty_ninety": {"30": "string", "60": "string", "90": "string"},
  "considerations_for_the_operator": ["string — curated from upstream considerations, things to sit with"],
  "passes_monday_morning_test": true,
  "math_consistent_with_goals": true,
  "bet_is_falsifiable": true,
  "survives_red_team": true,
  "flags_for_operator": ["string — if quality gate failed, explain. Red Team revision_required goes here FIRST if True."],
  "memo_contribution": "string — full memo as markdown for rendering"
}

the_move specification — every field is MANDATORY, no abstract language:
- recommended_option_name MUST match the Portfolio's primary_recommendation string exactly.
- one_sentence_what MUST be executable ("Shift X to Y over Z months" pattern is a good shape).
- why_this_beats_alternatives MUST cite the specific competitor option and dimension.
- first_week_action MUST be a Monday-morning decision the operator can make alone. Generic ("hold a strategy meeting") is rejected.
- decision_point_date MUST be specific ("End of Q1 2025" or "Day 90: email/SMS revenue share >25%" not "once we know").
"""

    data = await _llm_json(SYNTHESIZER_SYSTEM, prompt, max_tokens=4500,
                           temperature=0.5, timeout=90.0)
    if data.get("error"):
        return {
            "error": data["error"],
            "the_bet": "Strategy synthesis unavailable — agent error.",
            "memo_contribution": f"Synthesizer error: {data['error']}",
            "markdown": f"## Synthesizer Error\n\n{data['error']}",
            "flags_for_operator": [f"Synthesizer failed: {data['error']}"],
        }

    # Render the full memo as markdown (also becomes memo_contribution)
    md = []
    if data.get("the_bet"):
        md.append("## The Bet\n")
        md.append(f"**{data['the_bet']}**\n")

    # THE MOVE — prominent recommendation block, right after The Bet
    move = data.get("the_move") or {}
    if move and (move.get("recommended_option_name") or move.get("one_sentence_what")):
        md.append("\n## The Move\n")
        if move.get("recommended_option_name"):
            md.append(f"**Recommendation: {move['recommended_option_name']}**\n")
        if move.get("one_sentence_what"):
            md.append(f"> {move['one_sentence_what']}\n")
        if move.get("why_this_beats_alternatives"):
            md.append(f"\n**Why this beats the alternatives:** {move['why_this_beats_alternatives']}")
        if move.get("first_week_action"):
            md.append(f"\n**Monday morning:** {move['first_week_action']}")
        if move.get("decision_point_date"):
            md.append(f"\n**Decision point:** {move['decision_point_date']}")

    if data.get("flags_for_operator"):
        md.append("\n\n## ⚠ Flags for the Operator\n")
        for f in data["flags_for_operator"]:
            md.append(f"- {f}")
    if data.get("executive_summary"):
        md.append("\n## Executive Summary\n")
        md.append(data["executive_summary"])
    if data.get("crux"):
        md.append("\n## Diagnosis (The Crux)\n")
        md.append(data["crux"])
    if data.get("strategic_framing"):
        md.append("\n## Strategic Framing\n")
        md.append(data["strategic_framing"])
    if data.get("guiding_policy"):
        md.append("\n## Guiding Policy\n")
        md.append(data["guiding_policy"])
    wwd = data.get("what_we_are_not_doing") or []
    if wwd:
        md.append("\n## What We Are NOT Doing\n")
        for w in wwd:
            md.append(f"- {w}")
    ca = data.get("coherent_actions") or []
    if ca:
        md.append("\n## Coherent Actions\n")
        for a in ca:
            md.append(f"- {a}")
    kpis = data.get("kpis") or {}
    if kpis.get("leading") or kpis.get("lagging"):
        md.append("\n## KPIs & Leading Indicators\n")
        if kpis.get("leading"):
            md.append("**Leading:** " + "; ".join(kpis["leading"]))
        if kpis.get("lagging"):
            md.append("**Lagging:** " + "; ".join(kpis["lagging"]))
    kr = data.get("key_risks") or []
    if kr:
        md.append("\n## Key Risks & Mitigations\n")
        for r in kr:
            md.append(f"- {r}")
    tsn = data.get("thirty_sixty_ninety") or {}
    if tsn:
        md.append("\n## 30 / 60 / 90\n")
        if tsn.get("30"): md.append(f"**30-day:** {tsn['30']}")
        if tsn.get("60"): md.append(f"**60-day:** {tsn['60']}")
        if tsn.get("90"): md.append(f"**90-day:** {tsn['90']}")
    ccs = data.get("considerations_for_the_operator") or []
    if ccs:
        md.append("\n## Considerations for the Operator\n")
        for c in ccs:
            md.append(f"- {c}")

    # Quality gate footer (visible so operator knows the self-check happened)
    gates = [
        ("Monday Morning Test", data.get("passes_monday_morning_test")),
        ("Math consistent with goals", data.get("math_consistent_with_goals")),
        ("Bet is falsifiable", data.get("bet_is_falsifiable")),
        ("Survives Red Team", data.get("survives_red_team")),
    ]
    md.append("\n---\n\n**Quality Gates:** " + " · ".join(
        f"{'✓' if v else '✗'} {k}" for k, v in gates
    ))

    final_md = normalize_markdown("\n".join(md))
    data["markdown"] = final_md
    data["memo_contribution"] = final_md
    return data

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
MAP_SYSTEM = UNIVERSAL_AGENT_PREAMBLE + """
<your_role>
You are the Strategy Map Agent. You translate the full pipeline output into a causal graph that visualizes the strategy as a system.

Your output powers the front-end Cytoscape visualization. Focus on CLARITY, not completeness — a map with 15 well-chosen nodes beats a map with 50 noisy ones.

Node types: crux, guiding_policy, action, outcome, risk, assumption.
Edge types: causes, enables, threatens, depends_on, reinforces, balances.
</your_role>
"""


async def map_agent(crux=None, drivers=None, financial=None, ops=None,
                     tech=None, human=None, red_team=None,
                     portfolio=None, diagnosis=None, enright=None) -> str:
    """v2 Strategy Map — node types + edge types + feedback_loops + single_points_of_failure.
    Returns a JSON STRING (compatible with legacy frontend renderCytoscapeDiagram).
    """
    def _to_text(x):
        if x is None: return ""
        if isinstance(x, dict): return x.get("markdown") or x.get("memo_contribution") or ""
        return str(x)

    crux_s = _to_text(crux) if not isinstance(crux, str) else crux
    drivers_s = _to_text(drivers) if not isinstance(drivers, str) else drivers
    financial_s = _to_text(financial) if not isinstance(financial, str) else financial
    ops_s = _to_text(ops) if not isinstance(ops, str) else ops
    tech_s = _to_text(tech) if not isinstance(tech, str) else tech
    human_s = _to_text(human) if not isinstance(human, str) else human
    red_s = _to_text(red_team) if not isinstance(red_team, str) else red_team
    portfolio_s = _to_text(portfolio) if not isinstance(portfolio, str) else portfolio

    prompt = f"""<your_task>
Extract the essential nodes from upstream outputs:
- CRUX: from Diagnostician (1 node)
- GUIDING POLICY: from Portfolio (1 node)
- ACTIONS: 3-7 most important actions from Ops
- OUTCOMES: 3-5 expected outcomes/KPIs
- RISKS: top 3 attacks from Red Team
- ASSUMPTIONS: 2-4 most critical load-bearing assumptions

Then draw edges:
- CAUSES: Action X causes Outcome Y
- ENABLES: Capability X enables Action Y
- THREATENS: Risk X threatens Outcome Y
- DEPENDS_ON: Action X depends on Action Y
- REINFORCES: Outcome X reinforces Outcome Y (virtuous cycle)
- BALANCES: Force X balances Force Y

Identify FEEDBACK LOOPS in the graph. Name them.
Identify CRITICAL PATH — sequence of nodes forming the spine.
Identify SINGLE POINTS OF FAILURE — nodes whose collapse kills the strategy.

Keep it tight. 15-25 nodes max. 20-40 edges max. Every edge must have an obvious justification from upstream.
</your_task>

<input_context>
[CRUX]: {crux_s[:1800]}
[DRIVERS]: {drivers_s[:1500]}
[FINANCIAL]: {financial_s[:1800]}
[OPS]: {ops_s[:2500]}
[TECH]: {tech_s[:1200]}
[HUMAN]: {human_s[:1200]}
[RED TEAM]: {red_s[:1800]}
[PORTFOLIO]: {portfolio_s[:2800]}
</input_context>

Return JSON matching this schema (compatible with Cytoscape):
{{
  "nodes": [
    {{"data": {{"id": "crux", "label": "short label", "type": "crux", "source_agent": "diagnosis"}}}}
  ],
  "edges": [
    {{"data": {{"source": "crux", "target": "gp", "weight": 8, "edge_type": "causes"}}}}
  ],
  "feedback_loops": [
    {{"type": "reinforcing", "nodes": ["id1","id2"], "description": "one sentence"}}
  ],
  "critical_path": ["node_id_1", "node_id_2"],
  "single_points_of_failure": ["node_id"],
  "memo_contribution": "one sentence summary"
}}
"""

    data = await _llm_json(MAP_SYSTEM, prompt, max_tokens=3500,
                           temperature=0.3, timeout=75.0)
    if data.get("error"):
        return json.dumps({
            "nodes": [{"data": {"id": "error", "label": "Map error", "type": "goal"}}],
            "edges": [],
            "error": data["error"],
        })
    return json.dumps(data)

# ------------------------------------------------------------
# STREAMING ORCHESTRATOR (V2)
# ------------------------------------------------------------
async def run_vanguard_pipeline_stream(inputs):
    """
    v2 pipeline (per Vanguard OS v2 spec):

    Diagnostician
        ↓
    [Enright + Frameworks + Structure + Market Forces] (parallel)
        ↓
    Portfolio (binding — produces downstream_instruction contract)
        ↓
    [Financial + Tech + Human] (parallel, all bound to Portfolio's chosen option)
        ↓
    Ops (executes using Portfolio + Financial + Tech + Human context)
        ↓
    Red Team
        ↓
    [Synthesizer + Strategy Map] (parallel, reading all upstream)

    All agents now return structured dicts with `memo_contribution` + `markdown`.
    The Synthesizer is an ASSEMBLER — it reads memo_contribution from every upstream.
    """
    situation = inputs.get("situation", "")
    goal = inputs.get("goal", "")
    constraints = inputs.get("constraints", "")
    key_numbers = inputs.get("numbers", "")
    success_metrics = inputs.get("success_metrics", "")
    document_context = inputs.get("document_context", None)

    # 0. Market Data (ticker ambient context — orthogonal, runs early)
    yield json.dumps({"type": "status", "data": "Fetching Market Data..."}) + "\n"
    try:
        market_data_out = await market_data_agent(situation, goal)
        if market_data_out != "No publicly traded companies detected.":
            yield json.dumps({"type": "market_data", "data": market_data_out}) + "\n"
    except Exception as e:
        print(f"market_data_agent error: {e}")

    # Deep Dive branch (unchanged)
    if inputs.get("mode") == "deep_dive":
        topic = inputs.get("topic", "")
        context = inputs.get("context", "")
        yield json.dumps({"type": "status", "data": "Deep Diving..."}) + "\n"
        yield json.dumps({"type": "thought", "data": f"Deep Dive: Analyzing '{topic}'..."}) + "\n"
        dd_out = await deep_dive_agent(topic, context)
        yield json.dumps({"type": "deep_dive", "data": dd_out}) + "\n"
        yield json.dumps({"type": "done", "data": "done"}) + "\n"
        return

    # =============================================================
    # STAGE 1 — Diagnostician (owns Rumelt's CRUX)
    # =============================================================
    yield json.dumps({"type": "status", "data": "Diagnosing the Crux..."}) + "\n"
    yield json.dumps({"type": "thought", "data": "Diagnostician: separating symptom from crux..."}) + "\n"

    diagnosis_out = await diagnostician_agent(
        situation, goal, constraints, document_context,
        key_numbers=key_numbers, success_metrics=success_metrics,
    )
    yield json.dumps({"type": "thought", "data": "Diagnostician: crux identified."}) + "\n"
    yield json.dumps({"type": "diagnostician", "data": diagnosis_out}) + "\n"

    # Extract crux markdown for any legacy string consumers
    crux_md = diagnosis_out.get("markdown", "") if isinstance(diagnosis_out, dict) else str(diagnosis_out)

    # =============================================================
    # STAGE 2 — Parallel analytical block
    # Enright (SPACE) + Frameworks (meta-tools) + Structure (systems) + Market Forces (PORTER dynamics)
    # =============================================================
    yield json.dumps({"type": "status", "data": "Running Enright + Frameworks + Structure + Market Forces in parallel..."}) + "\n"
    yield json.dumps({"type": "thought", "data": "Climbing Enright's levels + selecting frameworks + mapping structure + pressure-testing forces..."}) + "\n"

    selected_frameworks = inputs.get("frameworks", [])

    enright_task = asyncio.create_task(
        enright_agent(situation, goal, constraints, key_numbers,
                      success_metrics, document_context)
    )
    fw_task = asyncio.create_task(
        framework_agent(situation, goal, selected_frameworks, document_context)
    )
    struct_task = asyncio.create_task(
        structure_agent(situation, goal, constraints, document_context, key_numbers)
    )
    # Market Forces now runs in the parallel-4 block (needs Enright optionally)
    # We pass enright=None initially; it will update after enright completes but
    # asyncio.gather doesn't support mid-execution injection. Trade-off accepted.
    mf_task = asyncio.create_task(
        market_forces_agent(crux_md, document_context, enright_output=None,
                            situation=situation)
    )

    enright_out, fw_out, struct_out, mf_out = await asyncio.gather(
        enright_task, fw_task, struct_task, mf_task
    )

    yield json.dumps({"type": "enright", "data": enright_out}) + "\n"
    yield json.dumps({"type": "frameworks", "data": fw_out}) + "\n"
    yield json.dumps({"type": "structure", "data": struct_out}) + "\n"
    yield json.dumps({"type": "drivers", "data": mf_out}) + "\n"

    mf_md = mf_out.get("markdown", "") if isinstance(mf_out, dict) else str(mf_out)

    # =============================================================
    # STAGE 3 — Portfolio (Rumelt GUIDING POLICY, binding downstream_instruction)
    # =============================================================
    yield json.dumps({"type": "status", "data": "Generating Strategy Portfolio..."}) + "\n"
    yield json.dumps({"type": "thought", "data": "Portfolio: distinct options, head-to-head tradeoffs, kill criteria..."}) + "\n"

    portfolio_out = await strategy_portfolio_agent(
        situation, goal, constraints, crux_md, document_context,
        diagnosis_output=diagnosis_out, enright_output=enright_out,
        market_forces_output=mf_out, key_numbers=key_numbers,
    )
    yield json.dumps({"type": "portfolio", "data": portfolio_out}) + "\n"

    # =============================================================
    # STAGE 4 — Parallel execution-layer block
    # Financial + Tech + Human, all bound to Portfolio's chosen option
    # =============================================================
    yield json.dumps({"type": "status", "data": "Modeling Financials + Tech + Human in parallel..."}) + "\n"

    financial_task = asyncio.create_task(
        financial_agent(crux_md, mf_md, document_context, key_numbers,
                        goal=goal, success_metrics=success_metrics,
                        portfolio_output=portfolio_out)
    )
    tech_task = asyncio.create_task(
        tech_agent(ops_output=None, document_context=document_context,
                   portfolio_output=portfolio_out)
    )
    human_task = asyncio.create_task(
        human_factors_agent(ops_output=None, document_context=document_context,
                             portfolio_output=portfolio_out)
    )

    financial_out, tech_out, human_out = await asyncio.gather(
        financial_task, tech_task, human_task
    )

    yield json.dumps({"type": "financial", "data": financial_out}) + "\n"
    yield json.dumps({"type": "tech", "data": tech_out}) + "\n"
    yield json.dumps({"type": "human_factors", "data": human_out}) + "\n"

    # =============================================================
    # STAGE 5 — Ops (Rumelt COHERENT ACTIONS, reads Portfolio + Financial/Tech/Human)
    # =============================================================
    yield json.dumps({"type": "status", "data": "Designing Operations plan..."}) + "\n"

    ops_out = await ops_agent(
        crux_md, financial_out, document_context,
        portfolio_output=portfolio_out, tech_output=tech_out,
        human_output=human_out, diagnosis_output=diagnosis_out,
    )
    yield json.dumps({"type": "ops", "data": ops_out}) + "\n"

    # =============================================================
    # STAGE 6 — Red Team (attacks the whole recommendation)
    # =============================================================
    yield json.dumps({"type": "status", "data": "Red Team attacking the recommendation..."}) + "\n"
    yield json.dumps({"type": "thought", "data": "Red Team: targeting load-bearing assumptions directly..."}) + "\n"

    red_team_out = await red_team_agent_v2(
        diagnosis_out, mf_out, financial_out, ops_out, tech_out, human_out,
        document_context, portfolio=portfolio_out,
    )
    yield json.dumps({"type": "red_team", "data": red_team_out}) + "\n"

    # =============================================================
    # STAGE 7 — Synthesizer + Strategy Map (parallel)
    # Synthesizer is an ASSEMBLER — reads memo_contribution from every upstream.
    # =============================================================
    yield json.dumps({"type": "status", "data": "Synthesizing memo + mapping causal graph..."}) + "\n"
    yield json.dumps({"type": "thought", "data": "Synthesizer: assembling The Bet + running quality gates..."}) + "\n"

    synth_task = asyncio.create_task(
        synthesizer_agent(
            enright=enright_out, diagnosis=diagnosis_out,
            frameworks=fw_out, structure=struct_out,
            market_forces=mf_out, portfolio=portfolio_out,
            financial=financial_out, tech=tech_out, human=human_out,
            ops=ops_out, red_team=red_team_out,
            document_context=document_context,
        )
    )
    map_task = asyncio.create_task(
        map_agent(
            crux=diagnosis_out, drivers=mf_out, financial=financial_out,
            ops=ops_out, tech=tech_out, human=human_out, red_team=red_team_out,
            portfolio=portfolio_out, diagnosis=diagnosis_out, enright=enright_out,
        )
    )

    synth_out, map_out = await asyncio.gather(synth_task, map_task)

    # Synthesizer output is a dict; frontend expects a string OR dict for "synthesizer"
    # Emit the full dict so the new memo renderer can pick up quality gates.
    yield json.dumps({"type": "synthesizer", "data": synth_out}) + "\n"
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
