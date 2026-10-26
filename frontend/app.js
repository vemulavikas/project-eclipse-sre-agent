const connectionStatus = document.querySelector("#connection-status");
const alertsContainer = document.querySelector("#alerts");
const refreshButton = document.querySelector("#refresh-alerts");
const form = document.querySelector("#investigation-form");
const submitButton = document.querySelector("#investigate-button");
const formError = document.querySelector("#form-error");
const resultsPanel = document.querySelector("#results-panel");
const workflowPanel = document.querySelector("#workflow-results");
const modeSelect = document.querySelector("#mode");
const liveQueryField = document.querySelector("#live-query-field");
const queryField = document.querySelector("#query");
const alertsPanel = document.querySelector(".alerts-panel");
let activeSession = null;

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("The server returned an invalid response.");
  }
  if (!response.ok) {
    throw new Error(payload.error || `Request failed (HTTP ${response.status}).`);
  }
  return payload;
}

function renderAlerts(container, alerts) {
  container.replaceChildren();
  if (!alerts.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No active alerts reported by Prometheus.";
    container.append(empty);
    return;
  }

  for (const alert of alerts) {
    const card = document.createElement("article");
    const state = String(alert.state || "unknown").toLowerCase();
    card.className = `alert-card ${state}`;
    const top = document.createElement("div");
    top.className = "alert-top";
    const name = document.createElement("h3");
    name.className = "alert-name";
    name.textContent = alert.name || "Unnamed alert";
    const badge = document.createElement("span");
    badge.className = `alert-badge ${state}`;
    badge.textContent = `${alert.severity || "unknown"} · ${state}`;
    top.append(name, badge);
    card.append(top);
    if (alert.summary || alert.description) {
      const description = document.createElement("p");
      description.className = "alert-description";
      description.textContent = alert.summary || alert.description;
      card.append(description);
    }
    if (alert.active_at) {
      const activeAt = document.createElement("p");
      activeAt.className = "alert-meta";
      activeAt.textContent = `Active since ${alert.active_at}`;
      card.append(activeAt);
    }
    container.append(card);
  }
}

async function loadHealth() {
  if (modeSelect.value === "demo") {
    connectionStatus.className = "connection demo";
    connectionStatus.lastElementChild.textContent = "Simulated incident ready";
    return;
  }
  try {
    await requestJson("/api/health");
    connectionStatus.className = "connection connected";
    connectionStatus.lastElementChild.textContent = "Prometheus connected";
  } catch (error) {
    connectionStatus.className = "connection disconnected";
    connectionStatus.lastElementChild.textContent = error.message;
  }
}

async function loadAlerts() {
  alertsContainer.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "muted";
  loading.textContent = "Loading alerts…";
  alertsContainer.append(loading);
  try {
    const payload = await requestJson("/api/alerts");
    renderAlerts(alertsContainer, payload.alerts || []);
  } catch (error) {
    const message = document.createElement("p");
    message.className = "form-error";
    message.textContent = error.message;
    alertsContainer.replaceChildren(message);
  }
}

