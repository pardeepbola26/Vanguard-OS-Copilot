// --- Core Logic ---
const $ = (id) => document.getElementById(id);
const bySel = (sel) => document.querySelectorAll(sel);

// State
const SESSIONS_KEY = "vanguard_sessions_titanium";
const LAST_RUN_KEY = "vanguard_last_run_outputs";
let currentMode = "advanced"; // Lite/Advanced toggle removed; always run full pipeline
let previousOutputsForDiff = null; // populated on refine to drive CHANGED badges

// --- Output pane registry: maps stream event → output element + agent label ---
const OUTPUT_PANES = {
    diagnostician:   { id: "diagnosisContent",    label: "Diagnostician" },
    frameworks:      { id: "frameworksContent",   label: "Framework Agent" },
    structure:       { id: "structureContent",    label: "Structure Agent" },
    portfolio:       { id: "portfolioContent",    label: "Strategy Portfolio" },
    drivers:         { id: "marketForcesContent", label: "Market Forces" },
    financial:       { id: "financialsContent",   label: "Financial Agent" },
    ops:             { id: "opsContent",          label: "Operations" },
    tech:            { id: "techContent",         label: "Technology" },
    human_factors:   { id: "humanContent",        label: "Human Factors" },
    red_team:        { id: "redTeamContent",      label: "Red Team" },
    synthesizer:     { id: "synthesizerContent",  label: "Synthesizer" },
};

function renderSkeleton(elementId, waitingOn) {
    const el = $(elementId);
    if (!el) return;
    el.innerHTML = `
      <div class="pane-loading">
        <div class="pane-loading-label">Waiting on ${waitingOn}</div>
        <div class="skeleton-bar"></div>
        <div class="skeleton-bar"></div>
        <div class="skeleton-bar"></div>
        <div class="skeleton-bar"></div>
      </div>
    `;
}

function primeAllPanesForLoading() {
    Object.values(OUTPUT_PANES).forEach(({ id, label }) => {
        renderSkeleton(id, label);
    });
}

function flashChanged(elementId) {
    const el = $(elementId);
    if (!el) return;
    el.classList.remove("diff-changed");
    // force reflow to restart animation
    void el.offsetWidth;
    el.classList.add("diff-changed");
    setTimeout(() => el.classList.remove("diff-changed"), 2400);
}

// Normalize content to a comparable string for diff detection
function contentFingerprint(value) {
    if (value == null) return "";
    if (typeof value === "string") return value.trim();
    try { return JSON.stringify(value); } catch { return String(value); }
}

// --- UI Interactions ---

// Collapsible Sidebar
const sidebar = document.querySelector(".sidebar");
const sidebarToggle = document.getElementById("sidebarToggleBtn");
if (sidebarToggle) {
    sidebarToggle.addEventListener("click", () => {
        sidebar.classList.toggle("collapsed");
    });
}

// Collapsible Input Panel
const inputPanel = document.querySelector(".input-panel");
const inputToggle = document.getElementById("inputToggleBtn");

function setInputsCollapsed(collapsed, auto) {
    if (!inputPanel) return;
    inputPanel.classList.toggle("collapsed", !!collapsed);
    inputPanel.classList.toggle("auto-collapsed", !!collapsed && !!auto);
    document.body.classList.toggle("inputs-auto-collapsed", !!collapsed && !!auto);
}

if (inputToggle) {
    inputToggle.addEventListener("click", () => {
        const nowCollapsed = !inputPanel.classList.contains("collapsed");
        setInputsCollapsed(nowCollapsed, false);
    });
}

// Inject floating "Edit inputs" flag — only shown via body.inputs-auto-collapsed
(() => {
    const flag = document.createElement("button");
    flag.className = "edit-inputs-flag";
    flag.type = "button";
    flag.innerHTML = "◀ Edit inputs";
    flag.title = "Expand input panel";
    flag.addEventListener("click", () => setInputsCollapsed(false, false));
    document.body.appendChild(flag);
})();

// Mirror sidebar collapsed state on <body> so the flag can adjust its anchor
(() => {
    const sb = document.querySelector(".sidebar");
    if (!sb) return;
    const sync = () => document.body.classList.toggle("sidebar-collapsed", sb.classList.contains("collapsed"));
    new MutationObserver(sync).observe(sb, { attributes: true, attributeFilter: ["class"] });
    sync();
})();

// Mode Toggle (Lite/Advanced) removed — pipeline always runs in Advanced.

// Tabs
const tabs = bySel(".tab-pill[data-tab]");
tabs.forEach(tab => {
    tab.addEventListener("click", () => {
        // Deactivate all
        tabs.forEach(t => t.classList.remove("active"));
        bySel(".output-pane").forEach(p => p.classList.remove("active"));

        // Activate clicked
        tab.classList.add("active");
        const targetId = "tab-" + tab.dataset.tab;
        const targetPane = $(targetId);
        if (targetPane) targetPane.classList.add("active");
    });
});

function addTerminalLine(text) {
    const term = $("agentTerminal");
    if (!term) return;

    const line = document.createElement("div");
    line.className = "terminal-line";
    line.textContent = text;
    term.appendChild(line);
    term.scrollTop = term.scrollHeight;
}

// Memo Mode Toggle
$("memoModeBtn").addEventListener("click", () => {
    document.body.classList.add("memo-mode");
});

$("memoExitBtn").addEventListener("click", () => {
    document.body.classList.remove("memo-mode");
});

// --- Deep Dive Context Menu ---
const ctxMenu = $("vanguardContextMenu");
let selectedTopic = "";

// 1. Intercept Right-Click
document.addEventListener("contextmenu", (e) => {
    // Only trigger on list items or paragraphs inside output-content
    if (e.target.closest(".output-content") && (e.target.tagName === "LI" || e.target.tagName === "P")) {
        e.preventDefault();
        selectedTopic = e.target.innerText;

        // Position menu
        ctxMenu.style.display = "block";
        ctxMenu.style.left = `${e.pageX}px`;
        ctxMenu.style.top = `${e.pageY}px`;
    } else {
        ctxMenu.style.display = "none";
    }
});

// 2. Hide menu on click elsewhere
document.addEventListener("click", () => {
    ctxMenu.style.display = "none";
});

