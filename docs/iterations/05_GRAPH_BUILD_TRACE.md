# Iteration 5: graph construction file trace

Date: 2026-10-26.

## Current input

> Create a logger that traces the graph that we are building. And output to a file.
> Also, what is the next change that we need to do to finish the building ?

## Starting situation and problem

The JSON compiler and separate request guard exist. The default deployment still
uses the Python recipe graph and its original internal guard. The offline suite
passed 297 tests.

Application logs went to the console. There was no file showing the state schema,
concrete node implementations, connections, and checkpointer selected for a build.

## Implemented solution

Added [GraphBuildTrace](../../backend/workflows/build_trace.py).
Construction records now append to:

```text
output/graph_build.jsonl
```

Each line is JSON, with UTC time, process ID, build ID, workflow name, source,
phase, and level. Recipe and ingestion builds also include the client ID.

Steps completed:

1. Created a file logger independent of console/application logging.
   A handler opens and closes for each build. A process-wide lock serializes
   writes from concurrent threads; repeated builds do not accumulate handlers.
2. Instrumented [the JSON compiler](../../backend/workflows/compiler.py):
   parsing, selected kinds, validation, derived fields, factories, concrete
   callables, edges, routers, checkpoint type, and completion.
3. Instrumented [the Python recipe graph](../../backend/graphs/recipe_chat_graph.py)
   and [the ingestion graph](../../backend/graphs/recipe_ingestion_graph.py).
4. Instrumented [the JSON recipe binding](../../backend/graphs/compiled_recipe_chat_graph.py)
   before file loading and policy checks. It shares one trace with the compiler,
   so one graph produces one start/completion pair.
5. Recorded failures without replacing the original exceptions.
   Parse failures contain locations/codes. Dependency failures contain the
   validation reason. Factory failures identify the node, kind, and error type.
   Arbitrary factory exception messages and rejected configuration values are not copied.
6. Isolated test traces in temporary files.
7. Generated the application output file using the runnable example and both
   recipe graph implementations with offline service doubles. Recipe node classes
   were assembled without executing their model or database operations.

## Read the file

```powershell
Get-Content output/graph_build.jsonl -Wait
```

For a compact table:

```powershell
Get-Content output/graph_build.jsonl |
    ForEach-Object { $_ | ConvertFrom-Json } |
    Select-Object timestamp, build_id, workflow, source, phase, event, node_id
```

Filter by `build_id` to follow one graph. A compiled wrapper exposes `build_id`
and `build_trace_path`; the recipe/ingestion graph objects expose `build_trace_path`.

| Event | Information |
|---|---|
| `build_started` | New construction attempt |
| `definition_loaded` | Schema version and component counts |
| `node_selected` | JSON ID, kind, and declared reads |
| `validation_succeeded` / `validation_failed` | Structure/dependency result |
| `state_field` | Type and reducer; JSON builds also include input/protection/output flags |
| `node_added` | Actual callable; JSON builds also include input/output contracts |
| `edge_added` | Source and destination |
| `route_added` | Router and conditional destinations |
| `stage_started`, phase `compile` | Checkpointer implementation |
| `graph_compiled` | State schema name |
| `build_succeeded` / `build_failed` | Outcome and elapsed milliseconds |

For example, the generated file records this recipe routing information, plus
timestamp and correlation metadata:

```json
{
  "event": "route_added",
  "workflow": "recipe_chat",
  "from_node": "guard",
  "router": "after_recipe_guard",
  "needs": ["validation"],
  "paths": {"response": "respond", "end": "END"}
}
```

Offline recipe builds record `InMemorySaver`. API builds using the configured
PostgreSQL saver record that type instead. This identifies the adapter; it does
not trace individual database/checkpoint writes.

## Configuration and limits

Choose a destination before starting the application:

```powershell
$env:GRAPH_BUILD_LOG = "output/custom_graph_build.jsonl"
.\.venv\Scripts\python.exe -m backend.workflows.example
```

Relative paths resolve against the project root, independent of the working
directory. Parent directories are created. The existing Git ignore rules already
cover the default output directory.

This is a construction log: it contains metadata and topology, not conversation
values or per-request node execution. It does not serialize services, prompts,
or database credentials. A file-open failure emits an application warning and
allows graph construction to proceed.

The file appends across builds and has no automatic rotation. For multiple worker
processes, use separate destinations or a process-safe sink; the built-in lock
coordinates threads within one process.

## Next recommended change

**Wire the new guard into shared startup and replace internal validation with
ordinary business routing.**

The new `RequestGuard` and `GuardedWorkflow` currently require explicit assembly.
The next iteration should:

1. Resolve a trusted client policy and evaluator during startup. Configure and
   evaluate thresholds explicitly; reuse and close provider clients properly.
2. Have shared bootstrap return `GuardedWorkflow(selected_workflow, guard, ...)`,
   with a bounded, client-scoped history loader, for both API and CLI.
3. Replace the recipe graph's composite validating guard with intent routing:
   `analyze -> chat/recipe/contact -> respond`. The compiler already supports the
   conditional edges needed for this.
4. Update recipe JSON and policy checks to match the new boundary. Ensure public
   startup cannot accidentally bypass the outer guard.
5. Verify one admission decision per request: approved requests run the business
   workflow once; rejected/unavailable requests start no graph and alter no
   checkpoint. Preserve presentation/history and document the intended change
   from the legacy validator-error behavior.

This completes the connection between the guard and the running application.
Concurrency with speculative preparation needs separate work that holds output
and side effects until approval, as explained in [iteration 4](04_REQUEST_GUARD.md).

To finish *configurable graph assembly*, typed JSON service bindings still remain:
JSON should choose allowed dependencies while Python registries enforce constructor
contracts. Today the recipe bootstrap still constructs services and injects node
dependencies in Python. Client/release database storage and client URL routing follow.

Only tracing was implemented here. These next steps are recommendations.

## Pros and tradeoffs

- A real file makes graph assembly reviewable without attaching a debugger.
- Build IDs distinguish repeated/concurrent builds, and names avoid dumping service objects.
- Construction logging does not add per-message state logging.
- File retention and multi-process aggregation need deployment-specific handling.
- A trace describes assembly, not whether model answers or business decisions are correct.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/integration --junitxml=tmp/graph-build-trace.xml
```

**307 tests passed.** New cases cover trace contents, JSON/Python recipe builds,
checkpoint/reducer types, failures, sensitive-value exclusion, concurrent/repeated
builds, path resolution, and an unavailable destination.

The standalone example ran and wrote a real trace. No live model calls or
PostgreSQL integration tests were run.
