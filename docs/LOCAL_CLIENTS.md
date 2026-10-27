# Run Alona and Michael locally

The two clients use one API process and separate URLs, releases and conversation
histories. Michael has a dedicated frontend JavaScript module and dark stylesheet.

| Client | Browser page | Chat endpoint |
|---|---|---|
| Alona | `http://127.0.0.1:8000/c/alona` | `/api/clients/alona/chat` |
| Michael Lipkin | `http://127.0.0.1:8000/c/michael-lipkin` | `/api/clients/michael-lipkin/chat` |

## Quick start in PowerShell

Run these commands from `D:\Python\AIChatAgentProject`:

```powershell
# Only create .env if you do not already have one.
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Set `OPENAI_API_KEY` in that local `.env` file. Do not share or commit the key.
The API needs access to the configured guard and chat models for real replies.
The pages and public configuration can be viewed without a provider key, but chat
will not generate AI replies until it is configured and the API is restarted.

```powershell
npm.cmd --prefix Frontend run build
.\.venv\Scripts\python.exe run_local.py
```

Open the browser URLs above. Keep this terminal running; use another terminal for
HTTP requests. Ctrl+C stops the API.

`run_local.py` starts a project-local PostgreSQL cluster listening only on
`127.0.0.1:55432`, prepares the schema, registers both clients if needed, and runs
the normal API in database mode. It reuses the clients on later starts without
publishing new releases or changing conversation pins.

The cluster is stored under `.local/client-preview` (ignored by Git), with a
database named `chat_clients_dev`. It uses local trust authentication for this
development preview. Do not expose its port or use this launcher in production.
Your configured `recipes_db` / `DATABASE_URL` is not used by this launcher.

**This is a new database:** Alona's saved recipe collection has not been imported.
Her recipe-generation flow can run with a provider key; saved-recipe searches
start with an empty collection. Michael's initial workflow offers cooking advice
without a saved-recipe search service. Both support up to ten ingredient photos.

PostgreSQL 16 with pgvector is installed on this machine. Elsewhere, install it
and set `LOCAL_POSTGRES_BIN` to its `bin` directory if it is not on PATH.
Use `--port 8001` or `--db-port 55433` if the defaults are occupied. Stop an existing
preview database before changing its port.

```powershell
# Optional: only prepare the database/clients, without running HTTP.
.\.venv\Scripts\python.exe run_local.py --prepare-only

# After stopping the API, stop the preview database while preserving its data.
.\.venv\Scripts\python.exe run_local.py --stop-db
```

## Try HTTP requests

The public-config requests do not call an AI provider:

```powershell
$baseUrl = 'http://127.0.0.1:8000'
Invoke-RestMethod "$baseUrl/api/clients/alona/public-config"
Invoke-RestMethod "$baseUrl/api/clients/michael-lipkin/public-config"
```

Expected identities are `clinic_001` / `Alona` and `michael_lipkin` /
`Michael Lipkin`, with separate release IDs.

With your API key configured, send the same message to both clients:

```powershell
$alonaSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$michaelSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$body = @{ message = 'Suggest a savory dinner with tomatoes and eggs. Answer in English.' } | ConvertTo-Json

Invoke-RestMethod "$baseUrl/api/clients/alona/chat" `
  -Method Post -ContentType 'application/json' -Body $body -WebSession $alonaSession

Invoke-RestMethod "$baseUrl/api/clients/michael-lipkin/chat" `
  -Method Post -ContentType 'application/json' -Body $body -WebSession $michaelSession

# Reuse the same session to keep the conversation's checkpoint and pinned release.
Invoke-RestMethod "$baseUrl/api/clients/michael-lipkin/chat" `
  -Method Post -ContentType 'application/json' -WebSession $michaelSession `
  -Body (@{ message = 'Make it quicker to prepare.' } | ConvertTo-Json)
```

Alternatively, [examples/client_requests.http](../examples/client_requests.http)
contains requests for an HTTP client, and `/docs` exposes FastAPI's interactive API
documentation. The website URL is `/c/...`; POST messages to `/api/clients/.../chat`.

## Where Michael's customizations live

- [michaelLipkin.js](../Frontend/src/clients/michaelLipkin.js): Michael-specific UI
  copy and theme selection.
- [michaelLipkin.css](../Frontend/src/clients/michaelLipkin.css): charcoal surfaces,
  warm accents, readable text, chat bubbles and photo controls.
- [experiences.js](../Frontend/src/clients/experiences.js): selects a reviewed local
  module from the `client_id` returned by the API.
- [michael_lipkin.json](../config/releases/michael_lipkin.json): identity, branding,
  workflow, prompts, models, guard and photo-recognition configuration.

Frontend changes require a rebuild. Published backend releases are immutable:
editing the JSON does not replace an active release or an existing conversation.
Use the publication commands below for later changes, incrementing prompt versions
when their text changes.

## Use your existing configured database instead

This is an alternative to `run_local.py`. Configure the database connection in
`.env` first. Back up an existing database and stop old API instances before
migrating. Do not run a legacy ownership migration without confirming the owner.

```powershell
.\.venv\Scripts\python.exe -m backend.persistence.migrate
.\.venv\Scripts\python.exe -m backend.clients.setup_local
$env:CLIENT_CONFIG_SOURCE = 'database'
.\.venv\Scripts\python.exe run_api.py
```

If the recipe migration reports unowned legacy rows, follow the explicit migration
instructions in [DIETITIAN_DEPLOYMENTS.md](DIETITIAN_DEPLOYMENTS.md). Setup preserves
already active releases and refuses conflicting client identities or URLs.

To publish a later Michael release to the **preview database**, point the management
CLI at that database explicitly:

```powershell
$env:DATABASE_URL = 'postgresql://chat_dev@127.0.0.1:55432/chat_clients_dev'
.\.venv\Scripts\python.exe -m backend.clients.manage validate config/releases/michael_lipkin.json
.\.venv\Scripts\python.exe -m backend.clients.manage publish config/releases/michael_lipkin.json --activate
```

New conversations use that release; existing browser sessions stay pinned to their
previous release. Use a new browser/private session to try the new configuration.