// 3. Handle Deep Dive Click
$("ctxDeepDive").addEventListener("click", async () => {
    const modal = $("deepDiveModal");
    const contentDiv = $("deepDiveContent");

    modal.classList.add("active");
    contentDiv.textContent = "Initializing Deep Dive Agent...\nTarget: " + selectedTopic + "\n\n";

    // Gather context (full strategy text)
    const context = $("synthesizerContent").innerText + "\n" + $("portfolioContent").innerText;

    // Stream Request
    try {
        const response = await fetch("/vanguard/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                mode: "deep_dive",
                topic: selectedTopic,
                context: context,
                situation: "Deep Dive Context", // Required by schema
                goal: "Deep Dive Analysis"      // Required by schema
            })
        });

        if (!response.ok) {
            throw new Error(`Server Error: ${response.status} ${response.statusText}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            const lines = chunk.split("\n");

            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const msg = JSON.parse(line);
                    if (msg.type === "deep_dive") {
                        contentDiv.textContent = msg.data;
                    } else if (msg.type === "thought") {
                        addTerminalLine(msg.data);
                    }
                } catch (e) {
                    console.error("Stream parse error", e);
                }
            }
        }
    } catch (e) {
        contentDiv.textContent += "\nError: " + e.message;
    }
});

// Close Modal
$("closeModalBtn").addEventListener("click", () => {
    $("deepDiveModal").classList.remove("active");
});

// Frameworks Dropdown
const fwBtn = $("frameworksBtn");
const fwList = $("frameworksList");
fwBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    fwList.classList.toggle("open");
});
document.addEventListener("click", () => fwList.classList.remove("open"));
fwList.addEventListener("click", (e) => e.stopPropagation());

fwList.addEventListener("change", () => {
    const count = fwList.querySelectorAll("input:checked").length;
    fwBtn.querySelector("span").textContent = count > 0 ? `${count} Selected` : "Select...";
});

function getSelectedFrameworks() {
    return Array.from(fwList.querySelectorAll("input:checked")).map(cb => cb.value);
}

// Agents Dropdown
const agBtn = $("agentsBtn");
const agList = $("agentsList");
agBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    agList.classList.toggle("open");
});
document.addEventListener("click", () => agList.classList.remove("open"));
agList.addEventListener("click", (e) => e.stopPropagation());

agList.addEventListener("change", () => {
    const count = agList.querySelectorAll("input:checked").length;
    agBtn.querySelector("span").textContent = count === 8 ? "All Agents Active" : `${count} Agents Active`;
});

function getSelectedAgents() {
    return Array.from(agList.querySelectorAll("input:checked")).map(cb => cb.value);
}

// Global variable to store document session ID
let documentSessionId = null;

// --- File Upload Handler ---
const uploadBtn = $("uploadBtn");
const fileInput = $("fileUpload");
const uploadStatus = $("uploadStatus");

uploadBtn.addEventListener("click", () => {
    fileInput.click();
});

fileInput.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    uploadStatus.textContent = "Processing...";
    uploadBtn.disabled = true;

    try {
        const formData = new FormData();
        formData.append("file", file);

        const res = await fetch("http://127.0.0.1:8000/vanguard/upload", {
            method: "POST",
            body: formData
        });

        if (!res.ok) throw new Error("Upload failed");

        const data = await res.json();

        // Auto-fill Key Numbers field
        $("numbers").value = data.auto_fill_text;

        uploadStatus.textContent = `✓ ${data.filename} (${data.rows} rows, ${Object.keys(data.detected_metrics).length} metrics)`;
        uploadStatus.style.color = "var(--accent-primary)";

        console.log("Detected metrics:", data.detected_metrics);

    } catch (err) {
        console.error("Upload error:", err);
        uploadStatus.textContent = "⚠️ Upload failed";
        uploadStatus.style.color = "var(--accent-danger)";
    } finally {
        uploadBtn.disabled = false;
        fileInput.value = ""; // Reset input
    }
});

// --- Document Upload Handler (10-K, Reports) ---
const uploadDocBtn = $("uploadDocBtn");
const docInput = $("docUpload");
const docUploadStatus = $("docUploadStatus");

uploadDocBtn.addEventListener("click", () => {
    docInput.click();
});

docInput.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    docUploadStatus.textContent = "Processing document...";
    uploadDocBtn.disabled = true;

    try {
        const formData = new FormData();
        formData.append("file", file);

        const res = await fetch("http://127.0.0.1:8000/vanguard/upload-document", {
            method: "POST",
            body: formData
        });

        if (!res.ok) throw new Error("Document upload failed");

        const data = await res.json();
        const analysis = data.analysis;

        // Store session ID globally
        documentSessionId = data.session_id;
        console.log("Document session stored:", documentSessionId);

        // Auto-fill Situation field
        $("situation").value = analysis.auto_fill_text || "";

        // Show detailed analysis in console
        console.log("Document Analysis:", analysis);

        // Success feedback
        const companyName = analysis.company_name || "Company";
        docUploadStatus.textContent = `✓ ${companyName} analyzed (${data.extracted_chars} chars)`;
        docUploadStatus.style.color = "var(--accent-primary)";

        // Optional: Show analysis modal (if you want visual feedback)
        alert(`Document Analyzed!\n\nCompany: ${analysis.company_name}\n\n${analysis.overview}\n\nSituation field has been auto-filled.`);

    } catch (err) {
        console.error("Document upload error:", err);
        docUploadStatus.textContent = "⚠️ Upload failed";
        docUploadStatus.style.color = "var(--accent-danger)";
    } finally {
        uploadDocBtn.disabled = false;
        docInput.value = ""; // Reset input
    }
});

// --- Backend Integration (Streaming) ---

function updateAgentStatus(agentName, percent) {
    $("statusText").textContent = `${agentName}`;
    $("globalProgressBar").style.width = `${percent}%`;
}

async function callVanguard(isRefine = false) {
    console.log("Vanguard: Run button clicked.");

    try {
        const payload = {
            situation: $("situation").value,
            goal: $("goal").value,
            success_metrics: $("successMetrics").value,
            constraints: $("constraints").value,
            numbers: $("numbers").value,
            problem_type: $("problemType").value,
            mode: currentMode,
            frameworks: getSelectedFrameworks(),
            selected_agents: getSelectedAgents(),
            document_session_id: documentSessionId || ""  // Include session ID
        };

        if (!payload.situation && !payload.goal) {
            alert("Mission parameters missing: Enter Situation or Goal.");
            return;
        }

        // UI State: Running
        const runBtn = $("runBtn");
        const refineBtn = $("refineBtn");
        const agentDot = $("agentDot");
        const statusText = $("statusText");
        const progressBar = $("globalProgressBar");

        if (runBtn) runBtn.disabled = true;
        if (refineBtn) refineBtn.disabled = true;
        if (agentDot) agentDot.className = "agent-dot active";
        if (statusText) statusText.textContent = "Contacting HQ...";
        if (progressBar) progressBar.style.width = "5%";

        // Snapshot previous outputs before clearing — used for diff highlighting on refine
        previousOutputsForDiff = isRefine ? captureCurrentOutputs() : null;

        // Reset any in-flight streaming buffers from prior runs
        Object.keys(streamBuffers).forEach(k => delete streamBuffers[k]);

        // Prime every pane with a loading skeleton so the UI looks intentional while agents run
        primeAllPanesForLoading();

        const url = "http://127.0.0.1:8000/vanguard/stream";

        if (isRefine) {
            const refineNotes = $("refineNotes").value;
            const updatedNumbers = $("numbers").value;

            if (!refineNotes && !updatedNumbers) {
                alert("Please add commentary or update numbers in the 'Refine Notes' or 'Key Numbers' fields.");
                if (runBtn) runBtn.disabled = false;
                if (refineBtn) refineBtn.disabled = false;
                return;
            }

            // Send previous outputs + user refinement context
            payload.refine_context = {
                user_commentary: refineNotes,
                updated_numbers: updatedNumbers,
                previous_outputs: {
                    // Just send what we have, though V2 refine logic might need updates on backend too
                    // For now sending synthesizer content as context
                    synthesizer: $("synthesizerContent").textContent
                }
            };

            console.log("Refining with context:", payload.refine_context);
        }

        console.log("Vanguard: Sending request to", url);

        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (!response.ok) throw new Error("Server Error");

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let agentCount = 0;
        const totalAgents = 8;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop(); // Keep incomplete line in buffer

            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const msg = JSON.parse(line);
                    handleStreamMessage(msg);

                    // Update Progress (skip status messages for count)
                    if (msg.type !== "status") {
                        agentCount++;
                        const pct = Math.min(95, Math.round((agentCount / totalAgents) * 100));
                        if (statusText) statusText.textContent = msg.type.charAt(0).toUpperCase() + msg.type.slice(1) + " Agent";
                        if (progressBar) progressBar.style.width = `${pct}%`;
                    }

                } catch (e) {
                    console.error("JSON Parse Error:", e);
                }
            }
        }

        updateAgentStatus("Mission Complete", 100);
        let agentDotEl = $("agentDot");
        let statusTextEl = $("statusText");
        if (agentDotEl) {
            agentDotEl.className = "agent-dot"; // Stop pulse
            agentDotEl.style.background = "#30D158"; // Green
            agentDotEl.style.boxShadow = "0 0 20px rgba(48, 209, 88, 0.6)"; // Green glow
        }
        if (statusTextEl) statusTextEl.style.color = "#30D158";

        // Auto-collapse inputs after a successful run so outputs get max width
        if (inputPanel && !inputPanel.classList.contains("collapsed")) {
            setInputsCollapsed(true, true);
        }
        setTimeout(() => {
            const progressBar = $("globalProgressBar");
            if (progressBar) progressBar.style.width = "0%";
        }, 2000);

        // Save Session to History (localStorage + durable server DB)
        try {
            const financialsEl = $("financialsContent");
            const financialStructured = financialsEl && financialsEl.dataset.structured
                ? JSON.parse(financialsEl.dataset.structured)
                : null;

            const outputs = {
                synthesizer:   $("synthesizerContent").textContent,
                diagnosis:     $("diagnosisContent").textContent,
                frameworks:    $("frameworksContent").textContent,
                structure:     $("structureContent").textContent,
                portfolio:     $("portfolioContent").textContent,
                market_forces: $("marketForcesContent").textContent,
                financials:    financialStructured || $("financialsContent").textContent,
                ops:           $("opsContent").textContent,
                tech:          $("techContent").textContent,
                human:         $("humanContent").textContent,
                red_team:      $("redTeamContent").textContent
            };
            saveSession(payload, outputs);

            // Durable backup to server (fire-and-forget; failure is non-fatal)
            fetch("http://127.0.0.1:8000/vanguard/history", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ inputs: payload, outputs })
            }).catch(err => console.warn("Server history save failed:", err));
        } catch (storageErr) {
            console.warn("Failed to save session to history:", storageErr);
        }

    } catch (err) {
        console.error(err);
        $("statusText").textContent = "System Failure";
        $("agentDot").className = "agent-dot error";
        $("globalProgressBar").style.background = "#FF453A";
    } finally {
        $("runBtn").disabled = false;
        $("refineBtn").disabled = false;
    }
}

// Per-pane streaming buffers; accumulate raw markdown so we can re-render on each delta.
const streamBuffers = {};

function applyStreamDelta(paneId, delta) {
    const el = $(paneId);
    if (!el) return;

    // First delta: clear skeleton / placeholder
    if (!streamBuffers[paneId]) {
        streamBuffers[paneId] = "";
        el.innerHTML = "";
        el.classList.add("streaming-cursor");
    }
    streamBuffers[paneId] += delta;

    const buffer = streamBuffers[paneId];
    if (typeof marked !== 'undefined') {
        el.innerHTML = marked.parse(buffer);
        el.classList.add("streaming-cursor");
    } else {
        el.textContent = buffer;
    }
}

function finalizeStream(paneId) {
    const el = $(paneId);
    if (el) el.classList.remove("streaming-cursor");
    delete streamBuffers[paneId];
}

async function handleStreamMessage(msg) {
    const type = msg.type;
    const data = msg.data;

    if (type === "status") {
        $("statusText").textContent = data;
        return;
    }

    if (type === "thought") {
        addTerminalLine(data);
        return;
    }

    if (type === "delta") {
        applyStreamDelta(msg.pane, data);
        return;
    }

    // Map V2 agent outputs to UI elements
    if (type === "market_data") {
        // Store market data to append after diagnosis
        window.currentMarketData = data;
    } else if (type === "diagnostician") {
        // If we streamed deltas, the pane already has the running content; the final
        // event just confirms completion. Only overwrite if we have no buffer.
        if (!streamBuffers["diagnosisContent"] && $("diagnosisContent").innerText.trim() === "") {
            updateContent("diagnosisContent", data);
        }
        finalizeStream("diagnosisContent");

        // Append market data if it exists
        if (window.currentMarketData) {
            const diagnosisEl = $("diagnosisContent");
            if (diagnosisEl) {
                const marketCard = document.createElement("div");
                marketCard.className = "market-data-card";
                marketCard.innerHTML = marked.parse(window.currentMarketData);
                diagnosisEl.appendChild(marketCard);
            }
        }

        updateAgentStatus("Diagnosis Complete", 10);
    } else if (type === "frameworks") {
        if (data && typeof data === "object" && !Array.isArray(data)) {
            renderFrameworks(data);
        } else {
            updateContent("frameworksContent", data);
        }
        updateAgentStatus("Frameworks Applied", 20);
    } else if (type === "structure") {
        renderCytoscapeDiagram("mermaidContainer", data, "structureGraph");
        updateContent("structureContent", JSON.stringify(data, null, 2));
        updateAgentStatus("Decision Tree Built", 30);
    } else if (type === "portfolio") {
        updateContent("portfolioContent", data);
        updateAgentStatus("Strategy Options Generated", 40);
    } else if (type === "drivers") {
        if (data && typeof data === "object" && !Array.isArray(data)) {
            renderMarketForces(data);
        } else {
            updateContent("marketForcesContent", data);
        }
        updateAgentStatus("Market Analysis Complete", 40);
    } else if (type === "financial") {
        if (data && typeof data === "object") {
            renderFinancials(data);
        } else {
            updateContent("financialsContent", data);
        }
        updateAgentStatus("Financials Modeled", 50);
    } else if (type === "ops") {
        updateContent("opsContent", data);
        updateAgentStatus("Ops Plan Designed", 60);
    } else if (type === "tech") {
        updateContent("techContent", data);
    } else if (type === "human_factors") {
        updateContent("humanContent", data);
        updateAgentStatus("Tech & Human Factors Analyzed", 75);
    } else if (type === "red_team") {
        updateContent("redTeamContent", data);
        updateAgentStatus("Red Team Critique Received", 90);
    } else if (type === "strategy_map") {
        const smc = $("strategyMapContainer");
        if (smc) smc.style.display = "block";
        renderCytoscapeDiagram("strategyMapContainer", data, "strategyMapGraph");
    } else if (type === "synthesizer") {
        if (!streamBuffers["synthesizerContent"] && $("synthesizerContent").innerText.trim() === "") {
            updateContent("synthesizerContent", data);
        } else if (streamBuffers["synthesizerContent"]) {
            // Final authoritative render (with normalized markdown) replaces the streaming buffer
            updateContent("synthesizerContent", data);
        }
        finalizeStream("synthesizerContent");
        updateAgentStatus("Strategy Synthesized", 100);
    }
}

function renderFrameworks(data) {
    const host = $("frameworksContent");
    if (!host) return;

    try { host.dataset.structured = JSON.stringify(data); } catch {}

    if (previousOutputsForDiff) {
        const prev = previousOutputsForDiff["frameworksContent"];
        if (prev != null && contentFingerprint(prev) !== contentFingerprint(data)) {
            setTimeout(() => flashChanged("frameworksContent"), 40);
        }
    }

    const en = data.enright || {};
    const selected = Array.isArray(data.selected_frameworks) ? data.selected_frameworks : [];
    const confidence = data.confidence;

    const md = (txt) => (typeof marked !== "undefined" && txt ? marked.parse(txt) : (txt || ""));

    const enrightRow = (key, label, colorVar) => {
        if (!en[key]) return "";
        return `
          <div class="enright-row" style="--level-color: ${colorVar};">
            <div class="enright-label">${label}</div>
            <div class="enright-body">${md(en[key])}</div>
          </div>`;
    };

    host.innerHTML = `
      ${confidence != null ? `<div class="agent-confidence"><span>Confidence</span><strong>${confidence}</strong></div>` : ""}

      <div class="frameworks-section">
        <h2 class="frameworks-section-title">Enright's 5 Levels</h2>
        <div class="enright-ladder">
          ${enrightRow("supranational", "Supranational", "var(--accent-secondary)")}
          ${enrightRow("national",      "National",      "var(--accent-primary)")}
          ${enrightRow("cluster",       "Cluster",       "var(--accent-warning)")}
          ${enrightRow("industry",      "Industry",      "var(--accent-success)")}
          ${enrightRow("firm",          "Firm",          "var(--text-primary)")}
        </div>
      </div>

      ${selected.length ? `
        <div class="frameworks-section">
          <h2 class="frameworks-section-title">Auto-selected Frameworks</h2>
          <div class="framework-cards">
            ${selected.map(fw => `
              <div class="framework-card">
                <div class="framework-card-head">
                  <span class="framework-name">${fw.name || "Framework"}</span>
                  <span class="framework-why">${fw.why_chosen || ""}</span>
                </div>
                <div class="framework-analysis">${md(fw.analysis || "")}</div>
              </div>
            `).join("")}
          </div>
        </div>
      ` : ""}

      ${data.error ? `<div class="financials-error">${data.error}</div>` : ""}
    `;
}

function renderMarketForces(data) {
    const host = $("marketForcesContent");
    if (!host) return;

    try { host.dataset.structured = JSON.stringify(data); } catch {}

    if (previousOutputsForDiff) {
        const prev = previousOutputsForDiff["marketForcesContent"];
        if (prev != null && contentFingerprint(prev) !== contentFingerprint(data)) {
            setTimeout(() => flashChanged("marketForcesContent"), 40);
        }
    }

    const forces = Array.isArray(data.forces) ? data.forces : [];
    const mdet = data.most_determinative || {};

    const arrow = (d) => ({ worsening: "↑", improving: "↓", stable: "→" }[d] || "·");
    const arrowColor = (d) => ({ worsening: "var(--accent-danger)", improving: "var(--accent-success)", stable: "var(--text-secondary)" }[d] || "var(--text-tertiary)");
    const barColor = (intensity) => {
        if (intensity >= 4) return "var(--accent-danger)";
        if (intensity >= 3) return "var(--accent-warning)";
        return "var(--accent-success)";
    };
    const isDeterminative = (name) => name && mdet.force && name.toLowerCase() === mdet.force.toLowerCase();

    host.innerHTML = `
      ${data.confidence != null ? `<div class="agent-confidence"><span>Confidence</span><strong>${data.confidence}</strong></div>` : ""}

      <div class="forces-panel">
        <div class="forces-panel-title">Porter Forces (intensity 1-5, direction next 24 mo)</div>
        <div class="forces-list">
          ${forces.map(f => {
            const intensity = Number(f.intensity) || 0;
            const pct = Math.max(4, (intensity / 5) * 100);
            const determ = isDeterminative(f.name) ? "force-determinative" : "";
            return `
              <div class="force-row ${determ}">
                <div class="force-head">
                  <span class="force-name">${f.name || "?"}</span>
                  <span class="force-score">${intensity}/5</span>
                  <span class="force-arrow" style="color:${arrowColor(f.direction)}">${arrow(f.direction)}</span>
                </div>
                <div class="force-bar-track">
                  <div class="force-bar" style="width:${pct}%; background:${barColor(intensity)}"></div>
                </div>
                <div class="force-note">${f.note || ""}</div>
              </div>
            `;
          }).join("")}
        </div>
      </div>

      ${mdet.force ? `
        <div class="forces-callout">
          <div class="forces-callout-label">Most Determinative Force</div>
          <div class="forces-callout-body"><strong>${mdet.force}</strong> — ${mdet.why || ""}</div>
        </div>
      ` : ""}

      ${data.implications ? `<div class="forces-implications"><strong>Implications:</strong> ${data.implications}</div>` : ""}

      ${data.error ? `<div class="financials-error">${data.error}</div>` : ""}
    `;
}

function renderFinancials(data) {
    const host = $("financialsContent");
    if (!host) return;

    // Store structured payload on the node for diff detection + save/load
    try { host.dataset.structured = JSON.stringify(data); } catch {}

    // Diff-mode flash if this scenario payload changed since last run
    if (previousOutputsForDiff) {
        const prev = previousOutputsForDiff["financialsContent"];
        if (prev != null && contentFingerprint(prev) !== contentFingerprint(data)) {
            setTimeout(() => flashChanged("financialsContent"), 40);
        }
    }

    const fmtMoney = (v, { millions = true, decimals = 1 } = {}) => {
        if (v == null || Number.isNaN(Number(v))) return "—";
        const n = Number(v);
        const suffix = millions ? "M" : "";
        return `$${n.toFixed(decimals)}${suffix}`;
    };
    const fmtPct = (v, decimals = 1) => (v == null ? "—" : `${Number(v).toFixed(decimals)}%`);
    const fmtNum = (v, decimals = 2) => (v == null ? "—" : Number(v).toFixed(decimals));

    const scenarioCard = (name, label, sc) => {
        if (!sc) return "";
        const m = sc.metrics || {};
        return `
          <div class="scenario-card scenario-${name}">
            <div class="scenario-head">
              <span class="scenario-label">${label}</span>
              <span class="scenario-tag">${fmtMoney(sc.initial_investment)} invested</span>
            </div>
            <div class="scenario-metrics">
              <div class="metric"><div class="metric-label">NPV</div><div class="metric-value">${m.npv_str || "—"}</div></div>
              <div class="metric"><div class="metric-label">IRR</div><div class="metric-value">${m.irr_str || "—"}</div></div>
              <div class="metric"><div class="metric-label">Payback</div><div class="metric-value">${m.payback_str || "—"}</div></div>
              <div class="metric"><div class="metric-label">5yr ROI</div><div class="metric-value">${m.roi_str || "—"}</div></div>
            </div>
            <div class="scenario-row"><span>Revenue (Y5)</span><span>${fmtMoney(sc.revenue_y5)}</span></div>
            <div class="scenario-row"><span>EBITDA (Y5)</span><span>${fmtMoney(sc.ebitda_y5)}</span></div>
            <div class="scenario-row"><span>Discount rate</span><span>${sc.discount_rate != null ? fmtPct(sc.discount_rate * 100, 0) : "—"}</span></div>
            <div class="scenario-cashflows">
              ${(sc.cash_flows || []).map((cf, i) => `
                <div class="cf-bar-wrap"><div class="cf-bar" style="height:${Math.min(100, Math.max(6, Math.abs(cf) * 4))}%"></div><span>Y${i + 1}</span></div>
              `).join("")}
            </div>
          </div>`;
    };

    const scenarios = data.scenarios || {};
    const ue = data.unit_economics || {};

    const ltvCacHealth = ue.ltv_cac_ratio == null
        ? "neutral"
        : ue.ltv_cac_ratio >= 3 ? "good" : ue.ltv_cac_ratio >= 1.5 ? "warn" : "bad";
    const ro40Health = ue.rule_of_40 == null
        ? "neutral"
        : ue.rule_of_40 >= 40 ? "good" : ue.rule_of_40 >= 20 ? "warn" : "bad";
    const paybackHealth = ue.payback_months == null
        ? "neutral"
        : ue.payback_months <= 12 ? "good" : ue.payback_months <= 24 ? "warn" : "bad";

    host.innerHTML = `
      <div class="financials-grid">
        ${scenarioCard("bear", "Bear", scenarios.bear)}
        ${scenarioCard("base", "Base", scenarios.base)}
        ${scenarioCard("bull", "Bull", scenarios.bull)}
      </div>

      <div class="unit-econ-panel">
        <div class="unit-econ-title">Unit Economics</div>
        <div class="unit-econ-grid">
          <div class="ue-cell"><div class="ue-label">CAC</div><div class="ue-value">${fmtMoney(ue.cac, { millions: false, decimals: 0 })}</div></div>
          <div class="ue-cell"><div class="ue-label">LTV</div><div class="ue-value">${fmtMoney(ue.ltv, { millions: false, decimals: 0 })}</div></div>
          <div class="ue-cell ue-${ltvCacHealth}"><div class="ue-label">LTV / CAC</div><div class="ue-value">${fmtNum(ue.ltv_cac_ratio)}×</div></div>
          <div class="ue-cell ue-${paybackHealth}"><div class="ue-label">Payback</div><div class="ue-value">${ue.payback_months == null ? "—" : `${ue.payback_months} mo`}</div></div>
          <div class="ue-cell"><div class="ue-label">Gross margin</div><div class="ue-value">${ue.gross_margin == null ? "—" : fmtPct(ue.gross_margin * 100, 0)}</div></div>
          <div class="ue-cell"><div class="ue-label">Churn</div><div class="ue-value">${ue.churn_rate == null ? "—" : fmtPct(ue.churn_rate * 100, 1)}</div></div>
          <div class="ue-cell"><div class="ue-label">CAGR (base)</div><div class="ue-value">${fmtPct(ue.cagr, 1)}</div></div>
          <div class="ue-cell"><div class="ue-label">EBITDA margin</div><div class="ue-value">${fmtPct(ue.ebitda_margin, 1)}</div></div>
          <div class="ue-cell ue-${ro40Health}"><div class="ue-label">Rule of 40</div><div class="ue-value">${fmtPct(ue.rule_of_40, 1)}</div></div>
        </div>
      </div>

      ${data.narrative ? `<div class="financials-narrative">${(window.marked ? marked.parse(data.narrative) : data.narrative)}</div>` : ""}
      ${data.assumptions ? `<div class="financials-assumptions"><strong>Key assumptions:</strong> ${data.assumptions}</div>` : ""}
      ${data.error ? `<div class="financials-error">${data.error}</div>` : ""}
    `;
}

function makeDecisionTreeLayout(elements) {
    // Prefer dagre (hierarchical top-down with rank routing). Fall back to breadthfirst
    // if the dagre extension isn't registered — but we load cytoscape-dagre in index.html,
    // so dagre is the normal path.
    const hasDagre = typeof cytoscape !== "undefined" && cytoscape.prototype &&
                     (cytoscape.prototype.hasInitialised || true);
    try {
        // If dagre isn't registered, `cy.layout({name:'dagre'})` will throw at run() —
        // we handle that at the call site. Ship dagre config by default.
        return {
            name: 'dagre',
            rankDir: 'TB',      // top to bottom — proper decision tree
            align: undefined,
            nodeSep: 56,        // spacing between nodes in same rank (horizontal)
            edgeSep: 28,
            rankSep: 90,        // spacing between ranks (vertical)
            ranker: 'network-simplex',
            padding: 40,
            fit: true,
            animate: true,
            animationDuration: 350,
        };
    } catch (e) {
        return {
            name: 'breadthfirst',
            directed: true,
            padding: 40,
            spacingFactor: 1.5,
            roots: elements.filter(el => el.data && (el.data.type === 'root' || el.data.type === 'goal'))
                           .map(el => '#' + el.data.id),
            fit: true,
            animate: true,
            animationDuration: 350,
        };
    }
}

async function renderCytoscapeDiagram(containerId, jsonData, graphId) {
    const container = $(containerId);
    if (!container) return;

    try {
        const parsed = typeof jsonData === 'string' ? JSON.parse(jsonData) : jsonData;

        // Accept either {nodes:[], edges:[]} or a flat elements array
        let elements = [];
        if (Array.isArray(parsed)) {
            elements = parsed;
        } else {
            elements = [...(parsed.nodes || []), ...(parsed.edges || [])];
        }

        // Precompute ROI heat: scale 0-10 → color band
        const roiToTint = (roi) => {
            if (roi == null) return null;
            const v = Math.max(0, Math.min(10, Number(roi)));
            // amber → sage (cold → hot). Interpolate in HSL-ish space.
            const t = v / 10;
            // Low = muted tan (#7a6d57), High = sage (#8ca882)
            const r = Math.round(122 + (140 - 122) * t);
            const g = Math.round(109 + (168 - 109) * t);
            const b = Math.round(87  + (130 - 87)  * t);
            return `rgb(${r},${g},${b})`;
        };

        // Attach a `.roiColor` data field client-side for edges/nodes
        elements.forEach(el => {
            if (el.data && typeof el.data.roi === "number") {
                el.data._roiColor = roiToTint(el.data.roi);
            }
        });

        container.innerHTML = "";
        container.classList.add("vg-cytoscape-host");

        const cy = cytoscape({
            container: container,
            elements: elements,
            wheelSensitivity: 0.25,
            style: [
                {
                    selector: 'node',
                    style: {
                        'background-color': 'rgba(255, 245, 225, 0.03)',
                        'background-opacity': 1,
                        'border-width': 1.5,
                        'border-color': '#6b5f49',   // warm muted
                        'label': 'data(label)',
                        'color': '#efe8d8',
                        'font-family': "'Inter', -apple-system, sans-serif",
                        'font-size': 12,
                        'font-weight': 500,
                        'text-valign': 'center',
                        'text-halign': 'center',
                        'width': 'label',
                        'height': 'label',
                        'padding': '14px',
                        'shape': 'round-rectangle',
                        'text-wrap': 'wrap',
                        'text-max-width': '140px',
                        'text-margin-y': 0,
                        'transition-property': 'border-color, background-color, border-width',
                        'transition-duration': 200,
                    }
                },
                // ROOT — the goal, serif italic, warm orange glow
                {
                    selector: 'node[type="root"], node[type="goal"]',
                    style: {
                        'shape': 'round-rectangle',
                        'background-color': 'rgba(217, 119, 87, 0.14)',
                        'border-color': '#d97757',
                        'border-width': 2.5,
                        'color': '#fff6ea',
                        'font-family': "'Fraunces', Georgia, serif",
                        'font-weight': 500,
                        'font-style': 'italic',
                        'font-size': 15,
                        'padding': '18px',
                        'text-max-width': '180px',
                    }
                },
                // BRANCH (driver)
                {
                    selector: 'node[type="branch"]',
                    style: {
                        'background-color': 'rgba(212, 167, 74, 0.1)',
                        'border-color': '#d4a74a',
                        'border-width': 1.8,
                        'color': '#f5e9c6',
                        'font-weight': 500,
                    }
                },
                // LEAF (action) — colored by ROI if present
                {
                    selector: 'node[type="leaf"]',
                    style: {
                        'background-color': 'rgba(140, 168, 130, 0.08)',
                        'border-color': '#8ca882',
                        'border-width': 1.5,
                        'shape': 'round-rectangle',
                    }
                },
                {
                    selector: 'node[type="leaf"][?_roiColor]',
                    style: {
                        'border-color': 'data(_roiColor)',
                    }
                },
                // ROI badge: high-ROI leaves get a thicker border + soft glow
                {
                    selector: 'node[type="leaf"][roi >= 7]',
                    style: {
                        'border-width': 2.5,
                        'background-color': 'rgba(140, 168, 130, 0.18)',
                    }
                },
                // OUTCOME (used by strategy map)
                {
                    selector: 'node[type="outcome"]',
                    style: {
                        'shape': 'ellipse',
                        'background-color': 'rgba(198, 162, 122, 0.1)',
                        'border-color': '#c6a27a',
                    }
                },
                // ACTION (used by strategy map)
                {
                    selector: 'node[type="action"]',
                    style: {
                        'shape': 'round-rectangle',
                    }
                },

                // EDGES — base
                {
                    selector: 'edge',
                    style: {
                        'width': 1.5,
                        'line-color': 'rgba(198, 162, 122, 0.45)',
                        'target-arrow-color': 'rgba(198, 162, 122, 0.7)',
                        'target-arrow-shape': 'triangle',
                        'arrow-scale': 0.9,
                        'curve-style': 'bezier',
                        'line-cap': 'round',
                    }
                },
                // Weighted edges — thickness scales with weight (1-10)
                {
                    selector: 'edge[weight > 0]',
                    style: {
                        'width': 'mapData(weight, 1, 10, 1, 5)',
                        'line-color': 'rgba(217, 119, 87, 0.35)',
                        'target-arrow-color': '#d97757',
                    }
                },
                {
                    selector: 'edge[weight >= 7]',
                    style: {
                        'line-color': 'rgba(217, 119, 87, 0.7)',
                    }
                },
                // Map feedback-loop polarity
                {
                    selector: 'edge[polarity="balancing"]',
                    style: {
                        'line-style': 'dashed',
                        'line-color': 'rgba(200, 87, 77, 0.5)',
                        'target-arrow-color': '#c8574d',
                    }
                },
                // Hover + selected states
                {
                    selector: 'node:selected',
                    style: {
                        'border-color': '#d97757',
                        'border-width': 3,
                    }
                },
                {
                    selector: 'edge:selected',
                    style: {
                        'line-color': '#d97757',
                        'target-arrow-color': '#d97757',
                        'width': 3,
                    }
                },
            ],
            layout: makeDecisionTreeLayout(elements)
        });

        // Re-apply the layout AFTER Cytoscape renders (sometimes dagre needs a nudge)
        setTimeout(() => {
            try {
                cy.layout(makeDecisionTreeLayout(elements)).run();
                cy.fit(null, 30);
            } catch (e) { console.warn("layout re-run failed:", e); }
        }, 50);

        // Hover highlight: dim non-neighbors
        cy.on('mouseover', 'node', (e) => {
            const node = e.target;
            cy.elements().addClass('faded');
            node.removeClass('faded');
            node.neighborhood().removeClass('faded');
        });
        cy.on('mouseout', 'node', () => cy.elements().removeClass('faded'));

        cy.style()
          .selector('.faded').style({ 'opacity': 0.25 })
          .update();

        // Attach the cy instance to the container for later (zoom buttons, etc.)
        container._cy = cy;

        // Inject small on-canvas legend (once per container)
        if (!container.querySelector('.vg-graph-legend')) {
            const legend = document.createElement('div');
            legend.className = 'vg-graph-legend';
            legend.innerHTML = `
              <div class="vg-legend-row"><span class="vg-legend-dot vg-legend-goal"></span>Goal</div>
              <div class="vg-legend-row"><span class="vg-legend-dot vg-legend-branch"></span>Driver</div>
              <div class="vg-legend-row"><span class="vg-legend-dot vg-legend-leaf"></span>Action</div>
              <div class="vg-legend-row vg-legend-hint">Line weight = impact</div>
            `;
            container.appendChild(legend);
        }

    } catch (e) {
        console.error("Cytoscape Render Error:", e);
        container.innerHTML = `<div style="color: var(--accent-danger); padding: 16px; font-family: var(--font-mono); font-size: 12px;">
            <strong>Visualization Error</strong><br>
            <span style="opacity: 0.8;">${e.message}</span>
        </div>`;
    }
}

function updateContent(elementId, text) {
    const el = $(elementId);
    if (!el) return;

    // Clear any skeleton / placeholder
    if (el.querySelector(".pane-loading") || el.textContent === "Generating...") {
        el.innerHTML = "";
    }

    if (typeof marked !== 'undefined' && typeof text === "string") {
        el.innerHTML = marked.parse(text);
    } else {
        el.textContent = typeof text === "string" ? text : JSON.stringify(text);
    }

    // Diff-mode: if we have a previous-run snapshot, check whether this pane changed
    if (previousOutputsForDiff) {
        const prev = previousOutputsForDiff[elementId];
        if (prev != null && contentFingerprint(prev) !== contentFingerprint(text)) {
            flashChanged(elementId);
        }
    }
}

function captureCurrentOutputs() {
    const snap = {};
    Object.values(OUTPUT_PANES).forEach(({ id }) => {
        const el = $(id);
        if (!el) return;
        // For financials (structured), use the stored data if available
        if (id === "financialsContent" && el.dataset.structured) {
            snap[id] = el.dataset.structured;
        } else {
            snap[id] = el.textContent || "";
        }
    });
    return snap;
}

$("runBtn").addEventListener("click", () => callVanguard(false));

$("refineBtn").addEventListener("click", () => callVanguard(true));

// --- History Management ---
async function loadHistory() {
    try {
        const historyEl = $("sessionList"); // Fixed ID
        if (!historyEl) {
            console.log("History element not found.");
            return;
        }

        // Load from localStorage instead of server
        const sessionsJSON = localStorage.getItem(SESSIONS_KEY);
        if (!sessionsJSON) {
            historyEl.innerHTML = '<div style="padding: 12px; color: var(--text-tertiary); font-size: 13px;">No history yet</div>';
            return;
        }

        const sessions = JSON.parse(sessionsJSON);
        historyEl.innerHTML = "";

        // Display most recent first
        sessions.reverse().forEach((session, idx) => {
            const div = document.createElement("div");
            div.className = "nav-item";
            const timestamp = new Date(session.ts).toLocaleDateString();
            div.innerHTML = `
            <span style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${session.inputs.goal || session.inputs.situation.substring(0, 30) + "..."}</span>
            <span style="font-size: 9px; color: var(--text-tertiary); margin-left: auto;">${timestamp}</span>
          `;
            div.onclick = () => loadMission(sessions.length - 1 - idx); // Reverse index
            historyEl.appendChild(div);
        });
    } catch (e) { console.error("History Load Error:", e); }
}

async function loadMission(index) {
    try {
        const sessionsJSON = localStorage.getItem(SESSIONS_KEY);
        if (!sessionsJSON) return;

        const sessions = JSON.parse(sessionsJSON);
        const session = sessions[index];
        if (!session) return;

        // Restore Inputs
        $("situation").value = session.inputs.situation || "";
        $("goal").value = session.inputs.goal || "";
        $("constraints").value = session.inputs.constraints || "";
        $("numbers").value = session.inputs.numbers || "";
        $("problemType").value = session.inputs.problem_type || "general";

        // Restore Outputs — financials may be either a structured object (new) or text (legacy)
        const outs = session.outputs;
        if (outs) {
            previousOutputsForDiff = null; // don't flash CHANGED on load
            updateContent("synthesizerContent", outs.synthesizer || "");
            updateContent("diagnosisContent", outs.diagnosis || "");
            updateContent("frameworksContent", outs.frameworks || "");
            updateContent("structureContent", outs.structure || "");
            updateContent("portfolioContent", outs.portfolio || "");
            updateContent("marketForcesContent", outs.market_forces || "");
            if (outs.financials && typeof outs.financials === "object") {
                renderFinancials(outs.financials);
            } else {
                updateContent("financialsContent", outs.financials || "");
            }
            updateContent("opsContent", outs.ops || "");
            updateContent("techContent", outs.tech || "");
            updateContent("humanContent", outs.human || "");
            updateContent("redTeamContent", outs.red_team || "");
        }
    } catch (e) { console.error("Mission Load Error:", e); }
}

// Visual Feedback
updateAgentStatus("System Ready", 100);

function saveSession(inputs, outputs) {
    const sessions = JSON.parse(localStorage.getItem(SESSIONS_KEY) || "[]");
    const label = inputs.situation.substring(0, 30) + "...";
    sessions.unshift({ label, inputs, outputs, ts: new Date().toISOString() });
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions.slice(0, 20)));
    loadHistory(); // Refresh list
}

// --- Presets ---
$("presets").addEventListener("change", (e) => {
    const v = e.target.value;
    if (v === "telco") {
        $("situation").value = "Regional telecom operator with flat revenue growth and rising churn in the SMB segment.";
        $("goal").value = "Restore profitable growth to 8–10% in 18–24 months.";
        $("constraints").value = "Capex envelope of ~$2M, no major layoffs.";
        $("problemType").value = "profitability";
    } else if (v === "saas") {
        $("situation").value = "US-based B2B SaaS company (~$40M ARR) evaluating expansion into the EU.";
        $("goal").value = "Enter 1–2 priority EU markets and reach $8–10M incremental ARR.";
        $("constraints").value = "Limited brand awareness, strict data privacy.";
        $("problemType").value = "market_entry";
    }
});

// --- Download All Handler ---
$("downloadAllBtn").addEventListener("click", () => {
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const filename = `Vanguard_Strategy_${timestamp}.md`;

    let content = `# Vanguard Strategy Report\nGenerated: ${new Date().toLocaleString()}\n\n`;

    // Inputs
    content += `## Mission Parameters\n`;
    content += `**Situation:** ${$("situation").value}\n`;
    content += `**Goal:** ${$("goal").value}\n`;
    content += `**Constraints:** ${$("constraints").value}\n\n`;

    // Outputs
    const sections = [
        { title: "Diagnosis", id: "diagnosisContent" },
        { title: "Strategic Frameworks", id: "frameworksContent" },
        { title: "Decision Tree (Structure)", id: "structureContent" },
        { title: "Strategy Portfolio", id: "portfolioContent" },
        { title: "Market Forces", id: "marketForcesContent" },
        { title: "Financial Model", id: "financialsContent" },
        { title: "Operations Plan", id: "opsContent" },
        { title: "Tech & Human Factors", id: "techContent" }, // Combined usually, but techContent has data
        { title: "Human Factors", id: "humanContent" },
        { title: "Red Team Critique", id: "redTeamContent" },
        { title: "Executive Synthesis", id: "synthesizerContent" }
    ];

    sections.forEach(sec => {
        const el = $(sec.id);
        if (el && el.textContent && el.textContent !== "Generating...") {
            content += `## ${sec.title}\n\n${el.textContent}\n\n---\n\n`;
        }
    });

    // Trigger Download
    const blob = new Blob([content], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
});

// Init
loadHistory();
// ===================================================================
// COMPANY PRESETS - Add to beginning of vanguard.js
// ===================================================================

// Company presets for quick testing
const COMPANY_PRESETS = {
    tesla_india: {
        situation: "Tesla faces declining market share in developed markets and needs new growth vectors. India represents a $10B EV market growing at 40% annually, but lacks charging infrastructure and has high import tariffs.",
        goal: "Establish Tesla as the premium EV leader in India with 15% market share by 2028",
        constraints: "Must build local manufacturing to avoid 100% import duties. Cannot compromise on brand premium positioning. Limited initial capital allocation of $1.5B."
    },
    apple_vision: {
        situation: "Vision Pro 1.0 sold 400K units at $3,500 but faces adoption barriers due to price and limited content ecosystem. Meta Quest 3 dominates at $500 price point with 20M units sold.",
        goal: "Launch Vision Pro 2 to capture 10M units sold and establish spatial computing as mainstream category",
        constraints: "Manufacturing cost floor is $1,200. Must maintain 40% gross margin. Cannot alienate existing Vision Pro 1.0 customers."
    },
    netflix_gaming: {
        situation: "Netflix faces subscriber growth plateau in saturated markets. Gaming represents untapped $200B market. Current mobile games have 50M MAU but low engagement (avg 10 min/day).",
        goal: "Build gaming into a $5B revenue stream with 100M MAU by 2027",
        constraints: "No additional subscription fee allowed. Must leverage existing IP. Gaming team budget capped at $500M annually."
    }
};

// Add keyboard shortcut for presets
document.addEventListener('keydown', (e) => {
    // Ctrl/Cmd + Shift + P = Open Preset Menu
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'P') {
        e.preventDefault();
        showPresetMenu();
    }

    // Ctrl/Cmd + Shift + C = Clear History
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'C') {
        e.preventDefault();
        if (confirm("Clear all mission history?")) {
            localStorage.removeItem("strategyHistory");
            alert("History cleared!");
        }
    }
});

