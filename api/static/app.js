/* Task Lab. All task, prompt, and model text is escaped. */
"use strict";

const $ = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const attr = escapeHTML;
const path = encodeURIComponent;
const terminal = new Set(["completed", "passed", "failed", "error", "timeout", "budget_exhausted", "cancelled", "skipped", "interrupted", "validation_failed"]);
const renderedHTML = new WeakMap();
// "/" shows the frozen golden set; "/window" shows the best stretch of 20 consecutive probed tasks.
const VIEW = { "/window": "window", "/fidelity": "fidelity", "/stats": "stats" }[location.pathname.replace(/\/+$/, "")] || "golden";
const state = {
  examples: [], jobs: [], health: null, job: null, run: null,
  selectedJob: null, selectedRun: null, tab: "turns", refreshing: false,
  launching: false, runTicket: 0, jobTicket: 0, detailKey: null,
  expanded: new Map(), scrolls: new Map(),
  phase2: null, phase2Busy: false, search: null, searchBusy: false,
  shipped: [], history: null, historyKey: null,
};

const labels = {
  nop: "Nop", oracle: "Oracle", evaluation: "Model attempts",
  queued: "Queued", pending: "Pending", running: "Running", passed: "Passed",
  failed: "Failed", error: "Execution error", timeout: "Timed out", completed: "Complete",
  validation_failed: "Controls failed", cancelled: "Cancelled", cancelling: "Cancelling",
  skipped: "Skipped", interrupted: "Interrupted", learnable: "In target range",
  validation: "Checking task", validating: "Checking task", controls: "Running controls",
  budget_exhausted: "Budget exhausted", checkpoint: "Checkpoint", generating: "Generating",
  reviewing: "Semantic review", evaluating: "Evaluating", updating_prompt: "Revising prompt",
  pending_validation: "Pending validation", candidate: "Candidate · unvalidated",
  invalid: "Invalid task", invalid_task: "Invalid task",
};
function label(value) {
  return labels[value] || String(value || "Pending").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
function badge(status, text) {
  const safeClass = /^[a-z_]+$/.test(status || "") ? status : "pending";
  return `<span class="status ${safeClass}">${escapeHTML(text || label(status))}</span>`;
}
function showError(id, message) {
  $(id).textContent = message || "";
  $(id).classList.toggle("hidden", !message);
}
function readable(value) {
  if (typeof value === "string") return value;
  if (value == null) return "";
  return JSON.stringify(value, null, 2);
}
function clockText(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function elapsed(run) {
  let seconds = run.duration_sec;
  if (seconds == null && run.started_at) {
    seconds = (new Date(run.completed_at || Date.now()) - new Date(run.started_at)) / 1000;
  }
  if (!Number.isFinite(Number(seconds)) || seconds == null) return "Not started";
  seconds = Math.max(0, Math.floor(Number(seconds)));
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
}
async function request(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options, headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } });
  const body = await response.text();
  let data;
  try { data = body ? JSON.parse(body) : {}; } catch { data = null; }
  if (!response.ok) {
    const detail = data?.detail ?? data?.error ?? data?.message;
    throw new Error(detail ? readable(detail) : `Request failed (${response.status}): ${body.slice(0, 350) || response.statusText}`);
  }
  if (data === null) throw new Error("The server returned an unexpected response. Check that Task Lab is running.");
  return data;
}

// Only replace changed regions, and retain disclosures and terminal scroll offsets.
function setHTML(element, html, key = element.id) {
  if (renderedHTML.get(element) === html) return;
  captureView(element, key);
  element.innerHTML = html;
  renderedHTML.set(element, html);
  for (const item of element.querySelectorAll("details[data-detail]")) {
    const saved = state.expanded.get(`${key}:${item.dataset.detail}`);
    if (saved !== undefined) item.open = saved;
  }
  for (const item of element.querySelectorAll("[data-scroll]")) {
    const saved = state.scrolls.get(`${key}:${item.dataset.scroll}`);
    if (saved) { item.scrollTop = saved.top; item.scrollLeft = saved.left; }
  }
}
function captureView(element, key) {
  for (const item of element.querySelectorAll("details[data-detail]")) {
    state.expanded.set(`${key}:${item.dataset.detail}`, item.open);
  }
  for (const item of element.querySelectorAll("[data-scroll]")) {
    state.scrolls.set(`${key}:${item.dataset.scroll}`, { top: item.scrollTop, left: item.scrollLeft });
  }
}
function currentPaneKey() { return `${state.selectedJob}:${state.selectedRun}:${state.tab}`; }
function saveCurrentPane() {
  if ($("run-content") && $("run-content").dataset.viewKey) captureView($("run-content"), $("run-content").dataset.viewKey);
}

