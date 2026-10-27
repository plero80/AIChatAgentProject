**Iteration 1: establish the existing recipe workflow baseline**

Completed on 2026-10-26. This implements step 1 of the [workflow implementation plan](../CONFIGURABLE_WORKFLOWS_IMPLEMENTATION_PLAN.md). It adds executable regression scenarios and records the current behavior before graph construction changes.

**Current input and requested scope.** The request for this iteration was:

> What would be your first step and implement only it.
> (I want to see iterative implementatinos to understand you design).
> You must write the current input.
> the situiation we are currently in, the problem if there is, and the method/solution and so on.

The architectural goal remains a graph skeleton described by JSON, with Python implementations of nodes, services, contracts, and routing functions. The input to this iteration is the existing application and its tests. The output is a verified behavioral reference for subsequent iterations. This iteration stops before introducing the generic workflow interface, JSON schema, database tables, or new client routing.

**The situation at the start.** The application already works as a recipe assistant with one configured dietitian per application instance. The existing offline suite passed all 166 tests before changes. No failing application behavior was found in that suite.

The current construction and execution sequence is:

```text
Startup:
    tenant configuration + service/model configuration
        -> bootstrap creates services
        -> RecipeChatBuilder creates a fixed set of nodes
        -> RecipeChatGraph connects and compiles the graph

One request:
    POST /chat
        -> resolve tenant, session cookie, and rate limits
        -> ChatGraphRequest
        -> analyze
        -> guard (validation and selected branch run concurrently)
        -> response, or early exit with a refusal
        -> JSON containing reply and blocks
```

The branches inside the guard are chat, recipe, and dietitian contact. The recipe branch uses existing, inspired, or original mode. A food request with mode `none` defaults to a saved-recipe search. The top-level graph contains only analyze, guard, and response; the branches are called by the guard.

The relevant code is [RecipeChatBuilder](../../backend/graphs/builders/recipe_chat_builder.py), [RecipeChatGraph](../../backend/graphs/recipe_chat_graph.py), [GuardNode](../../backend/nodes/chat/guard_node.py), [RecipeNode](../../backend/nodes/chat/recipe_node.py), and [ResponseNode](../../backend/nodes/chat/response_node.py).

**An example of the current runtime input.** The browser sends a message body and its session cookie:

```json
{"message": "Find saved rolls"}
```

The API adds the deployment's identity and the resolved session identity. The new tests call the graph directly using a fixed session string:

```python
ChatGraphRequest(
    message="Find saved rolls",
    user_id="baseline-session",
    dietitian_id="clinic_001",
)
```

The fixed string is test input to the graph, not an example of a valid API session cookie. The HTTP layer separately issues or validates UUID-shaped cookies, which the existing session tests cover.

For the saved-recipe scenario, the model double returns food intent and existing mode. The search double returns two candidates, tofu followed by rolls. The presentation model selects only the second candidate. The expected public result contains the rolls card, its exact stored instructions, Instagram URL, and image URL. Mentioning tofu in the follow-up text must not attach its photo.

**The problem addressed in this iteration.** The current deployment and builder are tied to recipe-oriented configuration, and the graph edges are defined in Python. Those are the architectural limitations the larger plan will address. They are not changed here.

The immediate migration risk is changing several behaviors while replacing the builder: routing, whether search or generation runs, validation timing, which saved photo is displayed, or what survives into the next turn. Existing tests cover many individual components, but a compact set of scenarios through the real builder, nodes, and services gives us a stronger reference for the future JSON-built graph.

**Method and implemented solution.** Added [test_recipe_workflow_baseline.py](../../tests/test_recipe_workflow_baseline.py). It builds the current graph and runs deterministic conversations. Deterministic means that the external answers are fixed, so rerunning a test does not depend on an LLM producing the same wording.

| Part | Used in the new tests |
|---|---|
| Graph construction and execution | Real `RecipeChatBuilder` and `RecipeChatGraph` |
| Analysis, guard, chat, recipe, contact, and response nodes | Real implementations |
| Analyzer, validator, generation, contact, and presentation services | Real implementations |
| Tenant profile and prompt rendering | Real `clinic_001` configuration and templates |
| Model calls | `ScriptedModel`, returning fixed structured results or messages |
| Recipe search | An asynchronous double returning fixed recipe objects |
| Conversation storage | LangGraph `InMemorySaver` |
| FastAPI and React | Covered only by existing applicable tests, not invoked by the new graph scenarios |

The model double fails an unexpected call when no scripted reply is available. There are no model-provider requests, database connections, embedding calls, or image downloads in the new tests. The recipe objects use fixed IDs and timestamps.