function showPresetMenu() {
    const choice = prompt(`Quick Test Presets:
1 - Tesla India Entry
2 - Apple Vision Pro 2
3 - Netflix Gaming

Enter number (or 'clear' to clear history):`);

    if (choice === 'clear') {
        if (confirm("Clear all mission history?")) {
            localStorage.removeItem("strategyHistory");
            alert("History cleared!");
        }
        return;
    }

    const presetMap = {
        '1': 'tesla_india',
        '2': 'apple_vision',
        '3': 'netflix_gaming'
    };

    const presetKey = presetMap[choice];
    if (presetKey && COMPANY_PRESETS[presetKey]) {
        const preset = COMPANY_PRESETS[presetKey];
        document.getElementById('situation').value = preset.situation;
        document.getElementById('goal').value = preset.goal;
        document.getElementById('constraints').value = preset.constraints;
        alert("Preset loaded!");
    }
}

// ===================================================================
// THEME TOGGLE
// ===================================================================

const themeToggle = document.getElementById('themeToggle');
const sunIcon = themeToggle.querySelector('.sun-icon');
const moonIcon = themeToggle.querySelector('.moon-icon');

// Load saved theme or default to dark
const savedTheme = localStorage.getItem('theme') || 'dark';
document.documentElement.setAttribute('data-theme', savedTheme);
updateThemeIcons(savedTheme);

