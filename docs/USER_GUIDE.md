# Signal Desk user guide

This guide covers running Signal Desk locally and using its guided scenario
and live Prometheus investigation modes.

## Start Signal Desk

Requirements: Python 3.10 or newer. No third-party Python packages are needed.

From the repository root, start the server:

```powershell
py -m agent.server
```

Open <http://127.0.0.1:8000> in a browser. The guided scenario works without
external services or credentials.

## Investigate a guided scenario

1. Select **Guided scenario — simulated incident**.
2. Optionally enter incident context and select the AI second opinion if a
   provider is configured.
3. Choose **Investigate incident** to review the source evidence, timeline,
   hypotheses, and response plan.
4. To explore the recovery path, enter an approver and rationale, confirm the
   simulation notice, and approve the action.
5. Choose **Verify recovery** to inspect the simulated error-rate, latency,
   alert, and version checks. Download the incident report when available.

If the proposed action is rejected, Signal Desk records an escalation for
incident commander review and makes the report available.

The scenario's evidence, response action, and recovery measurements are
simulated. The guided scenario does not query or change production systems.

## Connect to Prometheus

Copy `.env.example` to `.env`, set `PROMETHEUS_URL`, and optionally configure
`PROMETHEUS_BEARER_TOKEN`. Process environment variables take precedence over
`.env`. Select **Live Prometheus query**, provide an incident description and
PromQL query, then choose **Investigate incident**.

Optional AI analysis uses NVIDIA NIM or Nebius. Set `AI_PROVIDER` to `nvidia`
or `nebius`, then configure the selected provider's API key and model. AI
analysis is advisory. If AI configuration is unavailable, live metrics and
alerts remain accessible and the dashboard explains the AI error.

See the [product overview](PRODUCT_OVERVIEW.md) for mode boundaries, safety
considerations, and architecture.

## Run tests

From the repository root:

```powershell
py -m unittest discover -v
```

Tests use mocked HTTP responses and the bundled scenario; no live monitoring
service or AI credentials are required.
