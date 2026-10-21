# Clench partner MCP prototype

An AI assistant can inspect a configured Clench core and propose switching its board to English
or Spanish. A caregiver reviews the exact change and applies or rejects it through a separate
API credential. The existing tablet WebView receives the normal SETTINGS broadcast.

This is a local stdio MCP server using the official Python SDK, with an opt-in REST API in the
Core. It is not a hosted multi-company service. Each core owns one patient session: multiple
connected browsers/tablets mirror that session. Connection counts are not device identities,
and an unreachable core does not prove a tablet is powered off. Use opaque aliases, not patient
names, in the configuration. No patient history, raw signals, contacts or current sentences are
returned. No usage analytics or custom tile editor is included in this first version.

## Isolated setup (PowerShell)

From the feature worktree, install dependencies:

```powershell
uv sync --extra mcp --extra sensor
```

Generate two independent random tokens, store them using your local secret manager/environment,
and substitute them below. Do not commit credentials. The normal Core reads these two optional
variables from its environment; no key means the corresponding API capability is disabled.

```powershell
$env:CLENCH_PARTNER_TOKEN = '<partner-token>'
$env:CLENCH_CAREGIVER_TOKEN = '<different-caregiver-token>'
$env:ELEVENLABS_PREWARM = 'false'
uv run python -c "import os, uvicorn; from core.main import create_app; uvicorn.run(create_app(env={'CLENCH_PARTNER_TOKEN':os.environ['CLENCH_PARTNER_TOKEN'],'CLENCH_CAREGIVER_TOKEN':os.environ['CLENCH_CAREGIVER_TOKEN'],'ELEVENLABS_PREWARM':'false','COMPUTER_START_URL':'http://127.0.0.1:8001/computer/start'}), host='127.0.0.1', port=8001)"
```

This test core uses an in-memory database and no cloud service keys. In another terminal, run
the web app on 5174 with `CORE_URL=http://127.0.0.1:8001` as described in the runbook. Never restart
the shared demo on 8000/5173. The tablet must load that test board to observe its changes.

Create `cores.local.json` outside version control (or use a temporary path):

```json
[
  {"id": "demo", "url": "http://127.0.0.1:8001", "token_env": "DEMO_PARTNER_TOKEN"}
]
```

Set `DEMO_PARTNER_TOKEN` to the core's partner token in the MCP host environment. Configure your
MCP host to launch `uv` with arguments:

```text
--directory <absolute-feature-worktree> run --extra mcp python -m core.mcp_server --config <absolute-cores.local.json>
```

Use one config and process per trust boundary/company; every configured alias is visible to
that host. Do not pass the caregiver token into this process. Tool arguments accept registered
aliases only, never arbitrary URLs. HTTP is allowed only on loopback; remote origins require
HTTPS. Requests time out after five seconds and do not follow redirects or environment proxies.

## Demonstration

Ask the connected assistant:

> List the configured Clench cores, check demo's board connections, and prepare a Spanish board update.

Available tools: `list_cores`, `get_device_status`, `get_board_settings`, `propose_board_update`.
The last tool returns an id, before/after language and expiry, with `pending_caregiver_review`.
It makes no live change. This switches existing bilingual content; it does not translate or
rewrite custom text.

In the caregiver's separate terminal, inspect the proposals:

```powershell
$headers = @{ Authorization = "Bearer $env:CLENCH_CAREGIVER_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8001/api/partner/proposals -Headers $headers
```

After reviewing the exact `before` and `after`, explicitly approve the chosen id:

```powershell
$proposalId = '<reviewed-proposal-id>'
Invoke-RestMethod "http://127.0.0.1:8001/api/partner/proposals/$proposalId/approve" -Method Post -Headers $headers
# Or reject it:
Invoke-RestMethod "http://127.0.0.1:8001/api/partner/proposals/$proposalId" -Method Delete -Headers $headers
```

Approval is not an MCP tool. Partner credentials cannot approve or reject. Approval returns 409
if the language has changed or the session is confirming, speaking, helping, loading, onboarding,
in Computer/Car mode, or showing a back prompt. Return to ordinary communication scanning and
retry after checking that the proposal is still appropriate. Proposals expire in ten minutes,
are consumed once, have a 100-pending limit, and disappear on restart. Applied language is a
session setting and also resets on core restart, matching the existing language control.

## Boundaries and next work

The existing core's other routes/WebSockets are local prototype interfaces and are not protected
by this feature. Do not expose the entire core to the internet. Remote deployment needs a gateway
that isolates these partner routes, HTTPS, company/user authorization, device enrollment,
persistent review/audit records and a caregiver review UI. Bearer credentials here are local
prototype credentials, not a production tenant/OAuth model. A language approval does not grant
permission to speak/send: all patient confirmations and emergency handling remain in the session.

Run `uv run --extra mcp --extra sensor pytest` and `uv run python -c "import core.contracts"`.
The MCP tests include a real stdio initialize/list/call exchange; HTTP failures are simulated.