themeToggle.addEventListener('click', () => {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    updateThemeIcons(newTheme);
});

function updateThemeIcons(theme) {
    if (theme === 'light') {
        sunIcon.style.display = 'none';
        moonIcon.style.display = 'block';
    } else {
        sunIcon.style.display = 'block';
        moonIcon.style.display = 'none';
    }
}

// ===================================================================
// MARKET MAPPING MODULE
// ===================================================================

const marketMapOverlay = $('marketMapOverlay');
const marketMapBtn = $('marketMapBtn');
const closeMarketMapBtn = $('closeMarketMapBtn');
const generateMarketMapBtn = $('generateMarketMapBtn');

// Open/Close Market Mapping
if (marketMapBtn) {
    marketMapBtn.addEventListener('click', () => {
        marketMapOverlay.classList.add('active');
    });
}

if (closeMarketMapBtn) {
    closeMarketMapBtn.addEventListener('click', () => {
        marketMapOverlay.classList.remove('active');
    });
}

// Generate Market Map
if (generateMarketMapBtn) {
    generateMarketMapBtn.addEventListener('click', async () => {
        const industry = $('marketIndustry').value.trim();
        const geo_scope = $('marketGeo').value.trim() || 'Global';
        const segments = $('marketSegments').value.trim() || 'All';

        if (!industry) {
            alert('Please enter an industry or market');
            return;
        }

        // Show loading
        $('marketMapOutput').style.display = 'none';
        $('marketLoading').style.display = 'block';

        try {
            const response = await fetch('http://127.0.0.1:8000/market_map', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ industry, geo_scope, segments })
            });

            const data = await response.json();

            // Hide loading
            $('marketLoading').style.display = 'none';
            $('marketMapOutput').style.display = 'block';

            // Render data
            renderCompetitors(data.competitors || []);
            renderSegments(data.segments || []);
            renderValueChain(data.value_chain || []);
            renderThreats(data.threats || []);
            renderScorecard(data.scorecard || {});

        } catch (error) {
            console.error('Market map error:', error);
            $('marketLoading').style.display = 'none';
            alert('Failed to generate market map. Please try again.');
        }
    });
}