**The new scenarios and expected results are:**

| Scenario | Expected behavior |
|---|---|
| Chat greeting | Chat draft then final response; no recipe search or generation |
| Saved recipe | Search once; present only the selected saved recipe and its matching image/source |
| Food with unspecified mode | Default to saved-recipe search |
| Inspired recipe | Search for references, then generate; pass reference content into generation |
| Original recipe | Generate without querying saved recipes |
| No search results | Produce `not_found`; do not invent a saved recipe |
| Contact request | Use the configured dietitian contact URL; do not search or generate recipes |
| Saved recipe, rejection, then follow-up with validator failure | Preserve displayed history, clear stale recipe cards and rejection state, and continue according to the current validator-error behavior |
| Rejection while a branch is running | Start validation and the branch concurrently, cancel the branch on rejection, and skip final presentation |

The final concurrency scenario uses `asyncio.Event` to coordinate the validator and branch. It does not rely on a short sleep to guess when a task has started. A five-second timeout turns a deadlock into a test failure. If the guard were changed to validate completely before starting the branch, this scenario would fail because validation explicitly waits for the branch to start.

The new checks assert result modes, external-call counts, selected recipe IDs and media, final response blocks, and conversation history. They do not assert generated message IDs, exact checkpoint internals, or provider-generated prose. The saved content assertions use the fixed recipe fixtures.

**Existing coverage retained.** The baseline also relies on existing tests rather than duplicating every concern:

| Concern | Existing test file |
|---|---|
| Builder completeness and separate session histories | [test_chat_graph_builder.py](../../tests/test_chat_graph_builder.py) |
| Graph selection through API and CLI | [test_chat_graph_selection.py](../../tests/test_chat_graph_selection.py) |
| Guard acceptance, cancellation, completed-result discard, and validator errors | [test_guard_node.py](../../tests/test_guard_node.py) |
| Per-turn reset of presentation and routing state | [test_graph_presentation.py](../../tests/test_graph_presentation.py) |
| API response blocks and selected recipe images | [test_chat_presentation_api.py](../../tests/test_chat_presentation_api.py) |
| Tenant configuration, separate histories, public config, and image folder ownership | [test_tenancy.py](../../tests/test_tenancy.py) |
| Service contract enforcement and implementation selection | [test_service_factories.py](../../tests/test_service_factories.py) |
| Exact saved content and image selection | [test_recipe_presentation.py](../../tests/test_recipe_presentation.py) and [test_response_node.py](../../tests/test_response_node.py) |

**Verification and observed results.** Run from the project root using the project's virtual environment:

```powershell
# All offline backend tests, including the new baseline scenarios.
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/integration

# Only this iteration's scenarios, when reviewing the new file separately.
.\.venv\Scripts\python.exe -m pytest -q tests/test_recipe_workflow_baseline.py
```

The first command was executed before and after the change. The targeted command is provided for convenient review; its scenarios were executed as part of the full offline run.

| Run | Result |
|---|---|
| Existing offline suite before changes | 166 passed; no failures |
| Offline suite after adding the baseline scenarios | 175 passed; no failures |
| New scenarios included in the second run | 9 passed |

JUnit reports were written to `tmp/workflow-baseline-before.xml` and `tmp/workflow-baseline-after.xml` as local run artifacts. This document and the test source record the baseline independently of those temporary report paths.

The interpreter was `.venv/Scripts/python.exe`, Python 3.12.2, with pytest 9.1.1, FastAPI 0.146.0, and LangGraph 1.2.16. The system Python lacked LangGraph; using the existing project virtual environment resolved that environment mismatch without installing or changing dependencies.

PostgreSQL integration tests were explicitly excluded. This iteration does not claim verification of real database queries, row-level policies, production persistence, provider availability, LLM answer quality, or browser rendering. Existing offline tenancy/API tests verify their mocked or in-memory boundaries. Frontend checks were not run because this iteration changes no frontend code.

**Result and tradeoff.** We now have a passing reference for the current graph, including behavior that must not change accidentally. For example, validator exceptions currently allow processing to continue; the test records that behavior without claiming it is the desired policy for every future workflow. Chat also currently stores an intermediate draft as well as the displayed answer; the existing builder tests expose this behavior, and this iteration does not alter it.

The benefit is that a later JSON compiler can be checked against known conversation outcomes. The limitation is that scripted external responses prove orchestration and presentation behavior, not the quality of live model answers or real search ranking.

Application runtime code, graph topology, deployment selection, database schema, and public API behavior were not changed. Step 1 is complete. The next planned iteration is the generic workflow boundary and recipe adapter from step 2, which remains unimplemented.
