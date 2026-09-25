# Alona chat — exact runtime flow

This is the path of one `POST /chat` request. Node names are LangGraph keys. Methods are the real callables.

---

## GraphState fields (shared memory)

Written incrementally. `messages` uses `add_messages` so it **appends**, it does not replace.

| Field | Type | Who writes it | Purpose |
|---|---|---|---|
| `user_id` | `str` | `RecipeChatGraph.arun` | Session id = LangGraph `thread_id` (from cookie `alona_session`) |
| `message` | `str` | `arun` | Latest user text only |
| `analysis` | `MessageAnalysis \| None` | `AnalyzeNode` | Intent + recipe mode + ingredients |
| `validation` | `RequestValidation \| None` | `GuardNode` | `legitimate` plus a user-facing `reason` when the request is rejected. `None` if the checker failed and the flow continued |
| `result` | `dict \| None` | Chat / Recipe / Dietitian, via `GuardNode` | Structured work product for `ResponseNode`. Cleared when the request is rejected |
| `messages` | `list[BaseMessage]` | `arun` + Guard/Response | Rolling conversation (checkpointer persists it) |
| `final_response` | `str \| None` | `GuardNode` on reject, else `ResponseNode` | Complete Markdown for conversation history and older clients |
| `response_blocks` | `list[dict] \| None` | `GuardNode` on reject, else `ResponseNode` | Ordered text and recipe cards; each recipe includes its own saved image |
| `context` | `dict` | unused today | Reserved |

### MessageAnalysis fields

| Field | Values | Why it exists |
|---|---|---|
| `intent` | `chat` / `food` / `dietitian_escort` | **Only** this chooses the branch inside `GuardNode` |
| `recipe_mode` | `existing` / `inspired` / `original` / `none` | Used **inside** `RecipeNode`, not for routing |
| `ingredients` | `list[str]` | Keywords for SQL substring match |
| `preferences` | `list[str]` | Passed into generation prompts |
| `confidence` | `float` | Stored, not used for branching |

---

## Chat graph (runtime)

```mermaid
flowchart TB
  subgraph ENTRY["1. HTTP — api.chat()"]
    UI["Frontend sendChatMessage(message)<br/>credentials: include"]
    COOKIE["resolve_session_id(cookie alona_session)<br/>reject non-UUID → new uuid4"]
    RL["enforce_rate_limits(user_id, client_ip)<br/>15/min user · 40/min IP"]
    ARUN["RecipeChatGraph.arun(message, user_id)<br/>initial_state: user_id, message,<br/>messages=[HumanMessage]<br/>config.thread_id = user_id"]
    UI --> COOKIE --> RL --> ARUN
  end

  subgraph ANALYZE["2. AnalyzeNode.__call__"]
    H["Read messages[-6:] + current message<br/>Why: pronouns like 'עוד רעיון' need history"]
    LLM["MessageAnalyzer.analyze()<br/>llm.with_structured_output(MessageAnalysis).ainvoke"]
    OV["_apply_mode_overrides(current_user_message only)<br/>Why: history often mentions 'אלונה' and would<br/>force existing even when user said NOT Alona"]
    WRITE["Writes state.analysis"]
    H --> LLM --> OV --> WRITE
  end

  ARUN --> ANALYZE

  subgraph GUARD["3. GuardNode.__call__ — parent"]
    FORK["Starts two tasks at once:<br/>RequestValidator.validate(message, history)<br/>and the branch chosen from analysis.intent"]
    VERDICT{"validation.legitimate?"}
    FORK --> VERDICT
    VERDICT -->|false| STOP["cancel the branch<br/>final_response = validation.reason<br/>route_after_guard → END<br/>Why: the branch must not write to the user"]
    VERDICT -->|true or checker error| KEEP["await the branch<br/>write result + validation<br/>route_after_guard → response"]
    I1{"analysis.intent"}
    KEEP --> I1
    I1 -->|chat or None| CHAT
    I1 -->|food| RECIPE
    I1 -->|dietitian_escort| DIET
  end

  ANALYZE --> GUARD

  subgraph CHAT["ChatNode.__call__"]
    C1["llm.ainvoke(system + messages[-10:])<br/>Why: greetings / small talk, NO DB"]
    C2["Writes result = {mode: chat, text}<br/>and appends AIMessage"]
    C1 --> C2
  end

  subgraph RECIPE["RecipeNode.__call__"]
    R0["Guard: analysis is None or intent != food → result None"]
    R1["mode = recipe_mode, but none → existing<br/>Why: a food ask without a mode still means lookup"]
    R2["RecipePlan(ingredients, preferences, mode)"]
    R3{"mode"}
    R0 --> R1 --> R2 --> R3

    R3 -->|existing| EX["find_best_recipes(user_text, plan)"]
    EX -->|hits| EX2["result = {mode: existing, recipes: model_dump}"]
    EX -->|empty| NF["result = {mode: not_found}<br/>Why: do NOT invent an Alona recipe"]

    R3 -->|inspired| IN["find_best_recipes then<br/>generate_inspired_recipe(plan, similar)<br/>Why: new dish, style of Alona, not the stored one"]
    IN --> IN2["result = {mode: inspired, text}"]

    R3 -->|original| OR["generate_original_recipe(plan)<br/>Why: user asked for YOUR idea / not Alona's DB"]
    OR --> OR2["result = {mode: original, text}"]
  end

  subgraph SEARCH["find_best_recipes — why two stages"]
    S1["find_recipe_with_ingredients()<br/>LOWER(name) LIKE %term%<br/>Why: 'אורז' must match 'אורז בסמטי / יסמין'<br/>exact equality used to miss"]
    S2["aembed_query(user_text)  // parallel with S1"]
    S3["find_most_similar_recipes(embedding, candidate_ids or None)<br/>Why: if no ingredient hit, rank ALL recipes"]
    S4["get_many(ids) + get_ingredients(id)<br/>Why: ingredients live in another table"]
    S1 --> S3
    S2 --> S3 --> S4
  end

  EX -.-> SEARCH

  subgraph DIET["DietitianNode.__call__"]
    D1["DietitianService.get_dietitian_url()<br/>reads Url_docs/url_answer_escort.txt"]
    D2["result = {mode: dietitian_escort, text}"]
    D1 --> D2
  end

  subgraph RESP["ResponseNode.__call__"]
    P["Saved recipes: structured recipe ID selection<br/>then canonical cards from database fields.<br/>Other modes: llm.ainvoke for final text"]
    F["Writes final_response + response_blocks<br/>and appends the displayed answer as AIMessage"]
    P --> F
  end

  CHAT --> RESP
  EX2 --> RESP
  NF --> RESP
  IN2 --> RESP
  OR2 --> RESP
  DIET --> RESP

  STOP --> OUT["HTTP { reply: final_response, blocks: response_blocks }"]
  RESP --> OUT
```