// Rendering Functions

function renderCompetitors(competitors) {
    const grid = $('competitorGrid');
    grid.innerHTML = '';

    competitors.forEach(comp => {
        const archetype = (comp.archetype || 'Challenger').toLowerCase();
        const card = document.createElement('div');
        card.className = `competitor-card ${archetype}`;

        card.innerHTML = `
            <div class="competitor-name">${comp.name}</div>
            <div class="competitor-archetype">${comp.archetype}</div>
            <div class="competitor-positioning">${comp.positioning}</div>
            
            <div class="competitor-metrics">
                <div class="metric">
                    <div class="metric-label">Market Share</div>
                    <div class="metric-bar">
                        <div class="metric-fill" style="width: ${comp.market_share}%"></div>
                    </div>
                </div>
                <div class="metric">
                    <div class="metric-label">Differentiation</div>
                    <div class="metric-bar">
                        <div class="metric-fill" style="width: ${comp.differentiation}%"></div>
                    </div>
                </div>
            </div>
            
            <div class="competitor-details">
                <h4>Strengths</h4>
                <ul>
                    ${(comp.strengths || []).map(s => `<li>${s}</li>`).join('')}
                </ul>
                <h4>Weaknesses</h4>
                <ul>
                    ${(comp.weaknesses || []).map(w => `<li>${w}</li>`).join('')}
                </ul>
            </div>
        `;

        grid.appendChild(card);
    });
}