function dockerReady(health) {
  const docker = health?.docker;
  if (typeof docker === "boolean") return docker;
  if (typeof docker === "string") return ["ok", "ready", "running", "available"].includes(docker.toLowerCase());
  return !!(docker?.available ?? docker?.ready ?? docker?.running ?? (docker?.status === "ok"));
}
function updateLaunchButtons() {
  const selected = !!$("example").value;
  const ready = !!state.health && dockerReady(state.health) && state.health.worker_alive !== false;
  $("controls-button").disabled = state.launching || !selected || !ready;
  $("start-button").disabled = state.launching || !selected || !ready || !state.health?.key_present;
  $("example").disabled = state.launching || !state.examples.length;
}
function renderHealth(health) {
  state.health = health;
  const ready = dockerReady(health);
  const healthy = health.status === "ok" || health.status === "ready";
  $("health-label").textContent = healthy ? "Runner connected" : "Runner needs attention";
  $("health-dot").className = `dot ${healthy && ready ? "green" : "red"}`;
  $("docker-label").textContent = ready ? "Docker ready" : "Docker unavailable";
  $("key-label").textContent = health.key_present ? "API key configured" : "API key missing";
  $("capacity-count").textContent = `${health.active_runs ?? 0} / ${health.max_concurrency ?? 5}`;
  $("model-label").textContent = `${health.model || "GLM-5.3-Flash"} · ${health.reasoning_effort || "high"} reasoning`;
  $("freshness").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;
  updateLaunchButtons();
}
function renderExamples() {
  const selected = $("example").value;
  const option = (example) => `<option value="${attr(example.id)}">${escapeHTML(example.name || example.id)}</option>`;
  const groups = [...new Set(state.examples.map((example) => example.group || "Examples"))];
  $("example").innerHTML = '<option value="">Choose a task…</option>' + groups.map((group) => `<optgroup label="${attr(group)}">${state.examples.filter((example) => (example.group || "Examples") === group).map(option).join("")}</optgroup>`).join("");
  if (state.examples.some((example) => example.id === selected)) $("example").value = selected;
  updateLaunchButtons();
}
// Newly accepted generated tasks appear without a page reload.
async function refreshExamples() {
  try {
    const data = await request("/api/examples");
    const next = data.examples || [];
    if (JSON.stringify(next.map((e) => e.id)) === JSON.stringify(state.examples.map((e) => e.id))) return;
    state.examples = next;
    renderExamples();
  } catch { /* The main refresh loop reports connection errors. */ }
}
function renderPreview() {
  const example = state.examples.find((item) => item.id === $("example").value);
  // The API renders Markdown with raw HTML and image embedding disabled.
  $("task-readme").innerHTML = example?.readme_html || `<p>${example ? "This task does not include a README." : "Choose an example to read its overview."}</p>`;
  $("task-instruction").textContent = example?.instruction || example?.description || "Choose an example to read its instructions.";
  updateLaunchButtons();
  showError("form-error", "");
}
function jobBadge(job) {
  if (job.status === "completed" && job.mode === "controls") {
    const controls = (job.runs || []).filter((run) => run.kind !== "evaluation");
    if (controls.length === 2 && controls.every((run) => run.passed === true || run.status === "passed")) return badge("passed", "Controls passed");
  }
  if (searchJob(job) && job.status === "completed" && job.summary?.learnable === true) return badge("learnable", acceptedJob(job) ? "Verified learnable" : "In target range · audit pending");
  if (!demoJob(job) && job.status === "completed" && job.summary?.learnable === true) return badge("learnable");
  return badge(job.status);
}
// Shipped tasks: the probe that shipped each one, then every later run of the same task.
function measurementName(m) {
  if (m.label) return m.label;
  if (m.shipping) return "Shipping probe";
  if (m.source === "stability-check") return "Re-run";
  if (m.source === "ui") return "Run from this page";
  return "Later probe";
}
function measurementBadge(m) {
  const s = m.summary || {};
  const passes = s.passes ?? 0;
  if (m.status === "completed" && (s.valid_runs ?? 0) === 5) {
    if (passes >= 1 && passes <= 3) return badge("learnable", `${passes}/5 · in band`);
    return badge(passes === 0 ? "failed" : "passed", `${passes}/5 · ${passes === 0 ? "below" : "above"} band`);
  }
  if (passes >= 4) return badge("passed", `${passes}/${s.valid_runs ?? passes} · above band (stopped early)`);
  if (m.status === "completed") return badge("pending", `${passes} passed · ${s.valid_runs ?? 0} of 5 valid`);
  if (m.status === "running") return badge("running", `Running · ${passes} passed so far`);
  return badge(m.status);
}
function consistency(task) {
  const done = task.measurements.filter((m) => m.status === "completed" && (m.summary?.valid_runs ?? 0) === 5);
  if (!done.length) return "";
  const inBand = done.filter((m) => m.summary.passes >= 1 && m.summary.passes <= 3).length;
  return `${inBand} of ${done.length} measurement${done.length === 1 ? "" : "s"} in 1–3/5`;
}
function renderList() {
  const tasks = state.shipped || [];
  $("job-count").textContent = tasks.length;
  const scrollTop = $("job-list").scrollTop;
  setHTML($("job-list"), tasks.length ? tasks.map((task) => {
    const active = task.measurements.some((m) => m.id === state.selectedJob);
    const origin = task.tag
      ? `<span class="status ${attr(task.tagClass || "pending")}">${escapeHTML(task.tag)}</span>`
      : task.outcome
      ? (task.outcome === "shipped" ? '<span class="status learnable">shipped</span>' : `<span class="status pending">${escapeHTML(label(task.outcome))}</span>`)
      : task.derived_from
      ? `<span class="prompt-tag" title="GLM-5.1 edited ${attr(task.derived_from.slug)} (0/5) to remove unstated rules">eased variant</span>`
      : `<span class="prompt-tag" title="Prompt version that authored this task">prompt ${escapeHTML(task.prompt_version || "?")}</span>`;
    const runs = task.measurements.map((m) => `<button class="measurement ${m.id === state.selectedJob ? "active" : ""}" data-job="${attr(m.id)}" aria-current="${m.id === state.selectedJob}"><span class="measurement-name">${escapeHTML(measurementName(m))}<span class="submission-date">${escapeHTML(clockText(m.created_at))}</span></span>${measurementBadge(m)}</button>`).join("");
    const steady = consistency(task);
    // Collapsed by default: a task's run history shows only when it is opened (the selected task opens itself).
    return `<details class="shipped-task ${active ? "active" : ""}" data-detail="task-${attr(task.slug)}" ${active ? "open" : ""}><summary><span class="shipped-heading"><strong>${escapeHTML(task.name || task.slug)}</strong>${origin}</span>${steady ? `<span class="shipped-consistency">${escapeHTML(steady)}</span>` : ""}</summary><p class="shipped-description">${escapeHTML(task.description)}</p><div class="measurements">${runs || '<p class="help">No probe record found.</p>'}</div></details>`;
  }).join("") : '<p class="list-empty">No shipped tasks yet.</p>');
  // Open the task that holds the selected run once when the selection moves to it; later toggles are the user's.
  const activeSlug = tasks.find((task) => task.measurements.some((m) => m.id === state.selectedJob))?.slug;
  if (activeSlug && activeSlug !== state.openedTask) {
    const item = [...$("job-list").querySelectorAll("details.shipped-task")].find((d) => d.dataset.detail === `task-${activeSlug}`);
    if (item) item.open = true;
    state.openedTask = activeSlug;
  }
  $("job-list").scrollTop = scrollTop;
}
function repeatTask(job) {
  if (state.examples.some((example) => example.id === job.task_id)) return job.task_id;
  const generated = `gen-${String(job.task_name || job.task_id || "").replace(/^gen-/, "")}`;
  return state.examples.some((example) => example.id === generated) ? generated : null;
}

