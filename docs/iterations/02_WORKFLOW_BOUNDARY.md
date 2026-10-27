**Iteration 2: give every chat workflow the same public boundary**

Implemented on 2026-10-26. This implements only step 2 of the [workflow implementation plan](../CONFIGURABLE_WORKFLOWS_IMPLEMENTATION_PLAN.md).

**Current input.** The user's request was: "Do step 2". The previous instruction to document each iteration's input, situation, problem, method, and result still applies. The technical starting point was the existing recipe graph and the 175 passing offline tests recorded in [iteration 1](01_WORKFLOW_BASELINE.md).

The HTTP request body is still a message:

```json
{"message": "Find saved rolls"}
```

The API supplies identity from its configured workflow and the server-issued session cookie. Client, conversation, and release identity are not new browser-supplied body fields.

**Situation before this change.** Both the API and CLI called `BaseChatGraph.arun(ChatGraphRequest)` directly. The API extracted `final_response`, `response_blocks`, and analysis fields from the returned recipe `GraphState`. The CLI printed `result["final_response"]`. This made callers depend on the recipe workflow's internal representation, even though they only need an answer to display.

`RecipeChatGraph` already correctly handled routing, validation, per-turn resets, history, and presentation. Those responsibilities remain there. The deployment still loads a dietitian profile and selects one graph at startup.

**Problem addressed.** A future chef or other workflow should be able to use its own state fields without imitating the recipe `GraphState`. The API and CLI should have one stable request/response contract. They should not need another branch for every internal graph representation.

**Method.** Introduce a small public contract and adapt the existing recipe graph to it:

```text
Before:
    API / CLI
        -> ChatGraphRequest
        -> RecipeChatGraph
        -> GraphState
        -> caller extracts presentation fields

After:
    API / CLI
        -> ConversationRequest
        -> ChatWorkflow.respond()
        -> RecipeWorkflowAdapter
        -> existing RecipeChatGraph
        -> adapter creates ChatResponse
        -> caller receives reply and blocks
```

The adapter is a translation layer. It delegates execution to the existing graph, preserving the graph's node construction, edges, state reset, and checkpointer. It does not add a second graph or reimplement the recipe flow.

**The new pieces are:**

| Piece | Responsibility |
|---|---|
| [ClientContext](../../backend/clients/context.py) | Immutable server-resolved client ID, display name, and release identity; contains no dietitian profile or recipe fields |
| [ConversationRequest](../../backend/workflows/contracts.py) | Immutable message, client ID, conversation ID, and release identity |
| [ChatWorkflow](../../backend/workflows/contracts.py) | Public `respond()` method; checks identity and validates the public response |
| [RecipeWorkflowAdapter](../../backend/workflows/adapters/recipe.py) | Maps the generic request to `ChatGraphRequest` and recipe state to `ChatResponse` |
| [create_chat_workflow](../../backend/bootstrap.py) | Builds the configured existing graph once and wraps it with the matching client context |

`ClientContext` and `ConversationRequest` are frozen Pydantic models. Required text is nonempty. Client identifiers accept letters, digits, underscores, and hyphens up to 64 characters, including the current `clinic_001` identifier. The workflow implements `_respond()`; the shared `respond()` method checks client/release identity before calling it and validates the resulting `ChatResponse` afterward.

For this iteration, one browser or CLI session is one conversation. `conversation_id` therefore maps to the existing graph's `user_id`. The default `release_id` is the explicit marker `legacy`, because release tables and activation are later steps. It does not imply release pinning, configuration snapshots, or database version management have been implemented.

Inside an async function, an internal invocation looks like this:

```python
from backend.bootstrap import create_chat_workflow
from backend.workflows.contracts import ConversationRequest

workflow = create_chat_workflow()
response = await workflow.respond(ConversationRequest(
    message="Find saved rolls",
    client_id=workflow.client.client_id,
    conversation_id="example-session",
    release_id=workflow.client.release_id,
))

print(response.reply)
```

This example uses normal application construction and requires its configured dependencies. The tests use doubles and in-memory checkpoints instead.