function renderSegments(segments) {
    const grid = $('segmentGrid');
    grid.innerHTML = '';

    segments.forEach(seg => {
        const growth = (seg.growth || 'Medium').toLowerCase();
        const card = document.createElement('div');
        card.className = 'segment-card';

        card.innerHTML = `
            <div class="segment-label">${seg.label}</div>
            <div class="segment-growth ${growth}">${seg.growth}</div>
            <div class="segment-info">
                <p><strong>Needs:</strong> ${seg.needs}</p>
                <p><strong>Pain Points:</strong> ${seg.pain_points}</p>
            </div>
            <div class="segment-attractiveness">
                <div class="metric-label">Attractiveness</div>
                <div class="attractiveness-bar">
                    <div class="attractiveness-fill" style="width: ${seg.attractiveness}%"></div>
                </div>
            </div>
        `;

        grid.appendChild(card);
    });
}

function renderValueChain(valueChain) {
    const container = $('valueChain');
    container.innerHTML = '';

    valueChain.forEach(stage => {
        const stageEl = document.createElement('div');
        stageEl.className = 'value-stage';

        stageEl.innerHTML = `
            <div class="stage-name">${stage.stage}</div>
            <div class="stage-description">${stage.description}</div>
            <div class="stage-section">
                <strong>Friction:</strong>
                <div style="color: var(--text-secondary)">${stage.friction}</div>
            </div>
            <div class="stage-section">
                <strong>Opportunities:</strong>
                <div style="color: var(--text-secondary)">${stage.opportunities}</div>
            </div>
        `;

        container.appendChild(stageEl);
    });
}

function renderThreats(threats) {
    const panel = $('threatsPanel');
    panel.innerHTML = '';

    const threatIcons = {
        'new entrants': '🚀',
        'substitutes': '🔄',
        'tech shift': '🤖',
        'regulation': '⚖️',
        'supply chain': '🔧'
    };

    threats.forEach(threat => {
        const severity = (threat.severity || 'Medium').toLowerCase();
        const typeKey = threat.type.toLowerCase();
        const icon = threatIcons[typeKey] || '⚠️';

        const card = document.createElement('div');
        card.className = 'threat-card';

        card.innerHTML = `
            <div class="threat-header">
                <div class="threat-type">${icon} ${threat.type}</div>
                <div class="threat-severity ${severity}">${threat.severity}</div>
            </div>
            <div class="threat-description">${threat.description}</div>
        `;

        panel.appendChild(card);
    });
}

