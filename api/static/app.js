/* Task Lab: example evaluation only. All task and model text is escaped. */
"use strict";

const $ = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const attr = escapeHTML;
const path = encodeURIComponent;
const terminal = new Set(["completed", "passed", "failed", "error", "timeout", "cancelled", "skipped", "interrupted", "validation_failed"]);
const renderedHTML = new WeakMap();
const state = {
  examples: [], jobs: [], health: null, job: null, run: null,
  selectedJob: null, selectedRun: null, tab: "turns", refreshing: false,
  launching: false, runTicket: 0, jobTicket: 0, detailKey: null,
  expanded: new Map(), scrolls: new Map(),
};

const labels = {
  nop: "Nop", oracle: "Oracle", evaluation: "Model attempts",
  queued: "Queued", pending: "Pending", running: "Running", passed: "Passed",
  failed: "Failed", error: "Execution error", timeout: "Timed out", completed: "Complete",
  validation_failed: "Controls failed", cancelled: "Cancelled", cancelling: "Cancelling",
  skipped: "Skipped", interrupted: "Interrupted", learnable: "In target range",
  validation: "Checking task", validating: "Checking task", controls: "Running controls",
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
  $("capacity-count").textContent = `${health.active_runs ?? 0} / ${health.max_concurrency ?? 3}`;
  $("model-label").textContent = `${health.model || "GLM-5.3-Flash"} · ${health.reasoning_effort || "high"} reasoning`;
  $("freshness").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;
  updateLaunchButtons();
}
function renderExamples() {
  $("example").innerHTML = '<option value="">Choose an example…</option>' + state.examples.map((example) => `<option value="${attr(example.id)}">${escapeHTML(example.name || example.id)}</option>`).join("");
  updateLaunchButtons();
}
function renderPreview() {
  const example = state.examples.find((item) => item.id === $("example").value);
  $("task-instruction").textContent = example?.instruction || example?.description || "Choose an example to read its instructions.";
  updateLaunchButtons();
  showError("form-error", "");
}
function jobBadge(job) {
  if (job.status === "completed" && job.mode === "controls") {
    const controls = (job.runs || []).filter((run) => run.kind !== "evaluation");
    if (controls.length === 2 && controls.every((run) => run.passed === true || run.status === "passed")) return badge("passed", "Controls passed");
  }
  if (job.status === "completed" && job.summary?.learnable === true) return badge("learnable");
  return badge(job.status);
}
function renderList() {
  $("job-count").textContent = state.jobs.length;
  const scrollTop = $("job-list").scrollTop;
  const scrollLeft = $("job-list").scrollLeft;
  setHTML($("job-list"), state.jobs.length ? state.jobs.map((job) => {
    const runs = (job.runs || []).filter((run) => job.mode !== "controls" || run.kind !== "evaluation");
    const finished = runs.filter((run) => terminal.has(run.status)).length;
    const detail = job.mode === "controls" ? "Controls only" : `${finished} / ${runs.length || 7} finished`;
    return `<button class="submission ${job.id === state.selectedJob ? "active" : ""}" data-job="${attr(job.id)}" aria-current="${job.id === state.selectedJob ? "true" : "false"}"><strong>${escapeHTML(job.task_name || job.task_id)}</strong><span class="submission-meta">${jobBadge(job)}<span>${escapeHTML(detail)}</span></span><span class="submission-date">${escapeHTML(clockText(job.created_at))}</span></button>`;
  }).join("") : '<p class="list-empty">Your evaluations will appear here.</p>');
  $("job-list").scrollTop = scrollTop;
  $("job-list").scrollLeft = scrollLeft;
}
function runLabel(run) {
  if (run.kind === "oracle") return "Oracle";
  if (run.kind === "nop") return "Nop";
  return `Run ${run.number ?? ""}`.trim();
}
function runScore(run) {
  if (run.status === "error") return "Execution error · not a model failure";
  if (run.status === "timeout") return "Timeout · excluded from solve rate";
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
  if (valid === 5) {
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
  const repeatAvailable = state.examples.some((example) => example.id === job.task_id);
  setHTML($("detail-head"), `<div class="detail-title"><h2>${escapeHTML(job.task_name || job.task_id)}</h2>${jobBadge(job)}</div><div class="detail-id">${escapeHTML(clockText(job.created_at))} · ${escapeHTML(job.mode === "controls" ? "Oracle + nop" : "Oracle + nop + 5 attempts")}</div><div class="simple-progress">${finished} of ${total} runs finished · ${escapeHTML(label(job.phase || job.status))}</div><div class="progress-track" role="progressbar" aria-label="Completed runs" aria-valuenow="${finished}" aria-valuemin="0" aria-valuemax="${total}"><span style="width:${Math.min(100, Math.max(0, finished / total * 100))}%"></span></div><div class="actions">${terminal.has(job.status) ? `<button class="secondary" data-action="repeat" ${repeatAvailable ? "" : 'disabled title="Only supplied examples can be restarted from this interface."'}>Run again</button>` : '<button class="secondary danger" data-action="cancel">Cancel evaluation</button>'}<a class="secondary" href="/api/jobs/${path(job.id)}" target="_blank" rel="noopener">View result JSON ↗</a></div>`);
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
  return `<p class="help">${episodes.length} recorded turns. Expand a turn to inspect commands and terminal output.</p>` + episodes.map((episode, position) => {
    const index = episode.index ?? position + 1;
    const number = Number.isFinite(Number(index)) ? Number(index) : position + 1;
    const commands = Array.isArray(episode.commands) ? episode.commands : [];
    const analysis = readable(episode.analysis);
    const firstCommand = commands[0]?.command;
    const preview = String(firstCommand || analysis || "Agent response").replace(/\s+/g, " ").slice(0, 120);
    const commandText = commands.map((command) => `${command.command ?? ""}${command.duration != null ? `\n# execution allowance: ${command.duration}s` : ""}`).join("\n\n");
    return `<details class="episode" data-detail="turn-${attr(index)}" ${position === episodes.length - 1 ? "open" : ""}><summary><strong>Turn ${number}</strong><span class="turn-preview">${escapeHTML(preview)}</span></summary><div class="episode-body">${analysis ? `<div class="episode-label">Agent assessment</div><p>${escapeHTML(analysis)}</p>` : ""}<div class="episode-label">Commands</div><pre data-scroll="commands-${attr(index)}">${escapeHTML(commandText || "No command in this turn.")}</pre><div class="episode-label">Observed terminal output</div><pre data-scroll="output-${attr(index)}">${escapeHTML(readable(episode.output) || "No terminal output captured for this turn.")}</pre></div></details>`;
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
  setHTML($("run-heading"), `<div class="results-heading"><h3>${escapeHTML(runLabel(run))} · ${run.kind === "evaluation" ? "Agent attempt" : "Environment check"}</h3>${badge(run.status)}</div><div class="run-meta"><span>${escapeHTML(elapsed(run))}</span><span>${escapeHTML(run.turns ?? run.episodes?.length ?? 0)} turns</span>${run.reward != null ? `<span>Reward ${escapeHTML(run.reward)}</span>` : ""}${run.kind !== "evaluation" ? `<span>Expected reward ${escapeHTML(run.expected_reward ?? (run.kind === "oracle" ? 1 : 0))}</span>` : ""}</div>${run.error ? `<div class="alert">${escapeHTML(readable(run.error))}</div>` : ""}`);
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
async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  const selectedAtStart = state.selectedJob;
  const ticketAtStart = state.jobTicket;
  try {
    const [health, list] = await Promise.all([request("/api/health"), request("/api/jobs")]);
    renderHealth(health);
    state.jobs = list.jobs || [];
    renderList();
    showError("error", "");
    if (!state.selectedJob && state.jobs.length) {
      await selectJob(state.jobs[0].id);
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
  const target = event.target.closest("[data-job], [data-run], [data-tab], [data-action], #focus-example");
  if (!target || target.disabled) return;
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
  if (target.dataset.action === "repeat") { await launch(state.job.mode, state.job.task_id); return; }
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
async function initialize() {
  try {
    const data = await request("/api/examples");
    state.examples = data.examples || [];
    renderExamples();
    let id = "";
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { /* Ignore a malformed fragment. */ }
    if (id) state.selectedJob = id;
    await refresh();
  } catch (error) { showError("error", error.message); }
}
initialize();
setInterval(refresh, 2000);