**What the adapter preserves.** `client_id` becomes `dietitian_id`, and `conversation_id` becomes `user_id`. The resulting checkpoint key is still `clinic_001:example-session`; the adapter does not insert a release ID into old checkpoint keys or move persisted conversations.

The adapter copies `final_response` into `ChatResponse.reply`. Explicit `response_blocks=[]` remains an empty list. Only missing or `None` response blocks use the existing text-block fallback. Saved recipes retain their selected IDs, original content, source links, and image URLs. Errors and cancellation propagate to the caller. Recipe intent/mode logging moved into the adapter; HTTP logging uses generic client, conversation/session, and release information.

**Where the application uses it.** [FastAPI](../../backend/web/api.py) now stores `app.state.workflow` and retrieves it through `get_workflow()`. `/chat` calls `workflow.respond()` and returns the public response directly. The request body, session cookie, rate-limit keys, PostgreSQL retry, error responses, and public JSON format remain compatible.

[ChatCommand](../../backend/cli/commands/chat_command.py) receives a workflow factory, constructs one workflow for the interactive session, creates a `ConversationRequest` for each message, and prints `response.reply`. It uses `workflow.client.display_name` for the label. Bootstrap maps the existing profile's brand name to that field, preserving the current Alona/Dana labels.

The existing `bootstrap.create_app()` still returns the raw graph for direct callers, graph registration, and existing internal tests. `create_chat_workflow()` calls it and wraps the result. `create_http_app(graph_factory=...)` remains available and its selected graph is now wrapped as well. Inspecting the underlying legacy graph is possible through `app.state.workflow.graph`, but request handlers do not depend on that attribute.

**Proof that the boundary is independent of recipe state.** Added [test_chat_workflow.py](../../tests/test_chat_workflow.py) with a small `CounterWorkflow`. It has its own `CounterState` containing only `message` and `count`, its own LangGraph node, and no recipe services or nodes. It returns ordinary text blocks through `ChatResponse`.

The tests send two HTTP messages and two CLI messages through this workflow. Each conversation maintains its own count, and API/CLI code accepts the response without reading counter fields. The API test supplies an already-built workflow and bypasses the normal deployment lifespan. This proves caller compatibility; it does not claim generic client onboarding or workflow selection through production configuration is complete.

Other checks cover immutable context/request values, client/release mismatch before execution, separate histories with a shared in-memory checkpointer, invalid public responses, presentation fallbacks, propagation of failure/cancellation, and the 503 response before workflow initialization.

Added one scenario to [the recipe baseline tests](../../tests/test_recipe_workflow_baseline.py): a real recipe turn and follow-up run through the adapter, then the existing checkpoint key is inspected. It verifies selected recipe content/photo, stored displayed answers, and removal of stale recipe blocks on the follow-up.

Existing API/CLI tests were updated to inject or inspect the new public workflow boundary. The deployment selection test still goes through the real bootstrap context mapping and adapter. The original nine recipe baseline scenarios remain intact.

**Verification.** Run from the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/integration --junitxml=tmp/workflow-step-02.xml
```

The offline suite passed **189 tests**: the previous 175 plus 14 additional cases. The run includes the existing graph, service-factory, tenancy, session, CLI, API presentation, and recipe baseline checks. The XML path is a local run artifact, not required to use the application.

Real PostgreSQL integration tests were excluded. Live provider calls and browser rendering were not exercised. No frontend code or database schema changed, so frontend/build and migration checks were not part of this iteration. The production lifecycle still validates the existing service/model configuration and builds recipe-oriented dependencies; removing those construction assumptions is planned for later steps.

**Result and limitations.** The API and CLI now consume `ConversationRequest -> ChatResponse`, while recipe state remains inside the graph. The benefit is that another implementation can choose its own state and execution structure. The cost is one explicit translation layer while legacy graph construction remains in use.

JSON graph definitions, a generic workflow registry, client database records, prompt storage changes, multiple clients per deployment, release publication, and conversation migration remain unimplemented. Step 2 is complete; step 3 is the validated JSON configuration schema.