function renderScorecard(scorecard) {
    const container = $('scorecard');
    container.innerHTML = '';

    const metrics = [
        { label: 'Profit Pool Growth', key: 'profit_pool_growth' },
        { label: 'Competitive Intensity', key: 'competitive_intensity' },
        { label: 'Switching Costs', key: 'switching_costs' },
        { label: 'Barriers to Entry', key: 'barriers_to_entry' },
        { label: 'Supplier Power', key: 'supplier_power' },
        { label: 'Buyer Power', key: 'buyer_power' }
    ];

    const grid = document.createElement('div');
    grid.className = 'scorecard-grid';

    metrics.forEach(metric => {
        const value = scorecard[metric.key] || 0;
        const color = value >= 70 ? '#34C759' : value >= 40 ? '#FFD60A' : '#FF453A';

        const metricEl = document.createElement('div');
        metricEl.className = 'scorecard-metric';
        metricEl.innerHTML = `
            <div class="scorecard-label">${metric.label}</div>
            <div class="scorecard-value">${value}</div>
            <div class="scorecard-bar">
                <div class="scorecard-fill" style="width: ${value}%; background: ${color}"></div>
            </div>
        `;
        grid.appendChild(metricEl);
    });

    container.appendChild(grid);

    // Overall Attractiveness
    const overall = scorecard.overall_attractiveness || 0;
    const interpretation = overall >= 70 ? 'Highly Attractive' :
        overall >= 50 ? 'Moderately Attractive' :
            overall >= 30 ? 'Challenging' : 'Unattractive';

    const overallEl = document.createElement('div');
    overallEl.className = 'scorecard-overall';
    overallEl.innerHTML = `
        <div class="overall-label">Overall Market Attractiveness</div>
        <div class="overall-score">${overall}</div>
        <div class="overall-interpretation">${interpretation}</div>
    `;
    container.appendChild(overallEl);
}

/* ============================================================
   UI OVERHAUL — Preset Hero, Memo Masthead, Copy Buttons,
   Phase Stepper Progress, Command Palette (Cmd+K)
   ============================================================ */

// ---- Preset mission cards ----
const PRESETS = {
    telco: {
        situation: "Regional telecom operator with flat revenue growth and rising churn in a mature, competitive market.",
        goal: "Restore profitable growth to 8–10% in 18–24 months.",
        constraints: "Capex envelope of ~$2M, no major layoffs.",
        successMetrics: "ARPU up 5%, churn down 2pp, EBITDA margin above 25%.",
        problemType: "turnaround",
    },
    saas: {
        situation: "B2B SaaS scaling from SMB ($100-500/mo ACV) into mid-market ($25-100K ACV); current product wasn't built for enterprise buyers.",
        goal: "Scale ARR from $25M to $100M in 24 months while preserving net-retention above 115%.",
        constraints: "Don't abandon SMB. Engineering headcount flat this year.",
        successMetrics: "$100M ARR, NRR ≥115%, mid-market >40% of new ACV.",
        problemType: "product",
    },
    ops: {
        situation: "Mature services company with bloated operating costs, legacy process debt, and margins compressing 2-3pp year-over-year.",
        goal: "Cut operating costs 15-20% in 12 months without degrading CSAT.",
        constraints: "No offshore outsourcing. Keep customer-facing headcount intact.",
        successMetrics: "OpEx down 15-20%, CSAT flat or up, cycle time down 25%.",
        problemType: "turnaround",
    },
};

function loadPreset(name) {
    const p = PRESETS[name];
    if (!p) return;
    if ($("situation"))      $("situation").value = p.situation;
    if ($("goal"))           $("goal").value = p.goal;
    if ($("constraints"))    $("constraints").value = p.constraints;
    if ($("successMetrics")) $("successMetrics").value = p.successMetrics;
    if ($("problemType"))    $("problemType").value = p.problemType;
    // Focus the run button so user can just hit Enter
    const runBtn = $("runBtn");
    if (runBtn) runBtn.focus();
}

document.addEventListener("click", (e) => {
    const card = e.target.closest(".preset-card");
    if (card && card.dataset.preset) loadPreset(card.dataset.preset);
});

function hidePresetHero() {
    const el = $("presetHero");
    if (el) el.classList.remove("visible");
}

// ---- Memo masthead (byline under the synthesizer output) ----
function renderMemoMasthead() {
    const masthead = $("memoMasthead");
    if (!masthead) return;
    const goal = ($("goal") && $("goal").value || "").trim();
    if (!goal) return;

    const title = goal.length > 80 ? goal.slice(0, 77) + "…" : goal;
    const now = new Date();
    const date = now.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
    const time = now.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });

    masthead.innerHTML = `
      <div class="memo-eyebrow">Vanguard Strategy Memo</div>
      <h1 class="memo-title">${title}</h1>
      <div class="memo-byline">
        <span>${date}</span>
        <span>${time}</span>
        <span>Multi-Agent Analysis</span>
      </div>
    `;
    masthead.style.display = "block";
}

// ---- "The Bet" callout — extract first ## The Bet section as a hero callout ----
function upgradeBetCallout() {
    const host = $("synthesizerContent");
    if (!host) return;

    // Find the H2 "The Bet" (case-insensitive)
    const headings = host.querySelectorAll("h2");
    for (const h of headings) {
        const text = (h.textContent || "").trim().toLowerCase();
        if (text === "the bet" || text.startsWith("the bet")) {
            // Grab the next paragraph as the bet body
            const p = h.nextElementSibling;
            if (!p || p.tagName !== "P") break;
            const betText = p.textContent.trim();

            const callout = document.createElement("div");
            callout.className = "bet-callout";
            callout.innerHTML = `
              <div class="bet-eyebrow">The Bet</div>
              <div class="bet-body">${betText}</div>
            `;
            // Insert callout BEFORE the masthead content — i.e. before synthesizerContent
            host.parentElement.insertBefore(callout, host);
            // Remove the original H2 + p so it's not duplicated
            h.remove();
            p.remove();
            break;
        }
    }
}

// Hook memo polish into existing synthesizer completion + preset-hero hide
(function wireSynthCompletionPolish() {
    const origHandle = window.handleStreamMessage;
    // can't wrap directly — handleStreamMessage isn't on window. Use a MutationObserver instead.
    const host = $("synthesizerContent");
    if (!host) return;

    const obs = new MutationObserver(() => {
        const el = $("synthesizerContent");
        if (!el) return;
        const hasContent = el.textContent && el.textContent.trim().length > 50;
        if (hasContent) hidePresetHero();
    });
    obs.observe(host, { childList: true, subtree: true, characterData: true });
})();

// After the mission completes, polish the memo (called from the existing flow)
function finalizeSynthesizerMemo() {
    hidePresetHero();
    renderMemoMasthead();
    upgradeBetCallout();
    // Re-init Lucide for any new icons
    if (window.lucide) try { lucide.createIcons(); } catch {}
}

// Hook into the existing "synthesizer" event — patch finalizeStream to also run memo polish
(function patchFinalizeStreamForMemo() {
    const _finalize = window.finalizeStream;
    if (typeof _finalize !== "function") return;
    window.finalizeStream = function (paneId) {
        _finalize(paneId);
        if (paneId === "synthesizerContent") {
            // Defer so the final `updateContent` has already swapped in normalized markdown
            setTimeout(finalizeSynthesizerMemo, 30);
        }
    };
})();

// ---- Copy-to-clipboard on headings ----
function ensureCopyButtonsOnHeadings(root) {
    const host = root || document;
    const headings = host.querySelectorAll(".output-pane h1, .output-pane h2, .output-pane h3");
    headings.forEach(h => {
        if (h.querySelector(".copy-heading-btn")) return;
        const btn = document.createElement("button");
        btn.className = "copy-heading-btn";
        btn.type = "button";
        btn.title = "Copy section";
        btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            // Copy heading text + all following siblings until next heading of same-or-higher level
            const levelNum = parseInt(h.tagName.substring(1), 10);
            const chunks = [h.textContent.replace(/\s+$/, "")];
            let sib = h.nextElementSibling;
            while (sib) {
                if (/^H[1-6]$/.test(sib.tagName)) {
                    const sLvl = parseInt(sib.tagName.substring(1), 10);
                    if (sLvl <= levelNum) break;
                }
                chunks.push(sib.innerText);
                sib = sib.nextElementSibling;
            }
            const text = chunks.join("\n\n");
            navigator.clipboard.writeText(text).then(() => {
                btn.classList.add("copied");
                btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>';
                setTimeout(() => {
                    btn.classList.remove("copied");
                    btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
                }, 1400);
            });
        });
        h.appendChild(btn);
    });
}

// Observe output panes for new headings and attach copy buttons
(function wireCopyButtons() {
    const panes = document.querySelectorAll(".output-pane");
    panes.forEach(pane => {
        ensureCopyButtonsOnHeadings(pane);
        new MutationObserver(() => ensureCopyButtonsOnHeadings(pane)).observe(pane, { childList: true, subtree: true });
    });
})();