// Bottom of the page: how the authoring prompt and the strategy library evolved.
async function refreshHistory() {
  try {
    const data = await request("/api/history");
    const key = JSON.stringify([(data.prompt_versions || []).map((v) => [v.version, v.stats?.probes, v.shipped]), data.strategy_version]);
    if (key === state.historyKey) return;
    state.historyKey = key;
    state.history = data;
    renderHistory();
  } catch (error) { setHTML($("prompt-content"), `<div class="alert">${escapeHTML(error.message)}</div>`); }
}
function outcomeChips(stats) {
  if (!stats?.probes) return '<span class="chip">no first probes measured</span>';
  const rate = Math.round(100 * stats.in_band / stats.probes);
  return `<span class="chip">${stats.probes} first probes</span><span class="chip band">${stats.in_band} in band · ${rate}%</span><span class="chip">${stats.too_easy} too easy</span><span class="chip">${stats.too_hard} too hard</span>`;
}
function renderPromptHistory(data) {
  const versions = data.prompt_versions || [];
  if (!versions.length) return '<p class="help">No prompt versions recorded.</p>';
  // Collapsed until opened; opening a version shows its full prompt, with what changed underneath.
  const rows = versions.map((v, index) => {
    const lineage = v.base_of ? `same instructions as ${v.base_of}` : "new instructions";
    const changes = (v.changes || []).slice(0, 12).map((c) => `<li>${escapeHTML(String(c).slice(0, 500))}</li>`).join("");
    const more = (v.changes || []).length > 12 ? `<li class="help">…and ${v.changes.length - 12} more</li>` : "";
    return `<details class="batch" data-detail="pv-${attr(v.version)}"><summary><span><strong>${escapeHTML(v.version)}</strong>${index === 0 ? ' <span class="prompt-tag">current</span>' : ""}${v.shipped ? ` <span class="status learnable">${escapeHTML(v.shipped)} shipped</span>` : ""}<span class="batch-date">${escapeHTML(clockText(v.created_at))} · ${escapeHTML(lineage)}</span></span><span class="chips">${outcomeChips(v.stats)}</span></summary><div class="batch-body"><div class="episode-label">Authoring prompt</div><pre class="prompt-text" data-scroll="pv-text-${attr(v.version)}">${escapeHTML(v.base_prompt || "")}</pre>${changes ? `<details class="evidence" data-detail="pv-changes-${attr(v.version)}"><summary>What changed in ${escapeHTML(v.version)}</summary><ul class="prompt-changes">${changes}${more}</ul></details>` : ""}</div></details>`;
  }).join("");
  return `<details class="history-collapse" data-detail="prompt-versions"><summary><strong>${versions.length} prompt versions</strong><span class="batch-date">current ${escapeHTML(versions[0].version)} · open to browse, then open a version to read its prompt</span></summary><div class="history-versions">${rows}</div></details>`;
}
function renderStrategyHistory(data) {
  const levers = data.strategies || [];
  const kinds = { hard: "Harder", easy: "Easier", avoid: "Avoid" };
  const rate = (st) => (st.stats?.runs ? `${st.stats.passes}/${st.stats.runs} runs passed · ${st.stats.tasks} tasks` : "not measured yet");
  const rows = levers.map((st) => `<tr class="${st.status === "retired" ? "retired" : ""}"><td><details data-detail="lever-${attr(st.id)}"><summary><strong>${escapeHTML(st.id)}</strong> ${escapeHTML(st.name)}${st.status === "retired" ? " · retired" : ""}</summary><p class="lever-how">${escapeHTML(st.how)}</p>${st.previous_how?.length ? `<p class="help">Revised ${st.previous_how.length} time${st.previous_how.length === 1 ? "" : "s"} by the strategist.</p>` : ""}</details></td><td><span class="chip lever-${attr(st.kind)}">${escapeHTML(kinds[st.kind] || st.kind)}</span></td><td>${escapeHTML(rate(st))}</td></tr>`).join("");
  const log = (data.strategy_history || []).map((h) => `<li><span class="history-version">v${escapeHTML(h.version)}</span><span class="history-at">${escapeHTML(h.at)}</span>${escapeHTML(h.note)}</li>`).join("");
  return `<div class="search-stats"><span><strong>v${escapeHTML(data.strategy_version ?? "—")}</strong> library version</span> · <span><strong>${levers.length}</strong> levers</span> · <span>updated ${escapeHTML(data.strategy_updated || "—")}</span></div><table class="test-table lever-table"><thead><tr><th scope="col">LEVER</th><th scope="col">KIND</th><th scope="col">MEASURED</th></tr></thead><tbody>${rows}</tbody></table><details class="evidence" data-detail="strategy-log" open><summary>Change log, newest first (${(data.strategy_history || []).length} versions)</summary><ol class="history-log">${log}</ol></details>`;
}
function renderHistory() {
  if (!state.history) return;
  setHTML($("prompt-content"), renderPromptHistory(state.history), "prompt-history");
  setHTML($("strategy-content"), renderStrategyHistory(state.history), "strategy-history");
}
function runLabel(run) {
  if (run.kind === "oracle") return "Oracle";
  if (run.kind === "nop") return "Nop";
  return `Run ${run.number ?? ""}`.trim();
}
function runScore(run) {
  if (run.status === "error") return "Execution error · not a model failure";
  if (run.status === "timeout") return "Timeout · excluded from solve rate";
  if (run.status === "budget_exhausted") return `${run.budget_reason === "cost" ? "Cost" : run.budget_reason === "time" ? "Time" : "Budget"} stop · excluded from solve rate`;
  if (run.status === "interrupted") return "Interrupted · incomplete result";
  if (run.status === "cancelled") return "Cancelled by user";
  if (run.status === "skipped") return "Not scheduled";
  if (run.reward != null) {
    if (run.kind === "nop" && (run.passed === true || run.status === "passed")) return "Expected failure confirmed";
    if (run.kind === "oracle" && (run.passed === true || run.status === "passed")) return "Reference solution verified";
    return `Reward ${run.reward}${run.kind !== "evaluation" ? ` · expected ${run.expected_reward ?? (run.kind === "oracle" ? 1 : 0)}` : ""}`;
  }
  if (run.status === "running") return run.kind === "evaluation" ? "Agent working…" : "Checking environment…";
  if (run.status === "queued") return run.kind === "evaluation" ? "Waiting for checks / capacity" : "Waiting for capacity";
  return "Waiting for results";
}
function runTile(run, planned = false) {
  return `<button class="run-tile ${state.selectedRun === run.id ? "selected" : ""}" data-run="${attr(run.id)}" ${planned ? "disabled" : ""} aria-pressed="${state.selectedRun === run.id}"><span class="run-tile-heading"><strong>${escapeHTML(runLabel(run))}</strong>${badge(run.status)}</span><span class="run-score">${escapeHTML(runScore(run))}</span><span class="run-time">${escapeHTML(elapsed(run))}${run.turns != null ? ` · ${escapeHTML(run.turns)} turns` : ""}</span><span class="run-tile-link">${planned ? "Awaiting setup" : "Inspect run →"}</span></button>`;
}
function summaryText(job) {
  if (job.mode === "controls") return "Controls only. Oracle should earn reward 1; nop should earn reward 0. A passing nop check confirms the unsolved task fails its verifier.";
  const summary = job.summary || {};
  const valid = summary.valid_runs ?? 0;
  const passes = summary.passes ?? 0;
  if (demoJob(job)) {
    const stops = (job.runs || []).filter((run) => run.kind === "evaluation" && ["budget_exhausted", "timeout"].includes(run.status)).length;
    return `Budgeted demo: ${passes} successful · ${summary.failures ?? 0} model failures · ${stops} budget stops. ${valid} of 5 valid results. This is not an official learnability measurement.`;
  }
  if (valid === 5) {
    if (searchJob(job) && (passes >= 1 && passes <= 3)) return `${passes} of 5 attempts passed every test. ${acceptedJob(job) ? "Accepted after failure review and diversity checks." : "Within the target range; failure review and acceptance are still required."}`;
    if (summary.learnable === true || (passes >= 1 && passes <= 3)) return `${passes} of 5 attempts passed every test. This result is within the target range.`;
    return `${passes} of 5 attempts passed every test. ${passes === 0 ? "Below" : "Above"} the target range of 1–3 successes.`;
  }
  return `${passes} successful · ${summary.failures ?? 0} model failures · ${valid} of 5 valid results. Execution errors and timeouts do not count as model failures.`;
}
function renderValidation(job) {
  if (!job.validation) return "";
  const validation = job.validation;
  const errors = validation.errors || [];
  const warnings = validation.warnings || [];
  const heading = validation.passed === true ? "Static task checks passed" : validation.passed === false ? "Static task checks failed" : "Static task checks pending";
  return `<details class="validation-summary" data-detail="validation"><summary>${escapeHTML(heading)}${warnings.length ? ` · ${warnings.length} warnings` : ""}</summary>${errors.length ? `<div class="alert">${escapeHTML(errors.map(readable).join("\n"))}</div>` : ""}${warnings.length ? `<ul>${warnings.map((warning) => `<li>${escapeHTML(readable(warning))}</li>`).join("")}</ul>` : '<p class="help">Task structure and configuration are checked before containers start.</p>'}</details>`;
}
function renderJob(job) {
  state.job = job;
  const runs = job.runs || [];
  if (!runs.some((run) => run.id === state.selectedRun)) {
    saveCurrentPane();
    state.selectedRun = runs.find((run) => run.status === "running")?.id || runs[0]?.id || null;
    state.run = null;
  }
  if (state.detailKey !== job.id) {
    $("detail").innerHTML = '<div id="detail-head" class="detail-head"></div><div class="detail-body"><div id="job-notices"></div><div id="run-cards"></div><div id="run-inspector" class="run-inspector"><div id="run-heading"></div><div id="run-tabs" class="tabs" role="tablist" aria-label="Run information"></div><div id="run-content" class="tab-content" role="tabpanel"></div></div><div id="validation-content"></div></div>';
    state.detailKey = job.id;
  }
  const finished = runs.filter((run) => (job.mode !== "controls" || run.kind !== "evaluation") && terminal.has(run.status)).length;
  const total = job.mode === "controls" ? 2 : 7;
  const repeatAvailable = !!repeatTask(job);
  setHTML($("detail-head"), `<div class="detail-title"><h2>${escapeHTML(job.metadata?.display_name || job.task_name || job.task_id)}</h2>${jobBadge(job)}</div><div class="detail-id">${escapeHTML(clockText(job.created_at))} · ${escapeHTML(job.mode === "controls" ? "Oracle + nop" : "Oracle + nop + 5 attempts")}</div><div class="simple-progress">${finished} of ${total} runs finished · ${escapeHTML(label(job.phase || job.status))}</div><div class="progress-track" role="progressbar" aria-label="Completed runs" aria-valuenow="${finished}" aria-valuemin="0" aria-valuemax="${total}"><span style="width:${Math.min(100, Math.max(0, finished / total * 100))}%"></span></div><div class="actions">${terminal.has(job.status) ? `<button class="secondary" data-action="repeat" ${repeatAvailable ? "" : 'disabled title="Only supplied examples can be restarted from this interface."'}>Run again</button>` : '<button class="secondary danger" data-action="cancel">Cancel evaluation</button>'}<a class="secondary" href="/api/jobs/${path(job.id)}" target="_blank" rel="noopener">View result JSON ↗</a></div>`);
  setHTML($("job-notices"), job.error ? `<div class="alert">${escapeHTML(readable(job.error))}</div>` : "");
  const controls = ["oracle", "nop"].map((kind) => runs.find((run) => run.kind === kind) || { id: `planned-${kind}`, kind, status: "queued", planned: true });
  const evaluations = Array.from({ length: 5 }, (_, i) => runs.find((run) => run.kind === "evaluation" && run.number === i + 1) || runs.filter((run) => run.kind === "evaluation")[i] || { id: `planned-eval-${i + 1}`, kind: "evaluation", number: i + 1, status: "queued", planned: true });
  setHTML($("run-cards"), `<div class="group-heading"><h3>Environment controls</h3><span>Oracle: reward 1 · Nop: reward 0</span></div><div class="runs-grid controls-grid">${controls.map((run) => runTile(run, run.planned)).join("")}</div>${job.mode === "controls" ? "" : `<div class="group-heading"><h3>Five independent attempts</h3><span>GLM-5.3-Flash · high reasoning</span></div><div class="runs-grid evaluation-grid">${evaluations.map((run) => runTile(run, run.planned)).join("")}</div>`}<p class="summary-note">${escapeHTML(summaryText(job))}</p>`);
  setHTML($("validation-content"), renderValidation(job), `${job.id}:validation`);
  renderRun();
}
function turnsView(run) {
  const episodes = run.episodes || [];
  if (!episodes.length) {
    const message = run.kind !== "evaluation" ? "Oracle and nop do not call a model. Open Tests for verifier results or Logs for execution output." : run.status === "queued" ? "Agent turns will appear after the controls pass and this attempt starts." : run.status === "error" ? "No agent turns were captured. Open Logs for the execution error." : "No agent turns captured yet. This view updates as the agent works.";
    return `<div class="notice">${escapeHTML(message)}</div>`;
  }
  const stamps = episodes.map((episode) => Date.parse(episode.timestamp || ""));
  const seconds = (ms) => { const s = Math.round(ms / 1000); return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`; };
  const gaps = stamps.map((t, i) => (i > 0 && Number.isFinite(t) && Number.isFinite(stamps[i - 1]) ? t - stamps[i - 1] : NaN));
  const start = Date.parse(run.started_at || "");
  if (Number.isFinite(start) && Number.isFinite(stamps[0])) gaps[0] = stamps[0] - start;
  const last = stamps[stamps.length - 1];
  const waiting = run.status === "running" && Number.isFinite(last) ? Date.now() - last : NaN;
  const known = gaps.filter(Number.isFinite);
  const slowest = known.length ? Math.max(...known) : NaN;
  const timing = `${episodes.length} recorded turns${known.length ? ` · median turn ${seconds(known.sort((a, b) => a - b)[Math.floor(known.length / 2)])} · slowest ${seconds(slowest)}` : ""}`;
  const waitingNote = Number.isFinite(waiting) ? `<div class="notice ${waiting > 300000 ? "turn-slow" : ""}">Waiting for turn ${episodes.length + 1}: ${seconds(waiting)} since the last turn${waiting > 300000 ? " — unusually long (model response or a long-running command)" : ""}.</div>` : "";
  return `<p class="help">${timing}. Expand a turn to inspect commands and terminal output.</p>${waitingNote}` + episodes.map((episode, position) => {
    const index = episode.index ?? position + 1;
    const number = Number.isFinite(Number(index)) ? Number(index) : position + 1;
    const commands = Array.isArray(episode.commands) ? episode.commands : [];
    const analysis = readable(episode.analysis);
    const firstCommand = commands[0]?.command;
    const preview = String(firstCommand || analysis || "Agent response").replace(/\s+/g, " ").slice(0, 120);
    const commandText = commands.map((command) => `${command.command ?? ""}${command.duration != null ? `\n# execution allowance: ${command.duration}s` : ""}`).join("\n\n");
    return `<details class="episode" data-detail="turn-${attr(index)}" ${position === episodes.length - 1 ? "open" : ""}><summary><strong>Turn ${number}</strong>${Number.isFinite(stamps[position]) ? `<span class="turn-clock" title="When this turn returned">${escapeHTML(new Date(stamps[position]).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }))}</span>` : ""}${Number.isFinite(gaps[position]) ? `<span class="turn-time ${gaps[position] > 180000 ? "turn-slow" : ""}" title="Time since the previous turn (model thinking + command execution)">${seconds(gaps[position])}</span>` : ""}<span class="turn-preview">${escapeHTML(preview)}</span></summary><div class="episode-body">${analysis ? `<div class="episode-label">Agent assessment</div><p>${escapeHTML(analysis)}</p>` : ""}<div class="episode-label">Commands</div><pre data-scroll="commands-${attr(index)}">${escapeHTML(commandText || "No command in this turn.")}</pre><div class="episode-label">Observed terminal output</div><pre data-scroll="output-${attr(index)}">${escapeHTML(readable(episode.output) || "No terminal output captured for this turn.")}</pre></div></details>`;
  }).join("");
}
function testsView(run) {
  const tests = run.tests || [];
  const controlNote = run.kind === "nop" ? '<div class="notice">Nop is the negative control. Failing task tests with reward 0 is the expected result; the nop check then passes.</div>' : "";
  const table = tests.length ? `<table class="test-table"><thead><tr><th scope="col">VERIFIER TEST</th><th scope="col">RESULT</th></tr></thead><tbody>${tests.map((test, i) => `<tr><td>${escapeHTML(test.name || `Test ${i + 1}`)}${test.message ? `<details data-detail="test-${i}"><summary>Test detail</summary><pre data-scroll="test-${i}">${escapeHTML(readable(test.message))}</pre></details>` : ""}</td><td>${badge(test.status || "pending")}</td></tr>`).join("")}</tbody></table>` : '<div class="notice">No verifier test results yet. Tests run after the agent finishes.</div>';
  return controlNote + table + `<details class="episode" data-detail="verifier" style="margin-top:16px"><summary>Raw verifier output</summary><div class="episode-body"><pre data-scroll="verifier">${escapeHTML(run.verifier_output || "No verifier output captured yet.")}</pre></div></details>`;
}
function logsView(run) {
  return `<p class="help">Execution output for ${escapeHTML(runLabel(run))}. Provider, build, and harness errors appear here.</p><pre class="raw-log" data-scroll="log">${escapeHTML(run.log || "No execution output captured yet.")}</pre>`;
}
function renderRun() {
  if (!$("run-inspector")) return;
  const basic = (state.job?.runs || []).find((run) => run.id === state.selectedRun);
  if (!basic) {
    $("run-inspector").classList.add("hidden");
    return;
  }
  $("run-inspector").classList.remove("hidden");
  const detail = state.run?.id === basic.id ? state.run : null;
  const run = detail ? { ...detail, ...basic, episodes: detail.episodes, log: detail.log, verifier_output: detail.verifier_output, tests: detail.tests?.length ? detail.tests : basic.tests } : basic;
  const cost = run.cost_usd ?? run.budget?.spent_usd;
  setHTML($("run-heading"), `<div class="results-heading"><h3>${escapeHTML(runLabel(run))} · ${run.kind === "evaluation" ? "Agent attempt" : "Environment check"}</h3>${badge(run.status)}</div><div class="run-meta"><span>${escapeHTML(elapsed(run))}</span><span>${escapeHTML(run.turns ?? run.episodes?.length ?? 0)} turns</span>${run.reward != null ? `<span>Reward ${escapeHTML(run.reward)}</span>` : ""}${cost != null && Number.isFinite(Number(cost)) ? `<span>Inference $${Number(cost).toFixed(4)}</span>` : ""}${run.kind !== "evaluation" ? `<span>Expected reward ${escapeHTML(run.expected_reward ?? (run.kind === "oracle" ? 1 : 0))}</span>` : ""}</div>${run.error ? `<div class="alert">${escapeHTML(readable(run.error))}</div>` : ""}`);
  setHTML($("run-tabs"), [["turns", "Agent turns"], ["tests", "Test results"], ["logs", "Execution logs"]].map(([key, title]) => `<button class="tab ${state.tab === key ? "active" : ""}" id="tab-${key}" role="tab" aria-selected="${state.tab === key}" aria-controls="run-content" data-tab="${key}">${title}${key === "turns" && run.episodes?.length ? ` (${run.episodes.length})` : ""}${key === "tests" && run.tests?.length ? ` (${run.tests.length})` : ""}</button>`).join(""));
  const key = currentPaneKey();
  const pane = $("run-content");
  if (pane.dataset.viewKey && pane.dataset.viewKey !== key) {
    captureView(pane, pane.dataset.viewKey);
    pane.innerHTML = "";
    renderedHTML.delete(pane);
  }
  pane.dataset.viewKey = key;
  pane.setAttribute("aria-labelledby", `tab-${state.tab}`);
  setHTML(pane, detail ? state.tab === "turns" ? turnsView(run) : state.tab === "tests" ? testsView(run) : logsView(run) : '<p class="help">Loading run details…</p>', key);
}
async function loadRun() {
  const jobId = state.selectedJob;
  const runId = state.selectedRun;
  if (!jobId || !runId) return;
  const ticket = ++state.runTicket;
  try {
    const run = await request(`/api/jobs/${path(jobId)}/runs/${path(runId)}`);
    if (ticket !== state.runTicket || state.selectedJob !== jobId || state.selectedRun !== runId) return;
    state.run = run;
    renderRun();
  } catch (error) {
    if (ticket !== state.runTicket || state.selectedJob !== jobId || state.selectedRun !== runId) return;
    showError("error", error.message);
  }
}
async function selectJob(id) {
  saveCurrentPane();
  state.selectedJob = id;
  state.selectedRun = null;
  state.run = null;
  state.tab = "turns";
  ++state.runTicket;
  const ticket = ++state.jobTicket;
  history.replaceState(null, "", `#${path(id)}`);
  renderList();
  const known = state.jobs.find((job) => job.id === id);
  if (known) renderJob(known);
  try {
    const job = await request(`/api/jobs/${path(id)}`);
    if (ticket !== state.jobTicket || state.selectedJob !== id) return;
    renderJob(job);
    await loadRun();
  } catch (error) { if (state.selectedJob === id) showError("error", error.message); }
}
async function launch(mode, taskId = $("example").value) {
  if (state.launching || !taskId) return;
  state.launching = true;
  showError("form-error", "");
  updateLaunchButtons();
  const oldText = $("start-button").textContent;
  $("start-button").textContent = mode === "full" ? "Starting…" : oldText;
  try {
    const job = await request("/api/jobs", { method: "POST", body: JSON.stringify({ task_id: taskId, mode }) });
    state.jobs = [job, ...state.jobs.filter((item) => item.id !== job.id)];
    await selectJob(job.id);
    await refresh();
  } catch (error) { showError("form-error", error.message); }
  finally {
    state.launching = false;
    $("start-button").textContent = oldText;
    updateLaunchButtons();
  }
}

function demoJob(job) {
  return !searchJob(job) && !!(job.demo || job.demo_mode || job.profile === "demo" || job.evaluation_profile === "demo");
}
function searchJob(job) { return job.profile === "search" || job.evaluation_profile === "search" || !!job.metadata?.search_id; }
function acceptedJob(job) {
  if (job.accepted === true || job.metadata?.accepted === true) return true;
  return (state.phase2?.batches || []).some((batch) => (batch.tasks || []).some((task) => task.job_id === job.id && (task.accepted === true || task.acceptance?.accepted === true)));
}
function dollars(value) {
  return Number.isFinite(Number(value)) && value != null ? `$${Number(value).toFixed(2)}` : "—";
}
function promptName(prompt) { return prompt?.version || prompt?.id || "Starter prompt"; }
function searchActive() { return ["queued", "running", "stopping", "cancelling"].includes(state.search?.search?.status); }
function updateSearchButtons() {
  const active = searchActive();
  const ready = !!state.search && state.search.worker_alive !== false && state.health?.key_present && dockerReady(state.health) && state.health?.worker_alive !== false && state.phase2?.worker_alive !== false;
  $("search-start").disabled = state.searchBusy || !ready || active || activeBatches().length > 0;
  $("search-start").textContent = state.searchBusy ? "Submitting…" : active ? "Search in progress" : state.search?.search ? "Start another search →" : "Start search →";
  $("search-stop").classList.toggle("hidden", !active);
  $("search-stop").disabled = state.searchBusy;
}
function renderSearch() {
  if (!state.search) return;
  const search = state.search.search;
  const workerNotice = state.search.worker_alive === false ? '<div class="alert">The search worker is offline. Existing results remain available.</div>' : "";
  if (!search) {
    setHTML($("search-content"), `${workerNotice}<p class="help">No search started. The search continues through prompt revisions until ten accepted tasks or a checkpoint requiring review.</p>`);
  } else {
    const accepted = search.accepted_count ?? 0;
    const target = search.target ?? 10;
    const rounds = Array.isArray(search.rounds) ? search.rounds.length : search.rounds ?? 0;
    setHTML($("search-content"), `${workerNotice}<div class="search-stats"><span><strong>${escapeHTML(accepted)} / ${escapeHTML(target)}</strong> accepted tasks</span><span><strong>${escapeHTML(search.attempted_count ?? 0)}</strong> attempted tasks</span><span><strong>${escapeHTML(rounds)}</strong> rounds</span><span><strong>${dollars(search.accounted_cost_usd)}</strong> accounted inference</span>${badge(search.status)}</div><p class="help">${escapeHTML(search.phase || label(search.status))}</p>${search.stop_reason ? `<div class="checkpoint-notice"><strong>${search.status === "completed" ? "Collection complete" : "Search stopped"}</strong><p>${escapeHTML(readable(search.stop_reason))}</p></div>` : ""}${search.report_path ? `<p class="help">Historical report: <code>${escapeHTML(search.report_path)}</code></p>` : ""}<p class="help">Acceptance requires five valid results, 1–3 successes, passing controls, diversity checks, and failure review. Timeouts remain inconclusive.</p>`, "search");
  }
  updateSearchButtons();
  updatePhase2Button();
}
async function refreshSearch() {
  try {
    state.search = await request("/api/search");
    renderSearch();
    showError("search-error", "");
  } catch (error) {
    state.search = null;
    updateSearchButtons();
    showError("search-error", error.message);
  }
}
async function changeSearch(action) {
  if (state.searchBusy) return;
  state.searchBusy = true;
  updateSearchButtons();
  showError("search-error", "");
  try {
    await request(`/api/search/${action}`, { method: "POST" });
    await refreshSearch();
  } catch (error) { showError("search-error", error.message); }
  finally { state.searchBusy = false; updateSearchButtons(); }
}
function activeBatches() { return (state.phase2?.batches || []).filter((batch) => ["queued", "running", "stopping", "cancelling"].includes(batch.status)); }
function updatePhase2Button() {
  const button = $("phase2-start");
  const active = activeBatches();
  const hasHistory = !!state.phase2?.batches?.length;
  const ready = !!state.phase2 && state.phase2.worker_alive !== false && state.health?.key_present && dockerReady(state.health) && state.health?.worker_alive !== false;
  button.disabled = state.phase2Busy || !ready || active.length > 0 || searchActive();
  button.textContent = state.phase2Busy ? "Submitting…" : searchActive() ? "Search controls the batches" : active.length ? "Batch in progress" : hasHistory ? `Continue with ${promptName(state.phase2.current_prompt)} →` : "Start first batch →";
}
function evidenceDetails(title, value, key) {
  if (value == null || value === "") return "";
  return `<details class="evidence" data-detail="${attr(key)}"><summary>${escapeHTML(title)}</summary><pre data-scroll="${attr(key)}">${escapeHTML(readable(value))}</pre></details>`;
}
function reviewBadge(review) {
  if (!review) return badge("pending", "Semantic review pending");
  if (["running", "reviewing"].includes(review.status)) return badge("reviewing", "Semantic review in progress");
  if (review.verdict === "pass") return badge("passed", "Semantic review passed");
  if (review.verdict === "repair") return badge("pending", "Semantic review needs repair");
  if (review.verdict === "reject") return badge("failed", "Semantic review rejected");
  if (review.verdict === "error") return badge("error", "Semantic review error");
  const passed = review.passed ?? review.approved ?? review.valid;
  if (passed === true || ["passed", "approved", "valid"].includes(review.status)) return badge("passed", "Semantic review passed");
  if (passed === false || ["failed", "rejected", "invalid"].includes(review.status)) return badge("failed", "Semantic review flagged");
  return badge(review.status || "reviewing", "Semantic review recorded");
}
function currentSemanticReview(task) {
  // A repair starts a new review; the prior verdict belongs in its history.
  if (task.status === "generating") return null;
  if (task.status === "reviewing") return { status: "reviewing" };
  const attempts = task.attempts || [];
  return attempts.length ? attempts[attempts.length - 1].semantic_review || null : task.semantic_review;
}
function generatedPreview(task, key) {
  // readme_html is rendered by the API from the frozen task snapshot, with
  // raw HTML and embedded images disabled, just like the example preview.
  return `${task.readme_html ? `<details class="task-preview generated-preview" data-detail="${attr(key)}-readme"><summary>Task overview (README)</summary><div class="readme-content">${task.readme_html}</div></details>` : ""}${evidenceDetails("Task instructions", task.instruction, `${key}-instruction`)}`;
}
function runtimeObservations(batch) {
  return (batch.runtime_observations || []).map((observation, index) => `<div class="alert runtime-observation"><strong>Runtime observation</strong><p>${escapeHTML(observation.summary || readable(observation))}</p>${observation.job_id ? `<button class="text-button" data-inspect-job="${attr(observation.job_id)}">Inspect affected evaluation →</button>` : ""}${evidenceDetails("Observation evidence", observation, `${batch.id}-observation-${index}`)}</div>`).join("");
}
function authoringHistory(task, key) {
  const attempts = (task.attempts || []).map((attempt, index) => ({
    attempt: Number.isInteger(attempt.number) ? attempt.number + 1 : index + 1,
    generation: { status: attempt.generation?.status || "pending", errors: attempt.generation?.errors || [] },
    semantic_review: attempt.semantic_review || null,
    job_id: attempt.job_id || null,
  }));
  if (!attempts.length) return "";
  const links = attempts.filter((attempt) => attempt.job_id).map((attempt) => `<button class="text-button" data-inspect-job="${attr(attempt.job_id)}">Inspect attempt ${attempt.attempt} evaluation →</button>`).join(" ");
  return `<details class="evidence" data-detail="${attr(key)}-authoring-history"><summary>Authoring and repair history (${attempts.length} ${attempts.length === 1 ? "attempt" : "attempts"})</summary>${links}<pre data-scroll="${attr(key)}-authoring-history">${escapeHTML(readable(attempts))}</pre></details>`;
}
function rejectedProposals(batch) {
  const proposals = (batch.rejected_proposals || []).map((proposal) => ({
    version: promptName(proposal.prompt), status: "rejected", reason: proposal.reason,
  }));
  return proposals.length ? evidenceDetails(`Rejected prompt proposals (${proposals.length})`, proposals, `${batch.id}-rejected-proposals`) : "";
}
function phase2Task(task, batchId, index) {
  const attempts = task.attempts || [];
  const jobId = attempts.length ? attempts[attempts.length - 1].job_id : task.job_id;
  const job = state.jobs.find((item) => item.id === jobId);
  const runs = job?.runs || [];
  const key = `${batchId}-task-${task.id || index}`;
  const title = task.display_name || task.name || task.title || (task.id ? label(task.id.replace(/[-_]/g, " ")) : `Task ${index + 1}`);
  const review = currentSemanticReview(task);
  const controls = ["oracle", "nop"].map((kind) => runs.find((run) => run.kind === kind) || { kind, id: kind, status: "pending" });
  const evaluations = Array.from({ length: 5 }, (_, i) => runs.find((run) => run.kind === "evaluation" && run.number === i + 1) || { kind: "evaluation", id: `eval-${i + 1}`, number: i + 1, status: "pending" });
  const checks = [...controls, ...evaluations].map((run) => `<button class="mini-run" ${jobId ? `data-inspect-job="${attr(jobId)}" data-inspect-run="${attr(run.id)}"` : "disabled"} title="${attr(runLabel(run))}: ${attr(label(run.status))}"><span>${escapeHTML(runLabel(run))}</span>${badge(run.status)}</button>`).join("");
  return `<article class="generated-task"><div class="generated-heading"><h3>${escapeHTML(title)}</h3>${badge(task.status)}</div>${task.family ? `<p class="help task-family">${escapeHTML(label(task.family))}</p>` : ""}<div class="task-quality">${reviewBadge(review)}${jobId ? `<button class="text-button" data-inspect-job="${attr(jobId)}">Inspect all runs and turns →</button>` : '<span class="help">Evaluation starts after task checks.</span>'}</div><div class="mini-runs">${checks}</div>${task.error ? `<div class="alert">${escapeHTML(readable(task.error))}</div>` : ""}${job ? `<p class="help">${escapeHTML(summaryText(job))}</p>` : ""}${generatedPreview(task, key)}${evidenceDetails("Generation brief", task.topic, `${key}-brief`)}${evidenceDetails("Semantic review", review, `${key}-semantic`)}${evidenceDetails("Task feedback", task.feedback, `${key}-feedback`)}${authoringHistory(task, key)}${!job && jobId === task.job_id ? evidenceDetails("Evaluation summary", task.summary, `${key}-summary`) : ""}</article>`;
}
function diffView(diff) {
  return String(diff || "").split("\n").map((line) => `<span class="diff-line ${line.startsWith("+") && !line.startsWith("+++") ? "diff-add" : line.startsWith("-") && !line.startsWith("---") ? "diff-remove" : ""}">${escapeHTML(line)}\n</span>`).join("");
}
function phase2Batch(batch, index) {
  const isActive = ["queued", "running", "stopping", "cancelling"].includes(batch.status);
  const checkpoint = batch.status === "checkpoint" && batch.profile !== "search";
  const candidate = batch.candidate_prompt;
  const searchConfig = batch.config?.search;
  const searchCap = searchConfig ? searchConfig.tasks_per_round * (searchConfig.generation_cost_usd + searchConfig.semantic_review_cost_usd + searchConfig.failure_audit_cost_usd + 5 * searchConfig.attempt_cost_usd) + searchConfig.prompt_update_cost_usd : null;
  const batchCap = batch.profile === "search" ? searchConfig?.round_allowance_usd ?? searchCap : batch.config?.demo?.batch_cost_usd ?? state.phase2?.defaults?.batch_cost_usd ?? 3;
  const batchCost = Number.isFinite(Number(batch.accounted_cost_usd)) ? Number(batch.accounted_cost_usd).toFixed(4) : "—";
  return `<details class="batch" data-detail="batch-${attr(batch.id)}" ${index === 0 ? "open" : ""}><summary><span><strong>Batch ${escapeHTML(batch.number || batch.id?.slice(0, 8) || index + 1)}</strong><span class="batch-date">${escapeHTML(clockText(batch.created_at))} · ${escapeHTML(batch.prompt_version || batch.prompt_id || promptName(batch.source_prompt))}</span></span>${badge(batch.status)}</summary><div class="batch-body"><div class="batch-phase"><span>${escapeHTML(batch.phase || label(batch.status))}</span>${isActive ? `<button class="secondary danger" data-stop-batch="${attr(batch.id)}" ${state.phase2Busy ? "disabled" : ""}>Stop batch</button>` : ""}</div><div class="batch-cost"><strong>Accounted inference: $${batchCost} / ${dollars(batchCap)}</strong><span>Includes recorded costs and retained reservations for in-flight or unconfirmed requests. Authoring totals update after each response.</span></div>${runtimeObservations(batch)}${checkpoint ? `<div class="checkpoint-notice"><strong>Stopped for review</strong><p>${escapeHTML(batch.checkpoint_message || "This batch is complete. Review the results and prompt revision before explicitly starting another batch.")}</p></div>` : ""}${batch.error ? `<div class="alert">${escapeHTML(readable(batch.error))}</div>` : ""}<div class="generated-tasks">${(batch.tasks || []).map((task, taskIndex) => phase2Task(task, batch.id, taskIndex)).join("") || '<p class="help">Generated tasks will appear here as the batch progresses.</p>'}</div>${evidenceDetails("Batch feedback and update rationale", batch.feedback, `${batch.id}-feedback`)}${rejectedProposals(batch)}${candidate ? `<div class="prompt-revision"><div class="revision-heading"><h3>Proposed ${escapeHTML(promptName(candidate))}</h3>${badge("pending_validation", "Pending fresh-task validation")}</div><p class="help">This revision is a candidate, not a demonstrated improvement. The next batch uses the current prompt shown above.</p>${evidenceDetails("Revision rationale", candidate.rationale || candidate.reason, `${batch.id}-rationale`)}${batch.prompt_diff ? `<details class="evidence" data-detail="${attr(batch.id)}-diff" open><summary>What changed in the prompt</summary><pre class="prompt-diff" data-scroll="${attr(batch.id)}-diff">${diffView(batch.prompt_diff)}</pre></details>` : ""}<div class="prompt-comparison">${evidenceDetails(`Previous prompt · ${promptName(batch.source_prompt)}`, batch.source_prompt?.text, `${batch.id}-previous`)}${evidenceDetails(`Candidate prompt · ${promptName(candidate)}`, candidate.text, `${batch.id}-candidate`)}</div></div>` : ""}</div></details>`;
}
function renderPhase2() {
  const data = state.phase2;
  if (!data) return;
  const prompt = data.current_prompt;
  const defaults = data.defaults || {};
  const batches = data.batches || [];
  const candidate = prompt?.status && !["baseline", "validated", "accepted"].includes(prompt.status);
  const workerNotice = data.worker_alive === false ? '<div class="alert">The generation worker is offline. Existing results remain available; new batches are disabled.</div>' : "";
  const limits = `Manual demo: ${defaults.batch_size ?? 3} original tasks per batch · ${defaults.agent_timeout_sec ?? 300}s / ${dollars(defaults.attempt_cost_usd ?? .05)} per solver attempt · ${dollars(defaults.task_cost_usd ?? 1)} per task · ${dollars(defaults.batch_cost_usd ?? 3)} per batch`;
  setHTML($("phase2-content"), `${workerNotice}<div class="generation-limits">${escapeHTML(limits)}</div><div class="current-prompt"><div class="current-prompt-heading"><span>Next batch uses <strong>${escapeHTML(promptName(prompt))}</strong></span>${candidate ? badge("pending_validation", "Candidate · pending validation") : ["validated", "accepted"].includes(prompt?.status) ? badge("passed", "Produced audited tasks") : badge("pending", "Starter baseline")}</div><p class="help">The active prompt is selected automatically. A running search schedules its next batch; individual batches can also be started explicitly.</p>${evidenceDetails("Read the current generation prompt", prompt?.text || "The starter prompt will be loaded by the generation worker.", "current-prompt")}</div><div class="batch-history"><h3>Batch history <span class="count">${batches.length}</span></h3>${batches.length ? batches.map(phase2Batch).join("") : '<p class="help phase2-empty">No batches yet. The first batch will generate original tasks, validate them, run five budgeted attempts per valid task, and save a proposed prompt revision.</p>'}</div>`, "phase2");
  updatePhase2Button();
}
async function refreshPhase2() {
  try {
    state.phase2 = await request("/api/phase2");
    renderPhase2();
    showError("phase2-error", "");
  } catch (error) {
    state.phase2 = null;
    updatePhase2Button();
    showError("phase2-error", error.message);
  }
}
async function startBatch() {
  if (state.phase2Busy || activeBatches().length || searchActive()) return;
  state.phase2Busy = true;
  updatePhase2Button();
  showError("phase2-error", "");
  try {
    await request("/api/phase2/batches", { method: "POST" });
    await refreshHistory();
  } catch (error) { showError("phase2-error", error.message); }
  finally { state.phase2Busy = false; updatePhase2Button(); }
}
async function stopBatch(batchId) {
  if (state.phase2Busy) return;
  state.phase2Busy = true;
  updatePhase2Button();
  try {
    await request(`/api/phase2/batches/${path(batchId)}/stop`, { method: "POST" });
    await refreshPhase2();
  } catch (error) { showError("phase2-error", error.message); }
  finally { state.phase2Busy = false; updatePhase2Button(); }
}
async function refresh() {
  if (VIEW === "stats") {  // the Results tab only needs the status bar
    try { renderHealth(await request("/api/health")); } catch { /* reported by the next refresh */ }
    return;
  }
  if (state.refreshing) return;
  state.refreshing = true;
  const selectedAtStart = state.selectedJob;
  const ticketAtStart = state.jobTicket;
  try {
    const [health, list] = await Promise.all([request("/api/health"), request({ window: "/api/window", fidelity: "/api/fidelity" }[VIEW] || "/api/shipped")]);
    renderHealth(health);
    state.shipped = list.shipped || [];
    if (VIEW === "window") renderWindowBanner(list.window);
    if (VIEW === "fidelity") renderFidelityBanner(list.fidelity);
    renderList();
    showError("error", "");
    const first = state.shipped.find((task) => task.measurements.length)?.measurements[0];
    if (!state.selectedJob && first) {
      await selectJob(first.id);
    } else if (state.selectedJob === selectedAtStart && ticketAtStart === state.jobTicket && state.selectedJob) {
      const job = await request(`/api/jobs/${path(state.selectedJob)}`);
      if (state.selectedJob === selectedAtStart && ticketAtStart === state.jobTicket) {
        renderJob(job);
        await loadRun();
      }
    }
  } catch (error) {
    $("health-label").textContent = "Connection interrupted";
    $("health-dot").className = "dot red";
    showError("error", error.message);
  } finally { state.refreshing = false; }
}

$("example").addEventListener("change", renderPreview);
$("launch-form").addEventListener("submit", (event) => { event.preventDefault(); launch("full"); });
$("controls-button").addEventListener("click", () => launch("controls"));
$("refresh").addEventListener("click", refresh);
document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-job], [data-run], [data-tab], [data-action], [data-stop-batch], [data-inspect-job], #focus-example");
  if (!target || target.disabled) return;
  if (target.dataset.stopBatch) { await stopBatch(target.dataset.stopBatch); return; }
  if (target.dataset.inspectJob) {
    await selectJob(target.dataset.inspectJob);
    if (target.dataset.inspectRun && state.job?.runs.some((run) => run.id === target.dataset.inspectRun)) {
      state.selectedRun = target.dataset.inspectRun;
      state.run = null;
      renderJob(state.job);
      await loadRun();
    }
    $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (target.id === "focus-example") { $("example").focus(); $("launch-title").scrollIntoView({ behavior: "smooth", block: "center" }); return; }
  if (target.dataset.job) { await selectJob(target.dataset.job); return; }
  if (target.dataset.run) {
    saveCurrentPane();
    state.selectedRun = target.dataset.run;
    state.run = null;
    renderJob(state.job);
    await loadRun();
    return;
  }
  if (target.dataset.tab) { saveCurrentPane(); state.tab = target.dataset.tab; renderRun(); return; }
  if (target.dataset.action === "repeat") { await launch(state.job.mode, repeatTask(state.job)); return; }
  if (target.dataset.action === "cancel") {
    const jobId = state.selectedJob;
    target.disabled = true;
    try {
      const job = await request(`/api/jobs/${path(jobId)}/cancel`, { method: "POST" });
      if (state.selectedJob === jobId) renderJob(job);
      await refresh();
    } catch (error) { showError("error", error.message); target.disabled = false; }
  }
});
$("detail").addEventListener("keydown", (event) => {
  const current = event.target.closest('[role="tab"]');
  if (!current || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const tabs = ["turns", "tests", "logs"];
  const index = tabs.indexOf(state.tab);
  const next = event.key === "Home" ? 0 : event.key === "End" ? 2 : (index + (event.key === "ArrowRight" ? 1 : -1) + 3) % 3;
  saveCurrentPane();
  state.tab = tabs[next];
  renderRun();
  $(`tab-${state.tab}`).focus();
});
window.addEventListener("hashchange", () => {
  let id;
  try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
  if (id && id !== state.selectedJob) selectJob(id);
});
function renderWindowBanner(meta) {
  const banner = $("window-banner");
  banner.classList.remove("hidden");
  if (!meta) { setHTML(banner, '<p class="help">No window yet.</p>'); return; }
  $("jobs-title").firstChild.textContent = `Best ${meta.size}-task window `;
  const pct = (n, d) => (d ? `${Math.round(100 * n / d)}%` : "—");
  setHTML(banner, `<div class="window-rate"><strong>${escapeHTML(meta.shipped)}/${escapeHTML(meta.size)} shipped</strong><span>${pct(meta.shipped, meta.size)} of ${escapeHTML(meta.size)} consecutive probed tasks</span></div><div class="window-facts"><span><strong>${escapeHTML(meta.in_band_first)}/${escapeHTML(meta.size)}</strong> in band on the first probe</span><span>${escapeHTML(clockText(meta.start))} – ${escapeHTML(clockText(meta.end))}</span><span class="muted">${escapeHTML(meta.label)} · tasks that failed validation before any probe are not counted</span></div>`);
}
function renderFidelityBanner(meta) {
  const banner = $("window-banner");
  banner.classList.remove("hidden");
  setHTML(banner, meta ? `<div class="window-rate"><strong>Repeat measurements</strong><span>each task re-run unchanged, five attempts per run</span></div><div class="window-facts"><span><strong>${escapeHTML(meta.count)}</strong> tasks with two or more measurements, most consistent first</span><span class="muted">updated ${escapeHTML(clockText(meta.written))}</span></div>` : '<p class="help">No repeat measurements yet.</p>');
}
async function renderStats() {
  const data = (await request("/api/stats")).stats;
  const banner = $("window-banner");
  banner.classList.remove("hidden");
  banner.classList.add("stats-board");
  if (!data) { setHTML(banner, '<p class="help">No results snapshot yet.</p>'); return; }
  const pct = (n, d) => `${Math.round(100 * n / d)}%`;
  const b10 = data.best_window_10, b20 = data.best_window_20_champion;
  const cards = [
    [pct(b10.in_band_first, b10.size), "in band on the first probe", `best 10 consecutive tasks (${b10.in_band_first} of ${b10.size})`],
    [pct(b10.shipped, b10.size), "shipped through the fairness gate", `same 10 tasks (${b10.shipped} of ${b10.size})`],
    [pct(b20.in_band_first, b20.size), "in band on the first probe", `best 20 on the champion prompt (${b20.in_band_first} of 20; ${b20.shipped} shipped)`],
    [String(data.shipped), "learnable tasks shipped", `all ${data.shipped_pass_validator} pass Harbor's validator and the oracle and nop controls`],
    [String(data.repeat_verified), "in band on every repeat run", "identical task re-run unchanged, 3 of 3 or 4 of 4"],
    [`$${Number(data.cost_per_shipped_pipeline_usd).toFixed(2)}`, "per shipped task", `with the final pipeline (since 12:30); $${Math.round(data.cost_per_shipped_usd)} counting every experiment since day one`],
  ];
  setHTML(banner, `<div class="stats-grid">${cards.map(([big, what, how]) => `<div class="stat-card"><strong>${escapeHTML(big)}</strong><span class="stat-what">${escapeHTML(what)}</span><span class="stat-how">${escapeHTML(how)}</span></div>`).join("")}</div><p class="help stats-note">In band means 1–3 of 5 GLM-5.3-flash attempts (terminus-2, high reasoning) pass every hidden test. Shipped means a GLM-5.1 fairness audit traced the failing runs to rules stated in the instruction. Recomputed from the records ${escapeHTML(clockText(data.written))}.</p>`);
}
async function initialize() {
  if (VIEW === "stats") {
    document.querySelector(".launch-panel").classList.add("hidden");
    document.querySelector(".workspace-grid").classList.add("hidden");
    $("nav-stats").classList.add("active");
    try { await renderStats(); } catch (error) { showError("error", error.message); }
    return;
  }
  if (VIEW === "fidelity") {
    document.querySelector(".launch-panel").classList.add("hidden");
    $("jobs-title").firstChild.textContent = "Repeat-measured tasks ";
    document.querySelector(".list-help").textContent = "Each task's identical content measured more than once. Open a task to inspect every run.";
  }
  if (VIEW === "window") {
    document.querySelector(".launch-panel").classList.add("hidden");
    document.querySelector(".list-help").textContent = "Consecutive probed tasks, in probe order across batches and prompts. Open a task to inspect each of its probes.";
  }
  $({ window: "nav-window", fidelity: "nav-fidelity" }[VIEW] || "nav-golden").classList.add("active");
  try {
    const data = await request("/api/examples");
    state.examples = data.examples || [];
    renderExamples();
    let id = "";
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { /* Ignore a malformed fragment. */ }
    if (id) state.selectedJob = id;
    await refresh();
    await refreshHistory();
  } catch (error) { showError("error", error.message); }
}
initialize();
setInterval(refresh, 2000);
setInterval(refreshExamples, 10000);
setInterval(refreshHistory, 30000);
