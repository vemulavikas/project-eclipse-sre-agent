# Signal Desk — Round 2 demo runbook

## Before the demo

- Use Python 3.10 or newer.
- From the project root, run `py -m unittest discover -v`.
- Start the app with `py -m agent.server`.
- Open <http://127.0.0.1:8000>.
- Keep **Eclipse challenge demo — simulated incident** selected.
- The challenge demo requires neither Prometheus nor AI credentials.

## Three-minute walkthrough

### 1. Introduce the problem

“During a production incident, responders need to correlate signals across
monitoring, logs, deployments, source changes, and dependencies. Signal Desk
shows this evidence in a reviewable workflow rather than returning only a
generated answer.”

### 2. Run the investigation

Click **Investigate incident**. Point out the source badges and timestamped
timeline: commit, deployment, payment timeouts, dependency degradation, metric
changes, and firing alert.

### 3. Explain the hypotheses

Show the release-regression and dependency-outage hypotheses. Explain that each
has a confidence estimate, supporting evidence IDs, and a caveat. State clearly
that confidence is a scenario-specific heuristic—not a calibrated probability
or confirmed root cause.

If provider credentials are configured, optionally select **Request an
optional AI second opinion** before starting a new investigation. Explain that
the second opinion is advisory only and does not control the plan or approval.

### 4. Demonstrate the approval gate and recovery

Enter an approver and rationale, confirm the simulation warning, and approve
the action. Point out that the action is explicitly a **simulated rollback**;
no deployment API is called. Click **Verify recovery** and review all four
checks: error rate, latency, active alerts, and restored version.

### 5. Show escalation and reporting

On a fresh investigation, reject rather than approve the proposed action to
show escalation to the incident commander. For either outcome, download the
Markdown post-incident report.

## Claims to make accurately

- The multi-source evidence, rollback, and recovery values in this demo come
  from a bundled scenario fixture.
- Live Prometheus alerts and instant queries are available in a separate mode.
- Live Git, log, deployment-history, and dependency-system adapters are future
  work.
- The application is a working prototype, not a production-ready autonomous
  remediation system.

## Troubleshooting

- If port 8000 is occupied, set `PORT=8001` in the shell before running the
  server, then open the matching URL.
- If Python cannot import `agent.server`, run the command from the repository
  root.
- The demo mode does not need `.env` values. Live mode requires a reachable
  Prometheus server.
- AI is optional. If credentials or provider access are unavailable, rerun
  without the AI checkbox; the deterministic demo remains functional.