// ---- Command Palette (Cmd+K / Ctrl+K) ----
const CMDK_COMMANDS = [
    // Run / create
    { id: "run", label: "Run Vanguard Mission", group: "Actions", icon: "play", action: () => $("runBtn") && $("runBtn").click() },
    { id: "refine", label: "Refine Current Strategy", group: "Actions", icon: "repeat", action: () => $("refineBtn") && $("refineBtn").click() },
    { id: "new", label: "New Strategy (reload)", group: "Actions", icon: "plus", action: () => location.reload() },
    // Jump to tabs
    { id: "jump-synth", label: "Jump to Synthesizer", group: "Navigate", icon: "file-text", action: () => activateTab("synthesizer") },
    { id: "jump-diag", label: "Jump to Diagnosis", group: "Navigate", icon: "crosshair", action: () => activateTab("diagnosis") },
    { id: "jump-mf", label: "Jump to Market Forces", group: "Navigate", icon: "activity", action: () => activateTab("market_forces") },
    { id: "jump-fw", label: "Jump to Frameworks", group: "Navigate", icon: "layers", action: () => activateTab("frameworks") },
    { id: "jump-tree", label: "Jump to Decision Tree", group: "Navigate", icon: "git-branch", action: () => activateTab("structure") },
    { id: "jump-portfolio", label: "Jump to Portfolio", group: "Navigate", icon: "briefcase", action: () => activateTab("portfolio") },
    { id: "jump-fin", label: "Jump to Financials", group: "Navigate", icon: "trending-up", action: () => activateTab("financials") },
    { id: "jump-ops", label: "Jump to Ops Plan", group: "Navigate", icon: "list-checks", action: () => activateTab("ops") },
    { id: "jump-tech", label: "Jump to Tech & Human", group: "Navigate", icon: "users", action: () => activateTab("tech_human") },
    { id: "jump-red", label: "Jump to Red Team", group: "Navigate", icon: "shield-alert", action: () => activateTab("red_team") },
    // Presets
    { id: "preset-telco", label: "Load preset: Telecom Turnaround", group: "Presets", icon: "radio-tower", action: () => loadPreset("telco") },
    { id: "preset-saas", label: "Load preset: SaaS Expansion", group: "Presets", icon: "box", action: () => loadPreset("saas") },
    { id: "preset-ops", label: "Load preset: Cost Takeout", group: "Presets", icon: "scissors", action: () => loadPreset("ops") },
    // UI
    { id: "toggle-inputs", label: "Toggle Input Panel", group: "View", icon: "panel-left", action: () => setInputsCollapsed(!(inputPanel && inputPanel.classList.contains("collapsed")), false) },
    { id: "toggle-theme", label: "Toggle Light/Dark Theme", group: "View", icon: "sun", action: () => document.body.classList.toggle("light-mode") },
    { id: "toggle-memo", label: "Toggle Memo Mode", group: "View", icon: "book-open", action: () => $("memoModeBtn") && $("memoModeBtn").click() },
];

function activateTab(tabId) {
    const tab = document.querySelector(`.tab-pill[data-tab="${tabId}"]`);
    if (tab) tab.click();
}

const cmdkBackdrop = $("cmdkBackdrop");
const cmdkInput = $("cmdkInput");
const cmdkList = $("cmdkList");
let cmdkSelectedIndex = 0;
let cmdkFiltered = CMDK_COMMANDS.slice();

function renderCmdkList(items) {
    if (!cmdkList) return;
    cmdkFiltered = items;
    cmdkSelectedIndex = 0;
    if (!items.length) {
        cmdkList.innerHTML = '<div class="cmdk-empty">No matches</div>';
        return;
    }
    const byGroup = {};
    items.forEach(it => {
        (byGroup[it.group || "Commands"] = byGroup[it.group || "Commands"] || []).push(it);
    });
    const parts = [];
    Object.keys(byGroup).forEach(group => {
        parts.push(`<div class="cmdk-group-label">${group}</div>`);
        byGroup[group].forEach((it) => {
            const idx = items.indexOf(it);
            parts.push(`
              <div class="cmdk-item ${idx === 0 ? 'selected' : ''}" data-index="${idx}">
                <i data-lucide="${it.icon || 'arrow-right'}"></i>
                <span>${it.label}</span>
              </div>
            `);
        });
    });
    cmdkList.innerHTML = parts.join("");
    if (window.lucide) try { lucide.createIcons(); } catch {}
}

function openCmdk() {
    if (!cmdkBackdrop) return;
    cmdkBackdrop.classList.add("open");
    if (cmdkInput) { cmdkInput.value = ""; cmdkInput.focus(); }
    renderCmdkList(CMDK_COMMANDS);
}

function closeCmdk() {
    if (cmdkBackdrop) cmdkBackdrop.classList.remove("open");
}

function updateCmdkSelection() {
    const items = cmdkList ? cmdkList.querySelectorAll(".cmdk-item") : [];
    items.forEach((el, i) => el.classList.toggle("selected", i === cmdkSelectedIndex));
    const sel = items[cmdkSelectedIndex];
    if (sel) sel.scrollIntoView({ block: "nearest" });
}

function runCmdkSelected() {
    const cmd = cmdkFiltered[cmdkSelectedIndex];
    if (!cmd) return;
    closeCmdk();
    setTimeout(() => cmd.action && cmd.action(), 40);
}

if (cmdkInput) {
    cmdkInput.addEventListener("input", () => {
        const q = cmdkInput.value.trim().toLowerCase();
        if (!q) return renderCmdkList(CMDK_COMMANDS);
        const filtered = CMDK_COMMANDS.filter(c => c.label.toLowerCase().includes(q) || (c.group || "").toLowerCase().includes(q));
        renderCmdkList(filtered);
    });
    cmdkInput.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") { e.preventDefault(); cmdkSelectedIndex = Math.min(cmdkFiltered.length - 1, cmdkSelectedIndex + 1); updateCmdkSelection(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); cmdkSelectedIndex = Math.max(0, cmdkSelectedIndex - 1); updateCmdkSelection(); }
        else if (e.key === "Enter") { e.preventDefault(); runCmdkSelected(); }
        else if (e.key === "Escape") { closeCmdk(); }
    });
}

document.addEventListener("click", (e) => {
    if (cmdkBackdrop && cmdkBackdrop.classList.contains("open") && e.target === cmdkBackdrop) closeCmdk();
    const item = e.target.closest(".cmdk-item");
    if (item && item.dataset.index != null) {
        cmdkSelectedIndex = Number(item.dataset.index);
        runCmdkSelected();
    }
});

document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        openCmdk();
    } else if (e.key === "Escape" && cmdkBackdrop && cmdkBackdrop.classList.contains("open")) {
        closeCmdk();
    }
});

const openCmdkBtn = $("openCmdkBtn");
if (openCmdkBtn) openCmdkBtn.addEventListener("click", openCmdk);

// ---- Phase stepper progress (color bar fills as agents complete per phase) ----
const PHASE_PROGRESS = {
    diagnosis: { total: 3, done: 0, selector: ".nav-group.diagnosis-group" },
    strategy:  { total: 3, done: 0, selector: ".nav-group.strategy-group" },
    execution: { total: 4, done: 0, selector: ".nav-group.execution-group" },
};

const AGENT_TO_PHASE = {
    diagnostician: "diagnosis",
    drivers: "diagnosis",
    synthesizer: "diagnosis", // synthesizer is cross-cutting; counted in diagnosis for completion feel
    frameworks: "strategy",
    structure: "strategy",
    portfolio: "strategy",
    financial: "execution",
    ops: "execution",
    tech: "execution",
    human_factors: "execution",
    red_team: "execution",
};

function bumpPhaseForAgent(agentType) {
    const phase = AGENT_TO_PHASE[agentType];
    if (!phase) return;
    const p = PHASE_PROGRESS[phase];
    p.done = Math.min(p.total, p.done + 1);
    const el = document.querySelector(p.selector);
    if (!el) return;
    const pct = Math.round((p.done / p.total) * 100);
    el.classList.add("phase-active");
    el.style.setProperty("--phase-progress", pct + "%");
}

function resetPhaseProgress() {
    Object.values(PHASE_PROGRESS).forEach(p => { p.done = 0; });
    document.querySelectorAll(".nav-group").forEach(el => {
        el.classList.remove("phase-active");
        el.style.removeProperty("--phase-progress");
    });
}

// Monkey-patch the existing handleStreamMessage to track phase progress
(function wirePhaseProgress() {
    const orig = window.handleStreamMessage;
    if (typeof orig !== "function") {
        // Not on window — attach via event delegation on the stream receive instead
        // Fallback: patch applyStreamDelta / global finalizeStream? Skip silently — progress is additive polish.
        return;
    }
    window.handleStreamMessage = function (msg) {
        if (msg && msg.type && AGENT_TO_PHASE[msg.type]) bumpPhaseForAgent(msg.type);
        return orig(msg);
    };
})();

// Reset phase progress when a new run starts (piggyback on run button click)
(function wirePhaseReset() {
    const runBtn = $("runBtn");
    if (runBtn) runBtn.addEventListener("click", () => { resetPhaseProgress(); hidePresetHero(); });
    const refineBtn = $("refineBtn");
    if (refineBtn) refineBtn.addEventListener("click", () => resetPhaseProgress());
})();

// Initialize Lucide icons exactly ONCE on load. Do NOT re-init via a MutationObserver:
// lucide.createIcons() mutates the DOM (replaces <i> with <svg>), which would re-fire
// the observer → infinite loop → browser hangs. Dynamic icons must call createIcons()
// explicitly at their own render site (which is already done in renderCmdkList).
(function initLucideOnce() {
    if (!window.lucide) return;
    try { lucide.createIcons(); } catch (e) { console.warn("lucide init failed:", e); }
})();
