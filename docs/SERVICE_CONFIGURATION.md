# Project organization and configuration

For deployment identity, prompt files, database migration, and separate clinic
frontends, see [Dietitian deployments](DIETITIAN_DEPLOYMENTS.md).

Application code lives in the `backend` package. Python modules use lowercase
snake_case filenames; class names such as `RecipeChatGraph` remain the same.

```text
main.py                       CLI launcher
run_api.py                    HTTP server launcher (also used by Docker)
backend/
  bootstrap.py                Creates services, nodes, and the selected graphs
  paths.py                    Absolute paths to project configuration and data
  tenancy.py                  Immutable dietitian profile and prompt snapshot
  config/                     Settings and validated configuration classes
  cli/                        Argument parsing and chat/ingest commands
  web/                        FastAPI endpoints, sessions, and rate limits
  graphs/                     Graph contracts, workflows, and node builder
  nodes/
    chat/                     Nodes used by the chat graph
    ingestion/                Nodes used when adding recipes
  services/
    chat/                     Analysis, validation, search, generation, presentation
    ingestion/                Recipe extraction and database ingestion
    contracts.py              Abstract service interfaces
    exceptions.py             Shared service errors
  factories/                  Service construction and model-client registry
  models/                     Pydantic request, recipe, and response data
  persistence/                Database pool, schema, and repositories
  logging_config.py           Logging configuration
Frontend/                     React interface
config/                       services.json, models.json, models.example.json
  dietitians/<id>/            Per-dietitian profile.json and prompt overrides
  prompts/                    Shared text prompt templates
data/
  recipes/                    Source recipe text files for ingestion
  dietitians/<id>/recipe_images/  Images served only by their owning deployment
tests/                        Automated Python tests
docs/
  notes/                      Existing design notes, preserved as written
  legacy/                     Unused early graph-state draft, kept for reference
output/                       Generated deliverables, including the async trace PDF
```

Run commands from the project root using your Python environment:

```powershell
python main.py chat
python main.py ingest --file "data/recipes/example.txt"
python main.py --show-services
python run_api.py
python -m pytest -q
```

Replace the example recipe path with a real file. The `.env` file stays in the
project root; copy `.env.example` there and fill in local credentials.
The launchers retain their existing arguments. Direct ASGI imports now use
`backend.web.api:app`. When explicitly selecting a JSON file, use its new path,
for example `--models-config config/models.example.json`. Update any existing
`MODELS_CONFIG` or `SERVICES_CONFIG` path overrides accordingly.

Default configuration, data, and frontend paths resolve from the project root,
independently of the current working directory. Explicit relative paths passed
on the command line remain relative to the current working directory.
`backend/config/` contains Python definitions; top-level `config/` contains
the editable JSON choices.

VS Code hides generated caches, the virtual environment, frontend dependencies,
build output, and scratch files in the Explorer. They remain on disk.
The archived draft in `docs/legacy/` is not imported by the application.
The active graph state is `backend/graphs/graph_state.py`.

## Service implementations and startup configuration

Services implement abstract base classes in `backend/services/contracts.py`. Nodes depend
on those contracts. `backend/factories/service_factories.py` holds a separate factory for
each service, with a registry of named implementations. The existing behavior is
registered as the default. The chat provider factory reuses LangChain's existing
`BaseChatModel` contract and supports OpenAI, Anthropic, and Google. Embeddings
keep the original OpenAI setup.

At startup, the application resolves its choices, validates every name, creates
the selected services, and injects them into the nodes. Invalid names stop startup
before database connections are opened. Factories reject incomplete abstract
classes and builders returning the wrong service type.

## Running a task from one entry point

Choose the task on the command line:

```powershell
python main.py chat
python main.py chat --user-id my-session
python main.py ingest --file "data/recipes/example.txt"
python main.py --help
```

`chat` constructs one graph and reuses it for the interactive conversation. Enter
`exit` or `quit`, or send end-of-input, to finish. Blank messages are skipped.
Without `--user-id`, the command generates an ID for that session. The CLI uses
in-memory checkpoints, so providing the same ID after restarting the CLI does not
restore a previous process's conversation.

`ingest` reads the supplied UTF-8 file and runs the ingestion graph once. It prints
the saved recipe ID on success. An unreadable/empty file, duplicate recipe, or
reported ingestion failure returns a nonzero exit code. File paths are relative
to the current working directory. Running `main.py` without a task prints help;
it no longer automatically ingests a hardcoded recipe.