function renderMetricResults(result) {
  const target = document.querySelector("#metrics-result");
  target.replaceChildren();
  const series = Array.isArray(result.result) ? result.result : [];
  if (!series.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No matching time series.";
    target.append(empty);
    return;
  }
  const table = document.createElement("table");
  table.className = "metric-table";
  const head = document.createElement("thead");
  const headingRow = document.createElement("tr");
  for (const heading of ["Labels", "Latest value"]) {
    const cell = document.createElement("th");
    cell.textContent = heading;
    headingRow.append(cell);
  }
  head.append(headingRow);
  const body = document.createElement("tbody");
  for (const item of series) {
    const row = document.createElement("tr");
    const labels = document.createElement("td");
    labels.textContent = JSON.stringify(item.metric || {});
    const value = document.createElement("td");
    const sample = item.value || (Array.isArray(item.values) ? item.values.at(-1) : null);
    value.textContent = Array.isArray(sample) ? String(sample[1]) : "—";
    row.append(labels, value);
    body.append(row);
  }
  table.append(head, body);
  target.append(table);
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderWorkflow(session) {
  activeSession = session;
  resultsPanel.hidden = true;
  workflowPanel.hidden = false;
  document.querySelector("#workflow-heading").textContent = session.title;
  const metrics = session.metrics;
  document.querySelector("#workflow-summary").textContent =
    `${session.incident} Error rate: ${(metrics.baseline.error_rate * 100).toFixed(1)}% → ${(metrics.current.error_rate * 100).toFixed(1)}%; p95 latency: ${(metrics.baseline.p95_latency_seconds * 1000).toFixed(0)}ms → ${(metrics.current.p95_latency_seconds * 1000).toFixed(0)}ms.`;

  const sources = document.querySelector("#source-list");
  sources.replaceChildren();
  for (const source of session.sources) {
    sources.append(node("span", "source-badge", `${source.name} · ${source.event_count}`));
  }

  const timeline = document.querySelector("#timeline");
  timeline.replaceChildren();
  for (const event of session.timeline) {
    const item = node("li", "timeline-item");
    const meta = node("div", "timeline-meta");
    meta.append(
      node("time", "", new Date(event.timestamp).toLocaleString()),
      node("span", "source-badge source-badge-small", event.source),
    );
    item.append(meta, node("h4", "", event.title), node("p", "", event.details));
    timeline.append(item);
  }

  const hypotheses = document.querySelector("#hypotheses");
  hypotheses.replaceChildren();
  for (const hypothesis of session.hypotheses) {
    const card = node("article", "hypothesis-card");
    const title = node("div", "hypothesis-title");
    title.append(node("h4", "", hypothesis.title), node("span", "confidence", `${Math.round(hypothesis.confidence * 100)}% · ${hypothesis.confidence_label}`));
    card.append(title, node("p", "", hypothesis.reasoning), node("p", "caveat", hypothesis.caveat));
    card.append(node("p", "evidence-refs", `Evidence: ${hypothesis.evidence_ids.join(", ")}`));
    hypotheses.append(card);
  }
  const aiBlock = document.querySelector("#demo-ai-block");
  const aiAssessment = document.querySelector("#demo-ai-assessment");
  const aiError = document.querySelector("#demo-ai-error");
  aiAssessment.textContent = session.ai_assessment || "";
  aiError.textContent = session.ai_error || "";
  aiAssessment.hidden = !session.ai_assessment;
  aiError.hidden = !session.ai_error;
  aiBlock.hidden = !session.ai_assessment && !session.ai_error;

  const plan = document.querySelector("#plan");
  plan.replaceChildren();
  let awaitingApproval = false;
  let needsVerification = false;
  for (const action of session.plan) {
    const item = node("li", `plan-item ${action.status}`);
    const title = node("div", "plan-title");
    title.append(node("h4", "", action.title), node("span", `action-status ${action.status}`, action.status.replaceAll("_", " ")));
    item.append(title, node("p", "", action.description));
    if (action.approval_required) item.append(node("span", "impact-label", `Human approval · ${action.impact} impact`));
    plan.append(item);
    if (action.status === "awaiting_approval") awaitingApproval = true;
    if (action.id === "rollback-checkout" && action.status === "executed" && !session.verification) needsVerification = true;
  }

  const controls = document.querySelector("#approval-controls");
  controls.replaceChildren();
  controls.hidden = !awaitingApproval;
  if (awaitingApproval) {
    controls.append(node("h4", "", "Approval required before simulated rollback"));
    controls.append(node("p", "muted", "This high-impact step is simulated and will not call a real deployment API."));
    const approver = node("input", "approval-input");
    approver.id = "approval-name";
    approver.placeholder = "Approver name";
    approver.maxLength = 100;
    approver.setAttribute("aria-label", "Approver name");
    const rationale = node("textarea", "approval-input");
    rationale.id = "approval-rationale";
    rationale.placeholder = "Approval rationale (at least 8 characters)";
    rationale.maxLength = 500;
    rationale.rows = 2;
    rationale.setAttribute("aria-label", "Approval rationale");
    const confirmationLabel = node("label", "approval-confirm");
    const confirmation = node("input", "");
    confirmation.type = "checkbox";
    confirmation.id = "approval-confirmed";
    confirmationLabel.append(confirmation, document.createTextNode(" I understand this runs a simulated rollback."));
    const actions = node("div", "approval-buttons");
    const approve = node("button", "button button-primary", "Approve simulated action");
    approve.type = "button";
    approve.addEventListener("click", () => submitApproval(true));
    const reject = node("button", "button button-secondary", "Reject");
    reject.type = "button";
    reject.addEventListener("click", () => submitApproval(false));
    actions.append(approve, reject);
    controls.append(approver, rationale, confirmationLabel, actions);
  }
  document.querySelector("#verify-button").hidden = !needsVerification;
  const escalation = document.querySelector("#escalation");
  escalation.hidden = !session.escalation;
  escalation.textContent = session.escalation
    ? `Escalated to ${session.escalation.target}: ${session.escalation.reason}`
    : "";
  const verification = document.querySelector("#verification");
  verification.replaceChildren();
  verification.hidden = !session.verification;
  if (session.verification) {
    verification.append(node("h4", "", `Recovery verification ${session.verification.status}`));
    const checks = node("ul", "verification-list");
    for (const check of session.verification.checks) {
      checks.append(node("li", check.passed ? "check-passed" : "check-failed", `${check.passed ? "PASS" : "FAIL"} · ${check.name}: ${check.observed}`));
    }
    verification.append(checks);
  }
  const reportLink = document.querySelector("#report-link");
  reportLink.hidden = !session.report;
  if (session.report) {
    reportLink.href = `/api/report/${encodeURIComponent(session.session_id)}`;
    reportLink.download = `${session.service}-incident-report.md`;
  }
}

async function submitApproval(approved) {
  const approver = document.querySelector("#approval-name").value.trim();
  const rationale = document.querySelector("#approval-rationale").value.trim();
  if (approved && !document.querySelector("#approval-confirmed").checked) {
    formError.textContent = "Confirm that you understand the action is simulated before approving.";
    formError.hidden = false;
    return;
  }
  if (approved && (approver.length < 2 || rationale.length < 8)) {
    formError.textContent = "Enter an approver name and an approval rationale of at least 8 characters.";
    formError.hidden = false;
    return;
  }
  formError.hidden = true;
  try {
    const session = await requestJson("/api/actions/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: activeSession.session_id,
        action_id: "rollback-checkout",
        approved,
        approver,
        rationale,
      }),
    });
    renderWorkflow(session);
  } catch (error) {
    formError.textContent = error.message;
    formError.hidden = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  submitButton.disabled = true;
  submitButton.textContent = "Gathering signals…";
  try {
    const payload = await requestJson("/api/investigate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: modeSelect.value,
        ai_assist: modeSelect.value === "demo" && document.querySelector("#use-ai").checked,
        incident: document.querySelector("#incident").value,
        query: queryField.value,
      }),
    });
    if (modeSelect.value === "demo") {
      renderWorkflow(payload);
      workflowPanel.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    workflowPanel.hidden = true;
    document.querySelector("#assessment").textContent = payload.assessment;
    const aiBlock = document.querySelector("#ai-block");
    const aiAssessment = document.querySelector("#ai-assessment");
    const aiError = document.querySelector("#ai-error");
    aiAssessment.textContent = payload.ai_assessment || "";
    aiError.textContent = payload.ai_error || "";
    aiAssessment.hidden = !payload.ai_assessment;
    aiError.hidden = !payload.ai_error;
    aiBlock.hidden = !payload.ai_assessment && !payload.ai_error;
    renderMetricResults(payload.metric_result || {});
    renderAlerts(document.querySelector("#result-alerts"), payload.active_alerts || []);
    resultsPanel.hidden = false;
    resultsPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    formError.textContent = error.message;
    formError.hidden = false;
  } finally {
    submitButton.disabled = false;
    submitButton.innerHTML = 'Investigate incident <span aria-hidden="true">→</span>';
  }
});

modeSelect.addEventListener("change", () => {
  const live = modeSelect.value === "live";
  liveQueryField.hidden = !live;
  alertsPanel.hidden = !live;
  document.querySelector("#ai-opt-in").hidden = live;
  document.querySelector("#ai-privacy-note").hidden = live;
  queryField.required = live;
  document.querySelector("#mode-note").textContent = live
    ? "Live mode queries your configured Prometheus server; optional AI analysis sends incident context and results to your selected AI provider."
    : "Uses bundled simulated logs, metrics, alert, deployment, commit, and dependency evidence. Remediation is simulated only.";
  loadHealth();
  if (live) loadAlerts();
});

document.querySelector("#verify-button").addEventListener("click", async () => {
  try {
    const session = await requestJson("/api/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: activeSession.session_id }),
    });
    renderWorkflow(session);
  } catch (error) {
    formError.textContent = error.message;
    formError.hidden = false;
  }
});

refreshButton.addEventListener("click", loadAlerts);
loadHealth();
alertsPanel.hidden = modeSelect.value !== "live";
if (modeSelect.value === "live") loadAlerts();
