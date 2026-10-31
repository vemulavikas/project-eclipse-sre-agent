# Signal Desk — SRE incident triage

Signal Desk is an incident triage workspace for SRE and operations teams:

**INPUT → UNDERSTAND → PLAN → USE TOOLS → ACT → VERIFY / ESCALATE → REPORT**

The guided scenario runs end-to-end against a bundled simulated checkout
incident. Its deterministic investigator correlates a
Git commit, deployment, application log, dependency health, Prometheus-style
metrics, and alert in timestamp order; ranks root-cause hypotheses with
transparent heuristic confidence; proposes a remediation; gates the simulated
high-impact rollback on explicit human approval; and either verifies recovery
or escalates a rejected action. It then makes a Markdown post-incident report
available for download. This scenario is designed to explore the workflow
locally; it does not connect to production systems.
The AI second opinion is opt-in; when selected, the fixture incident context is
sent to the configured AI provider.

**Scenario boundaries:** all six data sources are fixture evidence in
`incidents/scenarios/checkout_latency.json`; confidence values are rule-based
demonstration estimates, not calibrated probabilities. The rollback and
post-rollback observations are simulated. No real deployment, Git host, log
system, dependency monitor, or production service is changed or queried by the
scenario. An optional, explicit AI second opinion can be requested; it cannot
change the evidence, confidence score, approval state, or remediation.

**Live Prometheus mode** separately connects to a configured Prometheus server
for alert and instant-query evidence. It does not yet connect live to Git,
deployment history, application logs, or service dependency systems. Optional
NVIDIA NIM/Nebius AI analysis is available for the live investigation mode.

## Requirements

- Python 3.10 or newer
- No third-party Python packages required
- A reachable Prometheus server only for live mode
- Optional AI provider credentials for AI-assisted analysis

## Run the code locally

To run the dashboard with a local Prometheus server using Docker Compose, copy
`.env.example` to `.env` and run:

```powershell
docker compose up --build
```

Open <http://127.0.0.1:8000>. Prometheus is available at
<http://127.0.0.1:9090>; both ports are bound to loopback. The local Compose
stack uses a development-only metrics token. Do not reuse it in a deployment.

To run only the Python server without Docker, run this from the repository root:

```powershell
py -m agent.server
```

Open <http://127.0.0.1:8000>, leave **Guided scenario — simulated incident** selected, and
choose **Investigate incident**. Review the source evidence, timeline, and
hypotheses. To demonstrate recovery, enter an approver and rationale, confirm
that the action is simulated, approve the rollback, and choose **Verify
recovery**. The verification checks the simulated error rate, latency, alert
state, and restored version. Download the report after verification.

If the remediation is rejected, the workflow records escalation to the
incident commander and still generates a report describing that outcome.

## Live Prometheus mode

Copy `.env.example` to `.env`, set `PROMETHEUS_URL`, and optionally set
`PROMETHEUS_BEARER_TOKEN`. Process environment variables take precedence over
`.env`. Select **Live Prometheus query** in the dashboard to read active
Prometheus alerts and submit an instant PromQL query.

AI analysis is optional. In demo mode, explicitly select the AI second opinion;
in live mode, the gateway attempts AI analysis automatically. Set
`AI_PROVIDER` to `nvidia` or `nebius`, then configure that provider's API key
and model. Without valid AI configuration, live metrics and alert results
remain available and the response explains why AI analysis was unavailable.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROMETHEUS_URL` | `http://localhost:9090` | Prometheus base URL |
| `PROMETHEUS_BEARER_TOKEN` | empty | Optional Prometheus bearer token |
| `PROMETHEUS_TIMEOUT_SECONDS` | `10` | Prometheus request timeout |
| `HOST` | `127.0.0.1` | Dashboard bind address |
| `PORT` | `8000` | Dashboard port |
| `AI_PROVIDER` | `nvidia` | `nvidia` or `nebius` |
| `AI_REQUEST_TIMEOUT_SECONDS` | `60` | AI request timeout |
| `NVIDIA_API_KEY` / `NEBIUS_API_KEY` | empty | Selected provider credential |
| `NVIDIA_MODEL` / `NEBIUS_MODEL` | empty | Selected provider model ID |
| `METRICS_BEARER_TOKEN` | empty | Bearer token required to enable `/metrics` |

### Render Prometheus setup

The root `render.yaml` defines a free-tier public Prometheus web service. Its
read-only API is protected by a bearer-authenticated proxy; only the harmless
`/-/ready` health check is unauthenticated. Create a Blueprint from this
repository in Render and deploy `signal-desk-prometheus`.

Set two different strong secrets in Render's Blueprint prompt and in the
existing dashboard service's Environment settings:

- `METRICS_BEARER_TOKEN`: same value on both services; Prometheus uses it to
  scrape the dashboard's protected `/metrics` endpoint.
- `PROMETHEUS_BEARER_TOKEN`: same value on both services; the Prometheus
  service's proxy requires it for API requests, and the dashboard sends it.

On the dashboard service, set `PROMETHEUS_URL` to the public URL shown in the
Prometheus service's Render settings, for example
`https://signal-desk-prometheus.onrender.com`. Keep both tokens secret and out
of Git. Free web services can spin down when idle, so the first dashboard
request may take longer while Prometheus starts. Prometheus history is
ephemeral and is lost when the service is recreated.

The dashboard binds to loopback by default. Approval is a workflow safeguard,
not identity authentication; approver names are self-reported. Do not expose
this development server to a network without adding authentication, audit
storage, and appropriate access controls. The guided scenario has no production
write connector.

## Workflow API

- `POST /api/investigate` — start `{"mode":"demo"}` or a live Prometheus investigation
- `POST /api/actions/approve` — approve/reject the demo rollback and record rationale
- `POST /api/verify` — verify the simulated recovery after approval
- `GET /api/report/<session_id>` — retrieve the report after verification or escalation
- `GET /api/scenarios` — list the bundled demonstration scenario

Incident sessions are held in server memory and are cleared on restart.

## Tests

```powershell
py -m unittest discover -v
```

Tests use mocked HTTP responses and the simulated scenario; no live monitoring
or AI credentials are required.

## Learn more

- [Product overview and architecture](docs/PRODUCT_OVERVIEW.md)
- [User guide](docs/USER_GUIDE.md)