Service/model flags work before or after the task name, including repeated
overrides. For example:

```powershell
python main.py chat --models-config config/models.example.json --model chat=classifier
python main.py --models-config config/models.example.json ingest --file "data/recipes/example.txt"
python main.py --show-models
python main.py --show-services
```

Configuration inspection does not require a task and does not construct clients
or database connections. `run_api.py` remains the HTTP server launcher and keeps
its existing service/model flags.

### Graph execution contract

Both graphs implement `BaseGraph[RequestT, ResultT]` in `backend/graphs/base_graph.py`.
Chat workflows specialize this as `BaseChatGraph`, the contract consumed by
FastAPI and `ChatCommand`. Recipe ingestion retains its separate request type.
Each subclass implements `async arun(request)`. The base class supplies
`run(request)` for synchronous callers outside an event loop. Task inputs are
frozen dataclasses in `backend/graphs/graph_requests.py`:

```python
from backend.graphs.graph_requests import ChatGraphRequest, RecipeIngestionRequest

chat = create_app()
reply = await chat.arun(ChatGraphRequest(
    message="Hi!", user_id="user-a", dietitian_id=chat.tenant.dietitian_id,
))

ingestion = create_ingest_recipe()
saved = await ingestion.arun(RecipeIngestionRequest(
    raw_recipe="Recipe text...", dietitian_id=ingestion.tenant.dietitian_id,
))
```

In synchronous code, use `chat.run(ChatGraphRequest(...))` or
`ingestion.run(RecipeIngestionRequest(...))` instead. The former positional
`arun(message, user_id)` and `run(raw_recipe)` signatures have been replaced by
request objects. The HTTP `/chat` request and response formats are unchanged;
the endpoint creates a `ChatGraphRequest` internally. Ingestion uses LangGraph's
async invocation, which runs the existing synchronous ingestion nodes in worker
threads. Embeddings remain the original OpenAI implementation.

### Registering another graph task

`backend.bootstrap.create_commands()` registers a command handler with its lazy graph factory:

```python
return {
    "chat": ChatCommand(create_app),
    "ingest": IngestCommand(create_ingest_recipe),
}
```

`backend/cli/commands/base_command.py` defines `add_arguments(parser)` and `async run(args)`.
The concrete handlers collect task-specific input, call their graph through
`arun(request)`, and display the result. The chat loop and recipe file handling
live in those handlers. `backend.cli.runner.run_cli()` validates shared configuration and
dispatches the selected command; only that command constructs a graph.

For a new task, implement a `BaseGraph` subclass and its request type, write a
command handler (or reuse a suitable one), and register its factory in
`create_commands()`. The central `main()` and CLI dispatch do not need another
task-specific branch. The fluent node builder continues to assemble the chat
node container; graph classes own their node connections and routing.

## Choosing implementations

Use `config/services.json` for reproducible, versioned defaults. Use Railway environment
variables for deployment overrides and CLI flags for local experiments.

Precedence, highest first:

1. CLI `--service NAME=TYPE` overrides.
2. Environment variables (including local `.env` values).
3. The selected JSON file.
4. Built-in defaults for fields omitted from JSON.

`SERVICES_CONFIG` selects a JSON file; `--services-config PATH` overrides that
path. The default file is `config/services.json` under the project root, independent
of the working directory. Explicit relative paths are relative to the working
directory. Missing files, malformed JSON, unknown fields, and invalid type names
are errors. Keep credentials in environment variables, not in this JSON file.

| Service key | Environment variable | Current type | Dependencies supplied to its builder |
| --- | --- | --- | --- |
| `chat_graph` | `CHAT_GRAPH_TYPE` | `recipe` | `nodes` (`RecipeChatNodes`), `checkpointer`, `model_registry`, `tenant` |
| `chat_model` (implicit default profile provider) | `CHAT_MODEL_TYPE` | `openai` | `profile` (`ModelProfile`) |
| `message_analyzer` | `MESSAGE_ANALYZER_TYPE` | `llm` | `llm`, `tenant` |
| `request_validator` | `REQUEST_VALIDATOR_TYPE` | `llm` | `llm`, `tenant` |
| `recipe_search` | `RECIPE_SEARCH_TYPE` | `postgres` | `recipe_repository`, `ingredient_repository`, `embedding_repository`, `embedding_model` |
| `recipe_generation` | `RECIPE_GENERATION_TYPE` | `llm` | `llm`, `tenant` |
| `recipe_extractor` | `RECIPE_EXTRACTOR_TYPE` | `llm` | `llm`, `tenant` |
| `recipe_ingestion` | `RECIPE_INGESTION_TYPE` | `postgres` | `recipe_repository`, `ingredient_repository`, `embedding_repository`, `tenant` |
| `dietitian` | `DIETITIAN_TYPE` | `file` | `tenant` |
| `recipe_presentation` | `RECIPE_PRESENTATION_TYPE` | `structured` | None |