---

## Recipe presentation

For saved recipes, `ResponseNode` asks for a `RecipePresentationPlan` containing
intro text, explicit recipe IDs, and optional follow-up text. `build_recipe_response`
selects only those IDs from the search results and copies each recipe's saved name,
ingredients, instructions, source, and photo into one `recipe` block. Mentioning an
alternative in prose does not select its photo. Unknown IDs are rejected.

The frontend renders `blocks` directly in order, independently of the model's
Markdown headings. Text blocks cannot introduce extra photos. Missing photos leave
the rest of their recipe visible. `reply` contains the same selected recipes as
Markdown for history and older clients, with photos beside their respective titles;
the API never appends a gallery of search-result images.

`RecipeChatGraph.arun` clears per-turn analysis, validation, result, and presentation
fields before each invocation. Conversation messages remain available, while stale
recipe cards and rejection state cannot carry into the next response.

## Why routing is intent-only

`recipe_mode` is **not** a graph edge. `GuardNode` picks chat, recipe, or dietitian from `analysis.intent` only, and runs that branch beside the legitimacy check. One `recipe` node reads `analysis.recipe_mode` and picks a method:

- **existing** — user wants a saved Alona recipe (`מתכון של אלונה`, `שמור אצלך`)
- **inspired** — new recipe in her style
- **original** — invent, explicitly not from the DB (`יצירתי`, `לא מהמתכונים של אלונה`, `עוד רעיון`)
- **none** on a food intent is treated as **existing** so “יש לי אורז” still searches

Overrides run on the **latest user turn only**, because conversation history kept the word אלונה and forced lookup.

---

## Ingestion graph (offline)

```mermaid
flowchart LR
  RAW["raw_recipe: str"] --> EXT["ExtractRecipeNode<br/>RecipeExtractorService.extract()<br/>structured RecipeCreate<br/>+ parse instagram_url = line"]
  EXT --> PRE["PrepareEmbeddingNode<br/>name + description + ingredient names + instructions"]
  PRE --> EMB["EmbeddingNode<br/>embed_query(embedding_text)"]
  EMB --> PER["PersistRecipeNode<br/>RecipeIngestionService.save()"]
  PER --> TX["db.transaction()<br/>recipes + ingredients + embeddings<br/>or RecipeAlreadyExists"]
```

`RecipeIngestionState`: `raw_recipe`, `instagram_url`, `recipe`, `embedding_text`, `embedding`, `recipe_id`, `success`, `error`.

---

## Persistence

- **Recipes:** `recipes` / `recipe_ingredients` / `recipe_embeddings` (vector 1536)
- **Chat memory:** LangGraph `AsyncPostgresSaver`, `thread_id = user_id`
- **Why cookie not body `user_id`:** client-supplied ids let anyone join another thread