For example, inspect or run the API from an activated Python environment:

```powershell
python run_api.py --show-services
python run_api.py --services-config config/services.json --service message_analyzer=llm
```

`--show-services` and `--show-models` print effective service selections, registered
alternatives, resolved model profiles, and model assignments, then exit without
creating models or database connections. These flags are also
available on the existing `main.py` runner. CLI overrides are passed through the
environment so uvicorn reload workers retain them. Direct `uvicorn backend.web.api:app`
startup reads the same JSON and environment settings.

## Switching the chat graph used by FastAPI

FastAPI holds a `BaseChatGraph`, not a concrete `RecipeChatGraph`. At startup,
`backend.bootstrap.create_app()` builds the configured services and node container, then calls:

```python
graph = factories.chat_graph.create(
    config.chat_graph,
    nodes=nodes,
    checkpointer=checkpointer,
    model_registry=models,
    tenant=tenant,
)
```

The factory requires `BaseChatGraph` and registers `RecipeChatGraph` as `recipe`.
The default configuration remains `"chat_graph": "recipe"`. Both the HTTP API
and CLI chat select this implementation through the same factory. FastAPI builds
the selected graph once per worker and invokes `arun(ChatGraphRequest(...))` for
each `/chat` request. The HTTP request/response format remains unchanged.

To add an experiment, implement `BaseChatGraph` in a new graph module. Its
constructor receives `nodes`, `checkpointer`, `model_registry`, and `tenant`; its async
`arun(request)` handles `ChatGraphRequest` and returns chat state with
`final_response` and `response_blocks`. Use the provided checkpointer and isolate
conversation history by `request.thread_id` (dietitian ID plus user ID). Reject
requests whose `dietitian_id` differs from the supplied `tenant`. You can also subclass
`RecipeChatGraph` and override `build_graph()` to reuse its request-state handling
while changing the workflow connections.

Register the implementation once in `ServiceFactories.__init__`:

```python
from backend.graphs.experimental_chat_graph import ExperimentalChatGraph

self.chat_graph.register("experimental", ExperimentalChatGraph)
```

`ExperimentalChatGraph` is an example name for your implementation; only `recipe`
is registered by default. Once the experiment is implemented and registered,
choose it with any of these options:

```powershell
python run_api.py --service chat_graph=experimental
python main.py chat --service chat_graph=experimental
python run_api.py --service chat_graph=experimental --show-services
```

```dotenv
CHAT_GRAPH_TYPE=experimental
```

Or set `"chat_graph": "experimental"` in `config/services.json`. The existing precedence
applies: CLI > environment > JSON > default. On Railway, deploy the new graph and
its registration once; subsequent switches between deployed implementations use
`CHAT_GRAPH_TYPE` and a restart/deployment of the changed configuration. There is
no per-request graph selection or live reload. Neither `backend/web/api.py` nor its `/chat`
endpoint needs to be edited to register or select another chat graph.

Unknown graph names fail before model clients or database pools are created.
The factory rejects ingestion graphs, incomplete abstract classes, and builders
returning objects that do not implement `BaseChatGraph`. `--show-services` lists
the selected graph and registered alternatives without constructing them.

The current startup supplies the standard six-node container to every chat graph.
An experiment can connect those nodes differently or use a subset. Experiments
needing additional dependencies also require extending their construction code;
configuration selects already-registered implementations.

## Switching model providers without Python changes

Provider selection is independent of the service implementation. For example,
`message_analyzer=llm` can run with OpenAI, Claude, or Gemini; it receives the
selected chat model through the same `BaseChatModel` interface. By default all
consumers share one client. Named profiles let each service or node receive a
different client, provider, or model configuration.

`CHAT_MODEL_TYPE` selects the provider adapter for the implicit `default` profile.
`CHAT_MODEL` is the actual model ID at that provider. Set both when switching;
changing the provider does not
translate an OpenAI model ID into a Claude or Gemini model ID.

| Chat provider | `CHAT_MODEL_TYPE` | Example `CHAT_MODEL` | Credential |
| --- | --- | --- | --- |
| OpenAI | `openai` | `gpt-5.4-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic` | `claude-haiku-4-5-20251001` | `ANTHROPIC_API_KEY` |
| Google | `google_genai` | `gemini-2.5-flash` | `GOOGLE_API_KEY` |

Choose a model available to your account that supports structured output/tool
calling. The Anthropic and Google adapters and their dependencies are included in
`requirements.txt`. Provider setup is documented by LangChain:
[Anthropic](https://docs.langchain.com/oss/python/integrations/chat/anthropic),
[Google](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai).

For example, to try Claude while keeping existing OpenAI recipe embeddings, set
these in `.env` locally or in Railway Variables:

```dotenv
CHAT_MODEL_TYPE=anthropic
CHAT_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_API_KEY=your-anthropic-key
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=your-openai-key
```

Then restart the app (or deploy the Railway variable changes). No Python edits
are required to switch among the included providers. With credentials and
`CHAT_MODEL` already set, you can also choose the provider through the CLI:

```powershell
python run_api.py --service chat_model=anthropic
```

Or put the provider defaults in `config/services.json`:

```json
{"chat_model": "anthropic"}
```

These existing settings continue to configure the implicit `default` profile.
Keep keys in environment variables. `CHAT_MAX_TOKENS` sets its token limit.
`OPENAI_REASONING_EFFORT` applies only when that profile uses OpenAI; set it empty
to omit the parameter for models that don't support it. Anthropic and Google
never receive the OpenAI reasoning setting.
Text responses use LangChain's `.text` accessor to handle providers returning
either plain text or content blocks.

Embeddings are always created directly with `OpenAIEmbeddings`, using the existing
`EMBEDDING_MODEL` setting (default: `text-embedding-3-small`). `OPENAI_API_KEY`
remains required for embeddings even when chat uses Anthropic or Google.
Switching the chat provider leaves recipe embeddings unchanged.

## Assigning different LLMs to different services

`config/models.json` separates two decisions:

- `models` defines named profiles: provider, model ID, token limit, and optional
  OpenAI reasoning effort.
- `assignments` maps each LLM consumer to a profile name.

The loader always supplies an implicit `default` profile from the `chat_model`
service selection and `CHAT_MODEL`, `CHAT_MAX_TOKENS`, and
`OPENAI_REASONING_EFFORT` settings unless the JSON explicitly defines that profile.
Roles without an assignment use `default`. Additional profiles and assignments in
`config/models.json` select separate models for those roles.

To try multiple models, use the included `config/models.example.json` or add profiles
to `config/models.json`. For example:

```json
{
  "models": {
    "classifier": {
      "provider": "openai",
      "model": "gpt-5.4-mini",
      "max_tokens": 1000,
      "reasoning_effort": "none"
    },
    "writer": {
      "provider": "anthropic",
      "model": "claude-haiku-4-5-20251001",
      "max_tokens": 8000
    }
  },
  "assignments": {
    "message_analyzer": "classifier",
    "request_validator": "classifier",
    "recipe_generation": "writer",
    "recipe_extractor": "classifier",
    "chat": "writer",
    "response": "writer"
  }
}
```

Use model IDs available to your accounts. The analyzer, validator, response
recipe selector, and extractor require structured output; extraction uses tool
calling. The example needs `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` to run.
Inspection commands do not require credentials or make API calls.

Explicit profiles are self-contained. Their required fields are `provider` and
`model`; `max_tokens` defaults to 16000, and `reasoning_effort` defaults to null
(omitted from SDK construction). Only the OpenAI adapter accepts a non-null
`reasoning_effort`. Two profiles may use the same provider with different models
or settings. Unknown fields, nonpositive token limits, unknown provider names,
and assignments to missing profiles stop startup before database connections.

JSON may also define a profile named `default`. That replaces the entire implicit
profile; the old global chat settings do not override explicit profiles. Missing
assignments always select `default`. Changing `--service chat_model=...` affects
only the implicit profile, not profiles with their own provider in JSON.

`MODELS_CONFIG` or `--models-config PATH` selects a model configuration file. The
default is `config/models.json` under the project root, independent of the working
directory. Explicit relative paths are relative to the working directory.
Missing or malformed files are errors.

For assignments, precedence is **CLI > environment > JSON > default**:

| Consumer | Assignment environment variable |
| --- | --- |
| `message_analyzer` | `MESSAGE_ANALYZER_MODEL_PROFILE` |
| `request_validator` | `REQUEST_VALIDATOR_MODEL_PROFILE` |
| `recipe_generation` | `RECIPE_GENERATION_MODEL_PROFILE` |
| `recipe_extractor` | `RECIPE_EXTRACTOR_MODEL_PROFILE` |
| `chat` | `CHAT_MODEL_PROFILE` |
| `response` | `RESPONSE_MODEL_PROFILE` |

```powershell
python run_api.py --models-config config/models.example.json --show-models
python run_api.py --models-config config/models.example.json --model chat=classifier
python main.py ingest --file "data/recipes/example.txt" --models-config config/models.example.json --model recipe_extractor=writer
```

Flags also work together with `--service` and `--services-config`. Validated
overrides are exported to the environment so reload workers inherit them.
On Railway, set `MODELS_CONFIG=config/models.example.json` and, for example,
`CHAT_MODEL_PROFILE=classifier`, then deploy the variable changes. Profiles must
exist in the deployed configuration. Changing a profile's model or settings in
the committed JSON requires deploying that updated file, but no Python edits.

### Startup construction and reuse

`backend/factories/model_registry.py` validates providers and constructs a dictionary of
clients through the existing `chat_model` factory. It creates one client per
assigned profile name, even when several roles use that profile. Unassigned
profiles are validated but not constructed. Chat and ingestion startup use this registry:

```python
models = ModelRegistry(model_config, factories.chat_model)

message_analyzer = factories.message_analyzer.create(
    service_config.message_analyzer,
    llm=models.for_role("message_analyzer"),
)
chat_node = ChatNode(llm=models.for_role("chat"))
```

Services receive a `BaseChatModel` directly; they never look up global model
names. Provider builders now accept `profile=ModelProfile(...)` rather than the
global settings object. The graph retains the registry for inspection through
`graph.model_registry.models`, a read-only mapping of profile names to initialized
clients.

Each graph construction builds a registry for its resolved assignments. FastAPI
constructs its graph once during startup, so requests share those initialized
clients for the lifetime of that worker. Separate worker processes have separate
registries. There is no per-request construction or live configuration reload.

`create_app` and `create_ingest_recipe` also accept an explicit `model_config`
(`ModelConfig`) for tests and experiments; this bypasses JSON/environment loading
for model choices. Service choices can independently use `service_config`.

## Building the chat nodes

`backend/graphs/builders/recipe_chat_builder.py` implements a fluent Builder: configure each
node through a method, then call `build()` to obtain a `RecipeChatNodes` container.
`backend.bootstrap.create_app()` uses the existing services and model clients:

```python
builder = RecipeChatBuilder(tenant=tenant)
builder.analysisNode(message_analyzer)
builder.recipeNode(recipe_generation_service, recipe_search_service)
builder.chatNode(models.for_role("chat"))
builder.responseNode(models.for_role("response"), presentation_service)
builder.dietitianNode(dietitian_service)
builder.guardNode(request_validator)
nodes = builder.build()

graph = factories.chat_graph.create(
    config.chat_graph,
    nodes=nodes,
    checkpointer=checkpointer,
    model_registry=models,
    tenant=tenant,
)
```

Each configuration method returns the builder, so calls can also be chained:

```python
nodes = (
    RecipeChatBuilder(tenant=tenant)
    .analysisNode(message_analyzer)
    .recipeNode(recipe_generation_service, recipe_search_service)
    .chatNode(models.for_role("chat"))
    .responseNode(models.for_role("response"), presentation_service)
    .dietitianNode(dietitian_service)
    .guardNode(request_validator)
    .build()
)
```

`RecipeChatNodes` stores all six nodes as named fields: `analysis`, `recipe`,
`chat`, `response`, `dietitian`, and `guard`. `build()` checks that every required
step was configured, and creates the guard using the final chat, recipe, and
dietitian nodes. Configuration order is flexible, including calling `guardNode()`
before configuring the branches. Reconfiguring a builder produces a new container
on the next `build()`; it does not replace nodes in an earlier container.

The container's fields are frozen, while the node objects remain usable. Inspect
nodes through `graph.nodes.analysis`, `graph.nodes.chat`, and the other fields.
`graph.model_registry` still exposes the initialized model clients.

`RecipeChatGraph` accepts the completed container, defines edges and conditional
routing in `build_graph()`, and compiles with its checkpointer. It retains async
execution, the synchronous CLI entry point, per-user conversation history, and
the existing guard behavior. Node construction happens during startup. Changing
execution order or routing belongs in the graph structure; the builder assembles
the nodes and their dependencies. Recipe ingestion retains its existing path.

## Adding another analyzer

Implement `BaseMessageAnalyzer` in a new service module. Its required signature is:

```python
async def analyze(
    self,
    message: str,
    current_user_message: str | None = None,
) -> MessageAnalysis:
    ...
```

`message` contains conversation context; `current_user_message` contains only the
latest user turn. Preserve this distinction when implementing classification
rules. Keep the method asynchronous and return `MessageAnalysis` so existing
nodes can consume the result.

Once `backend/services/chat/experimental_message_analyzer.py` contains your implementation,
import and register it in `ServiceFactories.__init__` alongside the existing one:

```python
from backend.services.chat.experimental_message_analyzer import ExperimentalMessageAnalyzer

# Inside ServiceFactories.__init__:
self.message_analyzer.register("experimental", ExperimentalMessageAnalyzer)
```

The factory passes `llm=...` and `tenant=...` to this constructor. If your constructor needs
different arguments, register an adapter that accepts the supplied dependencies:

```python
self.message_analyzer.register(
    "experimental",
    lambda llm, tenant: ExperimentalMessageAnalyzer(model=llm, tenant=tenant, prompt_version="v2"),
)
```

Now select it using any of these methods:

```json
{"message_analyzer": "experimental"}
```

```powershell
python run_api.py --service message_analyzer=experimental
```

Or set `MESSAGE_ANALYZER_TYPE=experimental` in Railway. The node, graph, and
existing implementation need no changes. A new implementation must be written,
registered, tested, and deployed once before its name can be selected.

For isolated experiments and tests, both application factories accept explicit
configuration and their own registries:

```python
factories = ServiceFactories()
factories.message_analyzer.register("experimental", ExperimentalMessageAnalyzer)
graph = create_app(
    service_config=ServiceConfig(message_analyzer="experimental"),
    service_factories=factories,
)
```

Use the same pattern with `create_ingest_recipe`. Explicit `ServiceConfig`
objects are complete resolved choices and bypass JSON/environment loading.
Registries are independent per `ServiceFactories` instance. Registration changes
for a deployed application belong in the central catalog so every worker sees
the same names.

## Railway updates

Objects are selected once per process startup. There is no live config reload;
local changes take effect on the next app start. Each deployed replica must start
with the updated configuration.

- **Switch between implementations already deployed:** change the service's
  environment variable in Railway, review the staged changes, and deploy them.
  No GitHub code change or push is required. See
  [Railway variables](https://docs.railway.com/variables).
- **Change the committed JSON defaults or add an implementation:** commit and
  push to the branch connected to Railway. With GitHub autodeploy enabled,
  Railway builds and deploys that commit. See
  [GitHub autodeploys](https://docs.railway.com/deployments/github-autodeploys).

A Railway variable override wins over the committed JSON. Remove that override
if you want a new JSON default to take effect. Deploying staged variable changes
is the step that applies them; restarting an old deployment is not a substitute.

## Verification

```powershell
python -m pytest -q
```

The tests exercise configuration precedence, invalid choices, abstract contract
enforcement, per-role model assignment, client reuse, and alternative service
implementations running through the real chat and ingestion graphs with local
test doubles for external dependencies. Provider adapters are constructed and
bound to schemas offline; the tests do not call live model APIs.
CLI tests cover command registration, configuration overrides, file errors,
interactive graph reuse, and help/inspection without external dependencies.
Graph-selection tests run an independently implemented chat workflow through the
actual `/chat` endpoint, verify graph reuse and separate session histories, and
check that invalid graph choices fail before external dependencies are opened.
